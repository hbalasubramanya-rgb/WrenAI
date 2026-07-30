import base64

import orjson

from app.mdl.rewriter import rewrite_mssql_logical_tables_to_physical


def test_rewrite_mssql_logical_tables_to_physical_uses_manifest_table_reference():
    logical_model = "logical_model"
    physical_schema = "physical_schema"
    physical_table = "physical_table"
    column_name = "Column With Spaces"
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
        f'SELECT "{logical_model}"."{column_name}" FROM "{logical_model}"',
        manifest_str,
    )

    assert (
        rewritten
        == "SELECT [logical_model].[Column With Spaces] "
        "FROM [physical_schema].[physical_table] AS [logical_model]"
    )
