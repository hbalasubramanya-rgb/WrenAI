import pytest
import tiktoken
from haystack import Document

from src.pipelines.retrieval.db_schema_retrieval import (
    construct_retrieval_results,
    check_using_db_schemas_without_pruning,
    dbschema_retrieval,
    expand_business_terms_for_retrieval,
    table_retrieval,
)


def test_expand_business_terms_for_retrieval_does_not_add_datasource_specific_aliases():
    query = "Create a SalesPerson performance ranking chart"

    assert expand_business_terms_for_retrieval(query) == query


def test_expand_business_terms_for_retrieval_leaves_query_unchanged():
    query = "Explain what this workspace does"

    assert expand_business_terms_for_retrieval(query) == query


@pytest.mark.asyncio
async def test_dbschema_retrieval_loads_only_selected_active_project_tables():
    class Retriever:
        def __init__(self):
            self.filters = None

        async def run(self, query_embedding, filters):
            self.filters = filters
            return {
                "documents": [
                    Document(
                        content=str(
                            {
                                "type": "TABLE",
                                "name": "orders",
                                "columns": [],
                            }
                        ),
                        meta={"type": "TABLE_SCHEMA", "name": "orders"},
                    ),
                    Document(
                        content=str(
                            {
                                "type": "TABLE",
                                "name": "customers",
                                "columns": [],
                            }
                        ),
                        meta={"type": "TABLE_SCHEMA", "name": "customers"},
                    ),
                ]
            }

    retriever = Retriever()

    documents = await dbschema_retrieval(
        query="total orders",
        table_retrieval={
            "documents": [
                Document(
                    content=str({"name": "orders"}),
                    meta={"type": "TABLE_DESCRIPTION", "name": "orders"},
                )
            ]
        },
        project_id="project-1",
        dbschema_retriever=retriever,
    )

    assert [document.meta["name"] for document in documents] == ["orders", "customers"]
    assert retriever.filters == {
        "operator": "AND",
        "conditions": [
            {"field": "type", "operator": "==", "value": "TABLE_SCHEMA"},
            {"field": "project_id", "operator": "==", "value": "project-1"},
            {"field": "name", "operator": "in", "value": ["orders"]},
        ],
    }


@pytest.mark.asyncio
async def test_dbschema_retrieval_uses_explicit_tables_without_embedding_results():
    class Retriever:
        def __init__(self):
            self.filters = None

        async def run(self, query_embedding, filters):
            self.filters = filters
            return {
                "documents": [
                    Document(
                        content=str(
                            {
                                "type": "TABLE",
                                "name": "dbo_failure_patterns",
                                "columns": [],
                            }
                        ),
                        meta={
                            "type": "TABLE_SCHEMA",
                            "name": "dbo_failure_patterns",
                        },
                    )
                ]
            }

    retriever = Retriever()

    documents = await dbschema_retrieval(
        query="show counts",
        table_retrieval={"documents": []},
        project_id="project-1",
        dbschema_retriever=retriever,
        tables=["dbo_failure_patterns"],
    )

    assert [document.meta["name"] for document in documents] == [
        "dbo_failure_patterns"
    ]
    assert retriever.filters == {
        "operator": "AND",
        "conditions": [
            {"field": "type", "operator": "==", "value": "TABLE_SCHEMA"},
            {"field": "project_id", "operator": "==", "value": "project-1"},
            {
                "field": "name",
                "operator": "in",
                "value": ["dbo_failure_patterns"],
            },
        ],
    }


@pytest.mark.asyncio
async def test_table_retrieval_skips_vector_lookup_for_explicit_tables():
    class Retriever:
        async def run(self, query_embedding, filters):
            raise AssertionError("explicit table lookup must not call vector retrieval")

    result = await table_retrieval(
        query="Show monthly record count by LiquidationDate in dbo.ytblTariffsRec",
        embedding={},
        project_id="project-1",
        tables=["dbo.ytblTariffsRec", "dbo_ytblTariffsRec", "ytblTariffsRec"],
        table_retriever=Retriever(),
    )

    assert [document.meta["name"] for document in result["documents"]] == [
        "dbo.ytblTariffsRec",
        "dbo_ytblTariffsRec",
        "ytblTariffsRec",
    ]


def test_construct_retrieval_results_preserves_semantic_analysis():
    result = construct_retrieval_results(
        check_using_db_schemas_without_pruning={"db_schemas": []},
        filter_columns_in_tables={
            "replies": [
                """
                {
                  "semantic_analysis": {
                    "analytical_intent": "summary",
                    "entities": ["invoice"],
                    "metrics": ["invoice amount"],
                    "dimensions": ["customer"],
                    "concept_mappings": [
                      {
                        "request_concept": "invoice amount",
                        "concept_type": "metric",
                        "schema_objects": ["invoices.invoice_amount"],
                        "required_in_sql": true,
                        "confidence": 0.95,
                        "mapping_reason": "invoice_amount stores invoice value"
                      }
                    ],
                    "interpretations": [
                      {
                        "description": "Summarize invoice amount by customer",
                        "schema_objects": ["invoices.customer_id", "invoices.invoice_amount"],
                        "confidence": 0.9,
                        "is_selected": true
                      }
                    ],
                    "is_fully_supported": true
                  },
                  "results": [
                    {
                      "table_name": "invoices",
                      "table_selection_reason": "Contains invoice facts.",
                      "table_contents": {
                        "chain_of_thought_reasoning": [
                          "Needed to group by customer.",
                          "Needed to sum invoice amount."
                        ],
                        "columns": ["customer_id", "invoice_amount"]
                      }
                    }
                  ]
                }
                """
            ]
        },
        construct_db_schemas=[
            {
                "type": "TABLE",
                "name": "invoices",
                "comment": "",
                "columns": [
                    {
                        "type": "COLUMN",
                        "name": "customer_id",
                        "data_type": "varchar",
                        "comment": "",
                        "is_primary_key": False,
                    },
                    {
                        "type": "COLUMN",
                        "name": "invoice_amount",
                        "data_type": "double",
                        "comment": "",
                        "is_primary_key": False,
                    },
                    {
                        "type": "COLUMN",
                        "name": "internal_note",
                        "data_type": "varchar",
                        "comment": "",
                        "is_primary_key": False,
                    },
                ],
            }
        ],
        dbschema_retrieval=[],
    )

    assert result["semantic_analysis"]["metrics"] == ["invoice amount"]
    assert result["semantic_analysis"]["concept_mappings"][0]["schema_objects"] == [
        "invoices.invoice_amount"
    ]
    assert result["semantic_analysis"]["interpretations"][0]["is_selected"] is True
    assert result["retrieval_results"][0]["table_name"] == "invoices"
    assert "invoice_amount" in result["retrieval_results"][0]["table_ddl"]
    assert "internal_note" not in result["retrieval_results"][0]["table_ddl"]


def test_explicit_table_retrieval_uses_full_schema_when_it_fits_context():
    result = check_using_db_schemas_without_pruning(
        construct_db_schemas=[
            {
                "type": "TABLE",
                "name": "dbo_tblNewOrders",
                "comment": "",
                "columns": [
                    {
                        "type": "COLUMN",
                        "name": "CustName",
                        "data_type": "varchar",
                        "comment": "",
                        "is_primary_key": False,
                    }
                ],
            }
        ],
        dbschema_retrieval=[],
        encoding=tiktoken.get_encoding("cl100k_base"),
        enable_column_pruning=True,
        context_window_size=8000,
        tables=["dbo_tblNewOrders"],
    )

    assert result["db_schemas"][0]["table_name"] == "dbo_tblNewOrders"
    assert "CustName" in result["db_schemas"][0]["table_ddl"]


def test_construct_retrieval_results_marks_missing_sales_country_concepts():
    result = construct_retrieval_results(
        check_using_db_schemas_without_pruning={
            "db_schemas": [
                {
                    "table_name": "dbo_ytblTariffsFullA",
                    "table_ddl": """
                    CREATE TABLE dbo_ytblTariffsFullA (
                      TariffCode VARCHAR,
                      LiquidationDate DATE
                    );
                    """,
                }
            ],
            "has_calculated_field": False,
            "has_metric": False,
            "has_json_field": False,
            "semantic_analysis": {},
        },
        filter_columns_in_tables={},
        construct_db_schemas=[],
        dbschema_retrieval=[],
        query="compare sales between countries",
    )

    assert result["semantic_analysis"]["is_fully_supported"] is False
    assert "sales or revenue metric" in result["semantic_analysis"]["missing_requirements"]
    assert "country dimension" in result["semantic_analysis"]["missing_requirements"]


def test_construct_retrieval_results_accepts_compound_sales_country_columns():
    result = construct_retrieval_results(
        check_using_db_schemas_without_pruning={
            "db_schemas": [
                {
                    "table_name": "dbo_tblSales",
                    "table_ddl": """
                    CREATE TABLE dbo_tblSales (
                      CountryCode VARCHAR,
                      SalesValue DECIMAL
                    );
                    """,
                }
            ],
            "has_calculated_field": False,
            "has_metric": False,
            "has_json_field": False,
            "semantic_analysis": {},
        },
        filter_columns_in_tables={},
        construct_db_schemas=[],
        dbschema_retrieval=[],
        query="compare sales between countries",
    )

    assert result["semantic_analysis"] == {}
