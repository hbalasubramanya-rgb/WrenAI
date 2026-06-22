from app.model.connector import MSSqlConnector
from app.model.error import DIALECT_SQL, ErrorCode, ErrorPhase, WrenError


class FakeCursor:
    def __init__(self, rows, description):
        self._rows = rows
        self.description = description
        self.closed = False

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursors=None, error=None):
        self.cursors = list(cursors or [])
        self.error = error
        self.queries = []

    def raw_sql(self, sql):
        self.queries.append(sql)
        if self.error:
            error = self.error
            self.error = None
            raise error
        return self.cursors.pop(0)


def _connector(connection):
    connector = MSSqlConnector.__new__(MSSqlConnector)
    connector.connection = connection
    return connector


def test_query_uses_raw_sql_for_grouped_aggregate_results():
    connection = FakeConnection(
        [
            FakeCursor(
                rows=[("Widgets", 12), ("Gadgets", 8)],
                description=[("ProdType",), ("TotalQty",)],
            )
        ]
    )
    connector = _connector(connection)

    result = connector.query(
        'SELECT "ProdType", SUM("Qty") AS "TotalQty" '
        'FROM "dbo_tblSalesHistory" '
        'GROUP BY "ProdType" '
        'ORDER BY SUM("Qty") DESC NULLS LAST'
    )

    assert connection.queries == [
        'SELECT "ProdType", SUM("Qty") AS "TotalQty" '
        'FROM "dbo_tblSalesHistory" '
        'GROUP BY "ProdType" '
        'ORDER BY SUM("Qty") DESC'
    ]
    assert result.column_names == ["ProdType", "TotalQty"]
    assert result.to_pylist() == [
        {"ProdType": "Widgets", "TotalQty": 12},
        {"ProdType": "Gadgets", "TotalQty": 8},
    ]


def test_query_translates_masked_mssql_describe_error():
    connection = FakeConnection(
        cursors=[
            FakeCursor(
                rows=[("Invalid column name 'ProdType'.",)],
                description=[("error_message",)],
            )
        ],
        error=AttributeError("'NoneType' object has no attribute 'lower'"),
    )
    connector = _connector(connection)

    try:
        connector.query('SELECT "ProdType" FROM "dbo_tblSalesHistory"')
    except WrenError as exc:
        error = exc
    else:
        raise AssertionError("Expected WrenError")

    assert error.error_code == ErrorCode.INVALID_SQL
    assert error.phase == ErrorPhase.SQL_EXECUTION
    assert error.metadata == {
        DIALECT_SQL: 'SELECT "ProdType" FROM "dbo_tblSalesHistory"'
    }
    assert "Invalid column name 'ProdType'." in error.message
