from __future__ import annotations

import ast
import logging
import re
import sys
from typing import TYPE_CHECKING, Any, Optional

import orjson
import tiktoken
from hamilton import base
from hamilton.async_driver import AsyncDriver
from haystack import Document
from haystack.components.builders.prompt_builder import PromptBuilder
from langfuse.decorators import observe
from pydantic import BaseModel, Field

from src.core.pipeline import BasicPipeline
from src.core.provider import DocumentStoreProvider, EmbedderProvider, LLMProvider
from src.pipelines.common import (
    build_table_ddl,
    clean_up_new_lines,
    get_engine_supported_data_type,
    normalize_data_type,
)
from src.utils import trace_cost

if TYPE_CHECKING:
    from src.web.v1.services.ask import AskHistory
else:
    AskHistory = Any

logger = logging.getLogger("wren-ai-service")

MAX_RELEVANT_TABLE_CANDIDATES = 8
MIN_TABLE_DESCRIPTION_CANDIDATE_WINDOW = 50


table_columns_selection_system_prompt = """
### TASK ###
You are a highly skilled data analyst. Your goal is to examine the retrieved candidate database schema, interpret the posed question, and identify the specific tables, columns, metrics, views, and relationships required to construct an accurate SQL query.

The retrieved schema is a candidate set, not the full datasource. Select only the objects needed by the question. Objects not shown here are unavailable to this SQL generation request.

### INSTRUCTIONS ###
1. First perform a semantic analysis of the user's request. Identify intended business entities, identifiers, descriptive attributes, metrics, dimensions, filters, aggregations, relationships, time constraints, ranking requirements, and analytical intent such as retrieval, detailed records, summary, comparison, trend analysis, dashboard, KPI, ranking, or record count.
2. Map each business term to explicit schema objects only when the active schema directly supports that term. Distinguish entities such as customer/order/invoice/product from identifiers such as order ID or invoice number, descriptive attributes, and measurable metrics such as amount, quantity, cost, profit, revenue, or duration.
3. Select tables and columns by semantic fit to the full request, not by isolated keyword overlap or commonly used default tables. Return only a subset of the retrieved candidates; never introduce a table or column that is not shown.
4. Include join keys and relationship columns needed to connect selected tables. Do not invent relationships or foreign keys.
5. If the schema does not support a requested entity, metric, dimension, filter, time range, aggregation, or ranking requirement, record it in `missing_requirements`.
6. If multiple schema interpretations are equally plausible and the question does not disambiguate them, record them in `ambiguous_requirements`.
7. Set `is_fully_supported` to false when any required request component is missing or ambiguous.
8. For each selected table, provide a concise reason for why the table is semantically relevant.
9. For each selected column, provide a concise reason for why the column is necessary.
10. Populate `concept_mappings` for every important concept in the request. Each mapping must classify the concept, list only directly supporting schema objects, state whether it must appear in SQL, and include a confidence score between 0 and 1.
11. Populate `interpretations` when the request has more than one plausible schema interpretation. Rank interpretations by semantic relevance, confidence, and schema support; mark the selected interpretation only when it is clearly the best supported one. Keep non-selected high-confidence interpretations so the SQL pipeline can retry the next-best mapping if validation fails.
12. If a "." is included in columns, put the name before the first dot into chosen columns.
13. The number of columns chosen must match the number of reasoning.
14. Final chosen columns must be only column names, don't prefix it with table names.
15. If the chosen column is a child column of a STRUCT type column, choose the parent column instead of the child column.
16. If the schema cannot answer the question, return the closest directly relevant schema objects only if they explain the limitation. Do not select unrelated fallback tables.

### FINAL ANSWER FORMAT ###
Please provide your response as a JSON object, structured as follows:

{
    "semantic_analysis": {
        "analytical_intent": "retrieval | detailed_records | summary | comparison | trend | dashboard | kpi | ranking | record_count | other",
        "entities": ["business entities requested by the user"],
        "identifiers": ["identifier fields requested by the user"],
        "metrics": ["business metrics or measures requested by the user"],
        "dimensions": ["grouping or descriptive dimensions requested by the user"],
        "filters": ["filters or predicates requested by the user"],
        "aggregations": ["aggregation or calculation requirements"],
        "relationships": ["required joins or relationships"],
        "time_constraints": ["time filters, grains, or trend requirements"],
        "ranking": ["top/bottom/order/limit requirements"],
        "supported_schema_objects": ["table.column or metric names that directly support the request"],
        "concept_mappings": [
            {
                "request_concept": "business concept from the user request",
                "concept_type": "entity | identifier | dimension | metric | filter | time | aggregation | ranking | relationship | comparison",
                "schema_objects": ["table.column, table, view, metric, or relationship object that directly supports the concept"],
                "required_in_sql": true,
                "confidence": 0.0,
                "mapping_reason": "Why these schema objects semantically support the concept"
            }
        ],
        "interpretations": [
            {
                "description": "Possible interpretation of the request",
                "schema_objects": ["schema objects used by this interpretation"],
                "confidence": 0.0,
                "is_selected": true
            }
        ],
        "missing_requirements": ["required request components not supported by the schema"],
        "ambiguous_requirements": ["request components with multiple equally plausible schema mappings"],
        "is_fully_supported": true,
        "support_reasoning": "Concise explanation of whether the selected schema fully supports the request"
    },
    "results": [
        {
            "table_selection_reason": "Reason for selecting tablename1",
            "table_contents": {
              "chain_of_thought_reasoning": [
                  "Reason 1 for selecting column1",
                  "Reason 2 for selecting column2",
                  ...
              ],
              "columns": ["column1", "column2", ...]
            },
            "table_name":"tablename1",
        },
        {
            "table_selection_reason": "Reason for selecting tablename2",
            "table_contents":
            {
              "chain_of_thought_reasoning": [
                  "Reason 1 for selecting column1",
                  "Reason 2 for selecting column2",
                  ...
              ],
              "columns": ["column1", "column2", ...]
            },
            "table_name":"tablename2"
        },
        ...
    ]
}

### ADDITIONAL NOTES ###
- Each table key must list only the columns relevant to answering the question.
- Provide a reasoning list (`chain_of_thought_reasoning`) for each table, explaining why each column is necessary.
- Provide the reason of selecting the table in (`table_selection_reason`) for each table.
- Populate `semantic_analysis` before `results`; use it to verify the selected schema directly supports the request.
- Be logical, concise, and ensure the output strictly follows the required JSON format.
- Use table name used in the "Create Table" statement, don't use "alias".
- Match Column names with the definition in the "Create Table" statement.
- Match Table names with the definition in the "Create Table" statement.

Good luck!

"""

table_columns_selection_user_prompt_template = """
### Database Schema ###

{% for db_schema in db_schemas %}
    {{ db_schema }}
{% endfor %}

### INPUT ###
{{ question }}
"""


def _build_metric_ddl(content: dict) -> str:
    columns_ddl = [
        f"{column['comment']}{column['name']} {get_engine_supported_data_type(normalize_data_type(column.get('data_type')))}"
        for column in content["columns"]
        if normalize_data_type(column.get("data_type")).lower()
        != "unknown"  # quick fix: filtering out UNKNOWN column type
    ]

    return (
        f"{content['comment']}CREATE TABLE {content['name']} (\n  "
        + ",\n  ".join(columns_ddl)
        + "\n);"
    )


def _build_view_ddl(content: dict) -> str:
    return (
        f"{content['comment']}CREATE VIEW {content['name']}\nAS {content['statement']}"
    )


## Start of Pipeline
def expand_business_terms_for_retrieval(query: str) -> str:
    return query


def _normalize_retrieval_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _retrieval_terms(value: Any) -> set[str]:
    stop_words = {
        "about",
        "and",
        "are",
        "ask",
        "between",
        "by",
        "chart",
        "compare",
        "distribution",
        "for",
        "from",
        "how",
        "in",
        "is",
        "me",
        "of",
        "show",
        "the",
        "to",
        "what",
        "which",
        "with",
    }
    terms: set[str] = set()
    for raw_token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")):
        split_token = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw_token)
        for token in re.findall(r"[A-Za-z0-9]+", split_token):
            normalized = _normalize_retrieval_token(token)
            if len(normalized) <= 2 and normalized not in {"bu"}:
                continue
            if normalized in stop_words:
                continue
            terms.add(normalized)
            if normalized.endswith("ies") and len(normalized) > 4:
                terms.add(normalized[:-3] + "y")
            elif normalized.endswith("s") and len(normalized) > 3:
                terms.add(normalized[:-1])
    return terms


def _source_text(document: Document) -> str:
    content_text = document.content or ""
    try:
        parsed = ast.literal_eval(content_text)
    except (ValueError, SyntaxError):
        parsed = None

    if isinstance(parsed, dict):
        content_text = " ".join(
            str(part or "")
            for part in (
                parsed.get("name"),
                parsed.get("description"),
                parsed.get("columns"),
                parsed.get("properties"),
            )
        )

    return " ".join(
        str(part or "")
        for part in (
            document.meta.get("name"),
            document.meta.get("description"),
            content_text,
        )
    )


def _query_concept_groups(query: str) -> list[set[str]]:
    query_terms = _retrieval_terms(query)
    concept_specs: list[tuple[set[str], set[str]]] = [
        (
            {"sale", "sales", "revenue"},
            {"sale", "sales", "revenue", "amount", "value", "total", "price"},
        ),
        (
            {"invoice", "invoices", "billing"},
            {"invoice", "invoices", "inv", "billing", "bill"},
        ),
        (
            {"currency", "currencies"},
            {"currency", "currencies", "curr", "ccy", "fx"},
        ),
        (
            {"country", "countries"},
            {"country", "countries", "nation"},
        ),
        (
            {"customer", "customers"},
            {"customer", "customers", "cust", "client", "account"},
        ),
        (
            {"order", "orders"},
            {"order", "orders", "ord", "ordno", "orderid", "purchase"},
        ),
        (
            {"month", "monthly", "year", "quarter", "trend", "date"},
            {"date", "time", "timestamp", "month", "year", "quarter", "period"},
        ),
    ]

    return [aliases for triggers, aliases in concept_specs if query_terms & triggers]


def _semantic_score(document: Document) -> float:
    score = getattr(document, "score", None)
    if isinstance(score, (int, float)):
        return float(score)
    meta_score = document.meta.get("score")
    if isinstance(meta_score, (int, float)):
        return float(meta_score)
    return 0.0


def _score_table_document(query: str, document: Document, index: int) -> tuple[float, int, Document]:
    query_terms = _retrieval_terms(query)
    source_terms = _retrieval_terms(_source_text(document))
    if not query_terms or not source_terms:
        return (_semantic_score(document), -index, document)

    lexical_score = 0
    for query_term in query_terms:
        if query_term in source_terms:
            lexical_score += 20
            continue
        if any(
            query_term in source_term or source_term in query_term
            for source_term in source_terms
        ):
            lexical_score += 6

    concept_groups = _query_concept_groups(query)
    if concept_groups:
        covered = sum(1 for group in concept_groups if group & source_terms)
        lexical_score += 25 * covered
        if covered == len(concept_groups):
            lexical_score += 35
        elif covered == 0:
            lexical_score -= 35

    return (_semantic_score(document) + lexical_score, -index, document)


def _rerank_table_documents(
    query: str,
    documents: list[Document],
    *,
    max_tables: int = MAX_RELEVANT_TABLE_CANDIDATES,
) -> list[Document]:
    if not documents:
        return []

    scored = sorted(
        (
            _score_table_document(query, document, index)
            for index, document in enumerate(documents)
        ),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    selected = [document for _score, _index, document in scored[:max_tables]]
    logger.info(
        "Scoped table-description candidates from %s to %s: %s",
        len(documents),
        len(selected),
        [document.meta.get("name") for document in selected],
    )
    return selected


@observe(capture_input=False, capture_output=False)
async def embedding(
    query: str,
    embedder: Any,
    histories: list[AskHistory],
    tables: Optional[list[str]] = None,
) -> dict:
    if tables:
        logger.info("Skipping embedding retrieval for explicit tables: %s", tables)
        return {}

    if query:
        if histories:
            previous_query_summaries = [history.question for history in histories]
        else:
            previous_query_summaries = []

        query = "\n".join(previous_query_summaries) + "\n" + query
        query = expand_business_terms_for_retrieval(query)

        return await embedder.run(query)
    else:
        return {}


@observe(capture_input=False)
async def table_retrieval(
    query: str,
    embedding: dict,
    project_id: str,
    tables: Optional[list[str]],
    table_retriever: Any,
) -> dict:
    base_filters = {
        "operator": "AND",
        "conditions": [
            {"field": "type", "operator": "==", "value": "TABLE_DESCRIPTION"},
        ],
    }

    if project_id:
        base_filters["conditions"].append(
            {"field": "project_id", "operator": "==", "value": project_id}
        )

    if embedding:
        result = await table_retriever.run(
            query_embedding=embedding.get("embedding"),
            filters=base_filters,
        )
        result["documents"] = _rerank_table_documents(
            query, result.get("documents") or []
        )
        return result

    if not tables:
        return {"documents": []}

    base_filters["conditions"].append(
        {"field": "name", "operator": "in", "value": tables}
    )

    result = await table_retriever.run(
        query_embedding=[],
        filters=base_filters,
    )
    return result


def _extract_table_names_from_table_retrieval(
    table_retrieval: dict, explicit_tables: Optional[list[str]] = None
) -> list[str]:
    table_names: list[str] = []

    def _append(name: Any):
        if isinstance(name, str) and name.strip() and name not in table_names:
            table_names.append(name)

    for table in explicit_tables or []:
        _append(table)

    for document in (table_retrieval or {}).get("documents", []):
        meta = getattr(document, "meta", {}) or {}
        _append(meta.get("name"))

        content = getattr(document, "content", None)
        if isinstance(content, str):
            try:
                parsed = ast.literal_eval(content)
            except (ValueError, SyntaxError):
                parsed = {}
            if isinstance(parsed, dict):
                _append(parsed.get("name"))

    return table_names


@observe(capture_input=False)
async def dbschema_retrieval(
    query: str,
    table_retrieval: dict,
    project_id: str,
    dbschema_retriever: Any,
    tables: Optional[list[str]] = None,
) -> list[Document]:
    filters = {
        "operator": "AND",
        "conditions": [
            {"field": "type", "operator": "==", "value": "TABLE_SCHEMA"},
        ],
    }
    if project_id:
        filters["conditions"].append(
            {"field": "project_id", "operator": "==", "value": project_id}
        )

    selected_table_names = _extract_table_names_from_table_retrieval(
        table_retrieval, tables
    )
    if not selected_table_names:
        logger.info(
            "No relevant tables selected for active project_id %s; skipping schema load",
            project_id,
        )
        return []

    filters["conditions"].append(
        {"field": "name", "operator": "in", "value": selected_table_names}
    )

    logger.info(
        "Loading deployed schema metadata for active project_id %s and tables %s",
        project_id,
        selected_table_names,
    )
    results = await dbschema_retriever.run(query_embedding=[], filters=filters)
    return results.get("documents", [])


@observe()
def construct_db_schemas(dbschema_retrieval: list[Document]) -> list[dict]:
    db_schemas = {}
    for document in dbschema_retrieval:
        content = ast.literal_eval(document.content)
        if content["type"] == "TABLE":
            if document.meta["name"] not in db_schemas:
                db_schemas[document.meta["name"]] = content
            else:
                db_schemas[document.meta["name"]] = {
                    **content,
                    "columns": db_schemas[document.meta["name"]].get("columns", []),
                }
        elif content["type"] == "TABLE_COLUMNS":
            if document.meta["name"] not in db_schemas:
                db_schemas[document.meta["name"]] = {"columns": content["columns"]}
            else:
                if "columns" not in db_schemas[document.meta["name"]]:
                    db_schemas[document.meta["name"]]["columns"] = content["columns"]
                else:
                    db_schemas[document.meta["name"]]["columns"] += content["columns"]

    # remove incomplete schemas
    db_schemas = {k: v for k, v in db_schemas.items() if "type" in v and "columns" in v}

    return list(db_schemas.values())

_FOREIGN_KEY_CONSTRAINT_PATTERN = re.compile(
    r"FOREIGN\s+KEY\s*\(\s*(?P<column>[^)]+?)\s*\)\s*"
    r"REFERENCES\s+(?P<table>[^\s(]+)\s*\(\s*(?P<referenced_column>[^)]+?)\s*\)",
    flags=re.IGNORECASE,
)


def _normalize_schema_identifier(value: Any) -> str:
    return str(value or "").strip().strip('"[]').replace(chr(96), "").lower()


def _schema_table_names_match(left: Any, right: Any) -> bool:
    left_name = _normalize_schema_identifier(left)
    right_name = _normalize_schema_identifier(right)
    if not left_name or not right_name:
        return False
    return left_name == right_name or left_name.rsplit(".", 1)[-1] == right_name.rsplit(
        ".", 1
    )[-1]


def _find_selected_schema_table(
    table_name: Any, selected_tables: set[str]
) -> str | None:
    for selected_table in selected_tables:
        if _schema_table_names_match(table_name, selected_table):
            return selected_table
    return None


def _parse_foreign_key_constraint(column: dict) -> tuple[str, str, str] | None:
    constraint = str(column.get("constraint") or "")
    match = _FOREIGN_KEY_CONSTRAINT_PATTERN.search(constraint)
    if not match:
        return None

    local_column = str(match.group("column")).strip().strip('"[]').replace(chr(96), "")
    referenced_table = str(match.group("table")).strip().strip('"[]').replace(chr(96), "")
    referenced_column = str(match.group("referenced_column")).strip().strip('"[]').replace(chr(96), "")
    if not local_column or not referenced_table or not referenced_column:
        return None
    return local_column, referenced_table, referenced_column


def _selected_columns_from_retrieval(
    columns_and_tables_needed: list[dict], construct_db_schemas: list[dict]
) -> dict[str, set[str]]:
    selected_by_name: dict[str, dict] = {}
    for selected_table in columns_and_tables_needed:
        if not isinstance(selected_table, dict):
            continue
        table_name = _normalize_schema_identifier(selected_table.get("table_name"))
        table_contents = selected_table.get("table_contents")
        if table_name and isinstance(table_contents, dict):
            selected_by_name[table_name] = table_contents

    selected_columns: dict[str, set[str]] = {}
    for table_schema in construct_db_schemas:
        table_name = str(table_schema.get("name") or "").strip()
        table_contents = selected_by_name.get(_normalize_schema_identifier(table_name))
        if not table_name or not table_contents:
            continue

        available_columns = {
            _normalize_schema_identifier(column.get("name")): str(column.get("name"))
            for column in table_schema.get("columns", [])
            if isinstance(column, dict)
            and column.get("type") == "COLUMN"
            and str(column.get("name") or "").strip()
        }
        requested_columns = table_contents.get("columns")
        if not isinstance(requested_columns, list):
            continue

        columns = {
            available_columns[_normalize_schema_identifier(column)]
            for column in requested_columns
            if _normalize_schema_identifier(column) in available_columns
        }
        if columns:
            selected_columns[table_name] = columns

    return selected_columns


def _include_selected_relationship_columns(
    selected_columns: dict[str, set[str]], construct_db_schemas: list[dict]
) -> None:
    selected_tables = set(selected_columns)
    schema_by_name = {
        str(table_schema.get("name") or "").strip(): table_schema
        for table_schema in construct_db_schemas
        if str(table_schema.get("name") or "").strip()
    }

    for table_name, table_schema in schema_by_name.items():
        if table_name not in selected_tables:
            continue

        for column in table_schema.get("columns", []):
            if not isinstance(column, dict) or column.get("type") != "FOREIGN_KEY":
                continue
            relationship_tables = column.get("tables")
            if not isinstance(relationship_tables, list) or not all(
                _find_selected_schema_table(relationship_table, selected_tables)
                for relationship_table in relationship_tables
            ):
                continue

            relationship = _parse_foreign_key_constraint(column)
            if not relationship:
                continue
            local_column, referenced_table, referenced_column = relationship
            selected_columns[table_name].add(local_column)

            referenced_schema_table = _find_selected_schema_table(
                referenced_table, selected_tables
            )
            if referenced_schema_table:
                selected_columns[referenced_schema_table].add(referenced_column)



@observe(capture_input=False)
def check_using_db_schemas_without_pruning(
    construct_db_schemas: list[dict],
    dbschema_retrieval: list[Document],
    encoding: tiktoken.Encoding,
    enable_column_pruning: bool,
    context_window_size: int,
    tables: Optional[list[str]] = None,
) -> dict:
    retrieval_results = []
    has_calculated_field = False
    has_metric = False
    has_json_field = False

    for table_schema in construct_db_schemas:
        if table_schema["type"] == "TABLE":
            ddl, _has_calculated_field, _has_json_field = build_table_ddl(table_schema)
            retrieval_results.append(
                {
                    "table_name": table_schema["name"],
                    "table_ddl": ddl,
                }
            )
            if _has_calculated_field:
                has_calculated_field = True
            if _has_json_field:
                has_json_field = True

    for document in dbschema_retrieval:
        content = ast.literal_eval(document.content)

        if content["type"] == "METRIC":
            retrieval_results.append(
                {
                    "table_name": content["name"],
                    "table_ddl": _build_metric_ddl(content),
                }
            )
            has_metric = True
        elif content["type"] == "VIEW":
            retrieval_results.append(
                {
                    "table_name": content["name"],
                    "table_ddl": _build_view_ddl(content),
                }
            )

    table_ddls = [
        retrieval_result["table_ddl"] for retrieval_result in retrieval_results
    ]
    _token_count = len(encoding.encode(" ".join(table_ddls)))
    has_explicit_tables = bool([table for table in tables or [] if table])
    if (enable_column_pruning and not has_explicit_tables) or (
        _token_count > context_window_size
    ):
        return {
            "db_schemas": [],
            "tokens": _token_count,
            "has_calculated_field": has_calculated_field,
            "has_metric": has_metric,
            "has_json_field": has_json_field,
            "semantic_analysis": {},
        }


    return {
        "db_schemas": retrieval_results,
        "tokens": _token_count,
        "has_calculated_field": has_calculated_field,
        "has_metric": has_metric,
        "has_json_field": has_json_field,
        "semantic_analysis": {},
    }


@observe(capture_input=False)
def prompt(
    query: str,
    construct_db_schemas: list[dict],
    prompt_builder: PromptBuilder,
    check_using_db_schemas_without_pruning: dict,
    histories: list[AskHistory],
) -> dict:
    if not check_using_db_schemas_without_pruning["db_schemas"]:
        db_schemas = [
            build_table_ddl(construct_db_schema)[0]
            for construct_db_schema in construct_db_schemas
        ]

        previous_query_summaries = (
            [history.question for history in histories] if histories else []
        )

        query = "\n".join(previous_query_summaries) + "\n" + query

        _prompt = prompt_builder.run(question=query, db_schemas=db_schemas)
        return {"prompt": clean_up_new_lines(_prompt.get("prompt"))}
    else:
        return {}


@observe(as_type="generation", capture_input=False)
@trace_cost
async def filter_columns_in_tables(
    prompt: dict, table_columns_selection_generator: Any, generator_name: str
) -> dict:
    if prompt:
        return await table_columns_selection_generator(
            prompt=prompt.get("prompt")
        ), generator_name
    else:
        return {}, generator_name


def _retrieval_schema_text(retrieval_results: list[dict]) -> str:
    return "\n".join(
        " ".join(
            str(part or "")
            for part in (
                result.get("table_name"),
                result.get("table_ddl"),
            )
        )
        for result in retrieval_results
        if isinstance(result, dict)
    )


def _query_mentions_pattern(query: str, pattern: str) -> bool:
    return bool(re.search(pattern, query or "", flags=re.IGNORECASE))


def _apply_deterministic_schema_support_guard(
    query: str,
    semantic_analysis: dict[str, Any],
    retrieval_results: list[dict],
) -> dict[str, Any]:
    if not retrieval_results:
        return semantic_analysis if isinstance(semantic_analysis, dict) else {}

    guarded_analysis = dict(semantic_analysis or {})
    schema_text = _retrieval_schema_text(retrieval_results)
    schema_terms = _retrieval_terms(schema_text)
    missing_requirements = list(guarded_analysis.get("missing_requirements") or [])

    def add_missing(requirement: str) -> None:
        if requirement not in missing_requirements:
            missing_requirements.append(requirement)

    def schema_has_any(terms: set[str]) -> bool:
        return bool(schema_terms & terms)

    normalized_query = re.sub(r"\s+", " ", (query or "").strip().lower())
    asks_sales_person = bool(
        re.search(r"\bsales\s*person\b|\bsalesperson\b", normalized_query)
    )
    if (
        not asks_sales_person
        and _query_mentions_pattern(query, r"\b(?:sales?|revenue)\b")
        and not schema_has_any(
            {
                "sale",
                "sales",
                "revenue",
                "amount",
                "value",
                "total",
                "price",
                "netamount",
                "grossamount",
            }
        )
    ):
        add_missing("sales or revenue metric")

    if _query_mentions_pattern(
        query, r"\b(?:invoice|invoices|billing)\b"
    ) and not schema_has_any(
        {"invoice", "invoices", "inv", "billing", "bill"}
    ):
        add_missing("invoice data")

    if _query_mentions_pattern(
        query, r"\b(?:currency|currencies)\b"
    ) and not schema_has_any(
        {"currency", "currencies", "curr", "ccy", "fx"}
    ):
        add_missing("currency dimension")

    if _query_mentions_pattern(
        query, r"\b(?:country|countries)\b"
    ) and not schema_has_any(
        {"country", "countries", "countrycode", "countryname"}
    ):
        add_missing("country dimension")

    if missing_requirements:
        guarded_analysis["missing_requirements"] = missing_requirements
        guarded_analysis["is_fully_supported"] = False
        guarded_analysis.setdefault(
            "support_reasoning",
            "Retrieved schema candidates do not expose every required concept in the question.",
        )

    return guarded_analysis


@observe()
def construct_retrieval_results(
    check_using_db_schemas_without_pruning: dict,
    filter_columns_in_tables: dict,
    construct_db_schemas: list[dict],
    dbschema_retrieval: list[Document],
    query: str = "",
) -> dict[str, Any]:
    if filter_columns_in_tables:
        try:
            retrieval_payload = orjson.loads(filter_columns_in_tables["replies"][0])
        except (KeyError, IndexError, TypeError, orjson.JSONDecodeError):
            logger.warning("Column selection did not return a valid retrieval payload")
            retrieval_payload = {}

        columns_and_tables_needed = retrieval_payload.get("results", [])
        if not isinstance(columns_and_tables_needed, list):
            columns_and_tables_needed = []
        semantic_analysis = retrieval_payload.get("semantic_analysis") or {}

        selected_columns = _selected_columns_from_retrieval(
            columns_and_tables_needed, construct_db_schemas
        )
        _include_selected_relationship_columns(selected_columns, construct_db_schemas)
        tables = set(selected_columns)
        retrieval_results = []
        has_calculated_field = False
        has_metric = False
        has_json_field = False

        for table_schema in construct_db_schemas:
            if table_schema["type"] == "TABLE" and table_schema["name"] in tables:
                ddl, _has_calculated_field, _has_json_field = build_table_ddl(
                    table_schema,
                    columns=selected_columns[table_schema["name"]],
                    tables=tables,
                )
                if _has_calculated_field:
                    has_calculated_field = True
                if _has_json_field:
                    has_json_field = True

                retrieval_results.append(
                    {
                        "table_name": table_schema["name"],
                        "table_ddl": ddl,
                    }
                )

        for document in dbschema_retrieval:
            if document.meta["name"] in tables:
                content = ast.literal_eval(document.content)

                if content["type"] == "METRIC":
                    retrieval_results.append(
                        {
                            "table_name": content["name"],
                            "table_ddl": _build_metric_ddl(content),
                        }
                    )
                    has_metric = True
                elif content["type"] == "VIEW":
                    retrieval_results.append(
                        {
                            "table_name": content["name"],
                            "table_ddl": _build_view_ddl(content),
                        }
                    )

        semantic_analysis = _apply_deterministic_schema_support_guard(
            query, semantic_analysis, retrieval_results
        )

        return {
            "retrieval_results": retrieval_results,
            "has_calculated_field": has_calculated_field,
            "has_metric": has_metric,
            "has_json_field": has_json_field,
            "semantic_analysis": semantic_analysis,
        }
    else:
        retrieval_results = check_using_db_schemas_without_pruning["db_schemas"]
        semantic_analysis = _apply_deterministic_schema_support_guard(
            query,
            check_using_db_schemas_without_pruning.get("semantic_analysis", {}),
            retrieval_results,
        )

        return {
            "retrieval_results": retrieval_results,
            "has_calculated_field": check_using_db_schemas_without_pruning[
                "has_calculated_field"
            ],
            "has_metric": check_using_db_schemas_without_pruning["has_metric"],
            "has_json_field": check_using_db_schemas_without_pruning["has_json_field"],
            "semantic_analysis": semantic_analysis,
        }


## End of Pipeline
class MatchingTableContents(BaseModel):
    chain_of_thought_reasoning: list[str]
    columns: list[str]


class MatchingTable(BaseModel):
    table_name: str
    table_contents: MatchingTableContents
    table_selection_reason: str


class SemanticConceptMapping(BaseModel):
    request_concept: str = ""
    concept_type: str = ""
    schema_objects: list[str] = Field(default_factory=list)
    required_in_sql: bool = True
    confidence: float | None = None
    mapping_reason: str = ""


class SemanticInterpretation(BaseModel):
    description: str = ""
    schema_objects: list[str] = Field(default_factory=list)
    confidence: float | None = None
    is_selected: bool = False


class SemanticAnalysis(BaseModel):
    analytical_intent: str = ""
    entities: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    aggregations: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    time_constraints: list[str] = Field(default_factory=list)
    ranking: list[str] = Field(default_factory=list)
    supported_schema_objects: list[str] = Field(default_factory=list)
    concept_mappings: list[SemanticConceptMapping] = Field(default_factory=list)
    interpretations: list[SemanticInterpretation] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    ambiguous_requirements: list[str] = Field(default_factory=list)
    is_fully_supported: bool | None = None
    support_reasoning: str = ""


class RetrievalResults(BaseModel):
    semantic_analysis: SemanticAnalysis | None = None
    results: list[MatchingTable]


RETRIEVAL_MODEL_KWARGS = {
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "retrieval_schema",
            "schema": RetrievalResults.model_json_schema(),
        },
    }
}


class DbSchemaRetrieval(BasicPipeline):
    def __init__(
        self,
        llm_provider: LLMProvider,
        embedder_provider: EmbedderProvider,
        document_store_provider: DocumentStoreProvider,
        table_retrieval_size: int = 10,
        table_column_retrieval_size: int = 100,
        **kwargs,
    ):
        table_description_candidate_window = max(
            table_retrieval_size,
            MIN_TABLE_DESCRIPTION_CANDIDATE_WINDOW,
        )
        self._components = {
            "embedder": embedder_provider.get_text_embedder(),
            "table_retriever": document_store_provider.get_retriever(
                document_store_provider.get_store(dataset_name="table_descriptions"),
                top_k=table_description_candidate_window,
            ),
            "dbschema_retriever": document_store_provider.get_retriever(
                document_store_provider.get_store(),
                top_k=table_column_retrieval_size,
            ),
            "table_columns_selection_generator": llm_provider.get_generator(
                system_prompt=table_columns_selection_system_prompt,
                generation_kwargs=RETRIEVAL_MODEL_KWARGS,
            ),
            "generator_name": llm_provider.get_model(),
            "prompt_builder": PromptBuilder(
                template=table_columns_selection_user_prompt_template
            ),
        }

        # for the first time, we need to load the encodings
        _model = llm_provider.get_model()
        if "gpt-4o" in _model or "gpt-4o-mini" in _model:
            _encoding = tiktoken.get_encoding("o200k_base")
        else:
            _encoding = tiktoken.get_encoding("cl100k_base")

        self._configs = {
            "encoding": _encoding,
            "context_window_size": llm_provider.get_context_window_size(),
        }

        super().__init__(
            AsyncDriver({}, sys.modules[__name__], result_builder=base.DictResult())
        )

    @observe(name="Ask Retrieval")
    async def run(
        self,
        query: str = "",
        tables: Optional[list[str]] = None,
        project_id: Optional[str] = None,
        histories: Optional[list[AskHistory]] = None,
        enable_column_pruning: bool = False,
    ):
        logger.debug("Ask Retrieval pipeline is running...")
        return await self._pipe.execute(
            ["construct_retrieval_results"],
            inputs={
                "query": query,
                "tables": tables,
                "project_id": project_id or "",
                "histories": histories or [],
                "enable_column_pruning": enable_column_pruning,
                **self._components,
                **self._configs,
            },
        )
