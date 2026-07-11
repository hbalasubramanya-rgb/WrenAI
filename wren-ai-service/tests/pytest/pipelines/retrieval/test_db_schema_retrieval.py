import pytest
from haystack import Document

from src.pipelines.retrieval.db_schema_retrieval import (
    _is_project_wide_analysis_query,
    dbschema_retrieval,
    expand_business_terms_for_retrieval,
    rank_semantic_schema_candidates,
)


def test_project_wide_analysis_query_includes_broad_ranking_questions():
    assert _is_project_wide_analysis_query(
        "Which projects have the highest number of completed questions?"
    )


def test_project_wide_analysis_query_ignores_empty_query():
    assert not _is_project_wide_analysis_query("")


def test_expand_business_terms_for_retrieval_does_not_add_datasource_specific_aliases():
    query = "Create a SalesPerson performance ranking chart"

    assert expand_business_terms_for_retrieval(query) == query


def test_expand_business_terms_for_retrieval_leaves_query_unchanged():
    query = "Explain what this workspace does"

    assert expand_business_terms_for_retrieval(query) == query


def test_rank_semantic_schema_candidates_prefers_complete_business_concept_coverage():
    schemas = [
        {
            "type": "TABLE",
            "name": "dbo_invoices",
            "columns": [
                {"name": "invoice_amount", "data_type": "decimal"},
                {"name": "customer_name", "data_type": "varchar"},
            ],
        },
        {
            "type": "TABLE",
            "name": "dbo_invoice_ids",
            "columns": [
                {"name": "invoice_id", "data_type": "varchar"},
            ],
        },
    ]

    candidates = rank_semantic_schema_candidates(
        "Show top customers by invoice amount",
        schemas,
    )

    assert candidates[0]["table_name"] == "dbo_invoices"
    assert "customer" in candidates[0]["matched_query_terms"]
    assert "invoice" in candidates[0]["matched_query_terms"]
    assert "amount" in candidates[0]["matched_query_terms"]


def test_rank_semantic_schema_candidates_uses_retry_rejections_as_negative_feedback():
    schemas = [
        {
            "type": "TABLE",
            "name": "dbo_invoices",
            "columns": [
                {"name": "invoice_amount", "data_type": "decimal"},
                {"name": "customer_name", "data_type": "varchar"},
            ],
        },
        {
            "type": "TABLE",
            "name": "dbo_invoice_summary",
            "columns": [
                {"name": "total_invoice_amount", "data_type": "decimal"},
                {"name": "customer_name", "data_type": "varchar"},
            ],
        },
    ]

    candidates = rank_semantic_schema_candidates(
        "Show top customers by invoice amount",
        schemas,
        semantic_retry_context={
            "rejected_schema_objects": [
                "dbo_invoices",
                "dbo_invoices.invoice_amount",
            ]
        },
    )

    assert candidates[0]["table_name"] == "dbo_invoice_summary"
    assert candidates[0]["rejected_by_retry"] is False


@pytest.mark.asyncio
async def test_dbschema_retrieval_loads_only_selected_active_project_schema():
    class Retriever:
        def __init__(self):
            self.filters = None
            self.documents = [
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

        async def run(self, query_embedding, filters):
            self.filters = filters
            requested_names = {
                condition["value"]
                for condition in filters["conditions"][-1]["conditions"]
            }
            return {
                "documents": [
                    document
                    for document in self.documents
                    if document.meta["name"] in requested_names
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

    assert [document.meta["name"] for document in documents] == ["orders"]
    assert retriever.filters == {
        "operator": "AND",
        "conditions": [
            {"field": "type", "operator": "==", "value": "TABLE_SCHEMA"},
            {"field": "project_id", "operator": "==", "value": "project-1"},
            {
                "operator": "OR",
                "conditions": [
                    {"field": "name", "operator": "==", "value": "orders"},
                ],
            },
        ],
    }


@pytest.mark.asyncio
async def test_dbschema_retrieval_does_not_load_full_schema_for_unmatched_query():
    class Retriever:
        def __init__(self):
            self.called = False

        async def run(self, query_embedding, filters):
            self.called = True
            return {"documents": []}

    retriever = Retriever()

    documents = await dbschema_retrieval(
        query="total orders",
        table_retrieval={"documents": []},
        project_id="project-1",
        dbschema_retriever=retriever,
    )

    assert documents == []
    assert retriever.called is False


@pytest.mark.asyncio
async def test_dbschema_retrieval_loads_all_selected_related_tables_only():
    class Retriever:
        def __init__(self):
            self.filters = None
            self.documents = [
                Document(
                    content=str(
                        {
                            "type": "TABLE",
                            "name": "orders",
                            "columns": [
                                {"name": "customer_id", "data_type": "integer"},
                            ],
                        }
                    ),
                    meta={"type": "TABLE_SCHEMA", "name": "orders"},
                ),
                Document(
                    content=str(
                        {
                            "type": "TABLE",
                            "name": "customers",
                            "columns": [
                                {"name": "id", "data_type": "integer"},
                                {"name": "name", "data_type": "varchar"},
                            ],
                        }
                    ),
                    meta={"type": "TABLE_SCHEMA", "name": "customers"},
                ),
                Document(
                    content=str(
                        {
                            "type": "TABLE",
                            "name": "products",
                            "columns": [],
                        }
                    ),
                    meta={"type": "TABLE_SCHEMA", "name": "products"},
                ),
            ]

        async def run(self, query_embedding, filters):
            self.filters = filters
            requested_names = {
                condition["value"]
                for condition in filters["conditions"][-1]["conditions"]
            }
            return {
                "documents": [
                    document
                    for document in self.documents
                    if document.meta["name"] in requested_names
                ]
            }

    retriever = Retriever()

    documents = await dbschema_retrieval(
        query="show orders by customer",
        table_retrieval={
            "documents": [
                Document(
                    content=str({"name": "orders"}),
                    meta={"type": "TABLE_DESCRIPTION", "name": "orders"},
                ),
                Document(
                    content=str({"name": "customers"}),
                    meta={"type": "TABLE_DESCRIPTION", "name": "customers"},
                ),
            ]
        },
        project_id="project-1",
        dbschema_retriever=retriever,
    )

    assert [document.meta["name"] for document in documents] == [
        "orders",
        "customers",
    ]
    assert retriever.filters["conditions"][-1] == {
        "operator": "OR",
        "conditions": [
            {"field": "name", "operator": "==", "value": "orders"},
            {"field": "name", "operator": "==", "value": "customers"},
        ],
    }


@pytest.mark.asyncio
async def test_dbschema_retrieval_keeps_complete_schema_for_metadata_request():
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
        query="",
        table_retrieval={"documents": []},
        project_id="project-1",
        dbschema_retriever=retriever,
    )

    assert [document.meta["name"] for document in documents] == [
        "orders",
        "customers",
    ]
    assert retriever.filters == {
        "operator": "AND",
        "conditions": [
            {"field": "type", "operator": "==", "value": "TABLE_SCHEMA"},
            {"field": "project_id", "operator": "==", "value": "project-1"},
        ],
    }
