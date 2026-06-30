from src.pipelines.retrieval.db_schema_retrieval import (
    expand_business_terms_for_retrieval,
)


def test_expand_business_terms_for_retrieval_includes_sales_aliases():
    expanded = expand_business_terms_for_retrieval(
        "Create a SalesPerson performance ranking chart"
    )

    assert "salesperson ranking" in expanded
    assert "customer growth" in expanded
    assert "Create a SalesPerson performance ranking chart" in expanded


def test_expand_business_terms_for_retrieval_leaves_non_analytics_query_unchanged():
    query = "Explain what this workspace does"

    assert expand_business_terms_for_retrieval(query) == query
