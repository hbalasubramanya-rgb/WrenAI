import base64

import orjson

from app.mdl.rewriter import rewrite_mssql_logical_tables_to_physical


def test_rewrite_mssql_logical_tables_to_physical_uses_manifest_table_reference():
    logical_model = "logical_model"
    physical_schema = "physical_schema"
    physical_table = "physical_table"
    logical_column = "logical_column"
    physical_column = "Column With Spaces"
    manifest = {
        "models": [
            {
                "name": logical_model,
                "tableReference": {
                    "schema": physical_schema,
                    "table": physical_table,
                },
                "columns": [
                    {
                        "name": logical_column,
                        "type": "VARCHAR",
                        "expression": f'"{physical_column}"',
                    }
                ],
            }
        ]
    }
    manifest_str = base64.b64encode(orjson.dumps(manifest)).decode("utf-8")

    rewritten = rewrite_mssql_logical_tables_to_physical(
        f'SELECT "{logical_model}"."{logical_column}" FROM "{logical_model}"',
        manifest_str,
    )

    assert (
        rewritten == "SELECT [logical_model].[Column With Spaces] "
        "FROM [physical_schema].[physical_table] AS [logical_model]"
    )


def test_rewrite_mssql_logical_tables_to_physical_rewrites_cte_source():
    logical_model = "logical_model"
    physical_schema = "physical_schema"
    physical_table = "physical_table"
    manifest = {
        "models": [
            {
                "name": logical_model,
                "tableReference": {
                    "schema": physical_schema,
                    "table": physical_table,
                },
            }
        ]
    }
    manifest_str = base64.b64encode(orjson.dumps(manifest)).decode("utf-8")

    rewritten = rewrite_mssql_logical_tables_to_physical(
        f'WITH logical_cte AS (SELECT * FROM "{logical_model}") '
        "SELECT * FROM logical_cte",
        manifest_str,
    )

    assert (
        rewritten
        == "WITH logical_cte AS ("
        "SELECT * FROM [physical_schema].[physical_table] AS [logical_model]"
        ") SELECT * FROM logical_cte"
    )


def test_rewrite_mssql_logical_tables_to_physical_rewrites_all_manifest_models():
    first_model = "first_logical_model"
    second_model = "second_logical_model"
    first_schema = "first_physical_schema"
    second_schema = "second_physical_schema"
    first_table = "first_physical_table"
    second_table = "second_physical_table"
    manifest = {
        "models": [
            {
                "name": first_model,
                "tableReference": {
                    "schema": first_schema,
                    "table": first_table,
                },
            },
            {
                "name": second_model,
                "tableReference": {
                    "schema": second_schema,
                    "table": second_table,
                },
            },
        ]
    }
    manifest_str = base64.b64encode(orjson.dumps(manifest)).decode("utf-8")

    rewritten = rewrite_mssql_logical_tables_to_physical(
        f'SELECT * FROM "{first_model}" '
        f'UNION ALL SELECT * FROM "{second_model}"',
        manifest_str,
    )

    assert (
        rewritten
        == "SELECT * FROM [first_physical_schema].[first_physical_table] "
        "AS [first_logical_model] UNION ALL SELECT * FROM "
        "[second_physical_schema].[second_physical_table] "
        "AS [second_logical_model]"
    )


def test_rewrite_mssql_logical_tables_to_physical_rewrites_unqualified_logical_columns():
    logical_model = "logical_model"
    logical_column = "logical_column"
    physical_column = "Physical Column"
    manifest = {
        "models": [
            {
                "name": logical_model,
                "tableReference": {
                    "schema": "physical_schema",
                    "table": "physical_table",
                },
                "columns": [
                    {
                        "name": logical_column,
                        "type": "VARCHAR",
                        "expression": f'"{physical_column}"',
                    }
                ],
            }
        ]
    }
    manifest_str = base64.b64encode(orjson.dumps(manifest)).decode("utf-8")

    rewritten = rewrite_mssql_logical_tables_to_physical(
        f'SELECT "{logical_column}" FROM "{logical_model}"',
        manifest_str,
    )

    assert (
        rewritten == "SELECT [Physical Column] "
        "FROM [physical_schema].[physical_table] AS [logical_model]"
    )


def test_rewrite_mssql_logical_tables_to_physical_uses_manifest_source_column_property():
    logical_model = "logical_model"
    logical_column = "logical_column"
    physical_column = "Physical Column"
    manifest = {
        "models": [
            {
                "name": logical_model,
                "tableReference": {
                    "schema": "physical_schema",
                    "table": "physical_table",
                },
                "columns": [
                    {
                        "name": logical_column,
                        "type": "VARCHAR",
                        "properties": {"sourceColumnName": physical_column},
                    }
                ],
            }
        ]
    }
    manifest_str = base64.b64encode(orjson.dumps(manifest)).decode("utf-8")

    rewritten = rewrite_mssql_logical_tables_to_physical(
        f'SELECT "{logical_model}"."{logical_column}" FROM "{logical_model}"',
        manifest_str,
    )

    assert (
        rewritten == "SELECT [logical_model].[Physical Column] "
        "FROM [physical_schema].[physical_table] AS [logical_model]"
    )


def test_rewrite_mssql_logical_tables_to_physical_keeps_calculated_expressions():
    logical_model = "logical_model"
    logical_column = "logical_column"
    manifest = {
        "models": [
            {
                "name": logical_model,
                "tableReference": {
                    "schema": "physical_schema",
                    "table": "physical_table",
                },
                "columns": [
                    {
                        "name": logical_column,
                        "type": "VARCHAR",
                        "isCalculated": True,
                        "expression": "upper(other_column)",
                    }
                ],
            }
        ]
    }
    manifest_str = base64.b64encode(orjson.dumps(manifest)).decode("utf-8")

    rewritten = rewrite_mssql_logical_tables_to_physical(
        f'SELECT "{logical_model}"."{logical_column}" FROM "{logical_model}"',
        manifest_str,
    )

    assert (
        rewritten == "SELECT [logical_model].[logical_column] "
        "FROM [physical_schema].[physical_table] AS [logical_model]"
    )
