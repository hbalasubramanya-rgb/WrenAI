from contextlib import closing
from decimal import Decimal as PyDecimal
import re

import pandas as pd
import pyarrow as pa
from ibis.expr.datatypes import Decimal
from ibis.expr.types import Table
from sqlglot import exp, parse_one

from wren.connector.base import IbisConnector
from wren.model.data_source import DataSource
from wren.model.error import DIALECT_SQL, ErrorCode, ErrorPhase, WrenError


class MSSqlConnector(IbisConnector):
    def __init__(self, connection_info):
        super().__init__(DataSource.mssql, connection_info)

    def query(self, sql: str, limit: int | None = None) -> pa.Table:
        sql = self._flatten_pagination_limit(self._normalize_tsql_for_execution(sql))
        try:
            with closing(self.connection.raw_sql(sql)) as cur:
                rows = cur.fetchall()
                columns = [
                    self._cursor_column_name(column, index)
                    for index, column in enumerate(cur.description or [])
                ]

            df = pd.DataFrame(rows, columns=columns)
            if limit is not None:
                df = df.head(limit)
            return pa.Table.from_pandas(df, preserve_index=False)
        except AttributeError as e:
            if self._is_none_lower_attribute_error(e):
                error_message = self._describe_sql_for_error_message(sql)
                raise WrenError(
                    error_code=ErrorCode.INVALID_SQL,
                    message=f"The sql query failed. {error_message or str(e)}.",
                    phase=ErrorPhase.SQL_EXECUTION,
                    metadata={DIALECT_SQL: sql},
                ) from e
            raise

    def _round_decimal_columns(self, ibis_table: Table, scale: int = 9) -> pa.Table:
        def round_decimal(val):
            if val is None:
                return None
            d = PyDecimal(str(val))
            return d.quantize(PyDecimal("1." + "0" * scale))

        decimal_columns = [
            name
            for name, dtype in ibis_table.schema().items()
            if isinstance(dtype, Decimal)
        ]
        if not decimal_columns:
            return ibis_table.to_pyarrow()

        pandas_df = ibis_table.to_pandas()
        for col_name in decimal_columns:
            pandas_df[col_name] = pandas_df[col_name].apply(round_decimal)
        return pa.Table.from_pandas(pandas_df)

    def _flatten_pagination_limit(
        self, sql_query: str, input_dialect: str = "tsql"
    ) -> str:
        try:
            parsed = parse_one(sql_query, dialect=input_dialect)
            if not isinstance(parsed, exp.Select) or not parsed.args.get("limit"):
                return sql_query

            from_clause = parsed.find(exp.From)
            if not from_clause:
                return sql_query

            subqueries = []
            if isinstance(from_clause.this, exp.Subquery):
                subqueries.append(from_clause.this)
            for join in parsed.args.get("joins") or []:
                if isinstance(join, exp.Join):
                    if isinstance(join.this, exp.Subquery):
                        subqueries.append(join.this)
                    if join.expression and isinstance(join.expression, exp.Subquery):
                        subqueries.append(join.expression)

            if len(subqueries) != 1:
                return sql_query

            inner = subqueries[0].this
            if not isinstance(inner, exp.Select):
                return sql_query

            inner.set("limit", exp.Limit(expression=parsed.args["limit"].expression))
            return inner.sql(dialect="tsql")
        except Exception:
            return sql_query

    def _normalize_tsql_for_execution(self, sql: str) -> str:
        replacements = (
            (r"DATE_PART\s*\(", "DATEPART("),
            (r"DATEPART\(\s*YEAR\s*,", "DATEPART('YEAR',"),
            (r"DATEPART\(\s*MONTH\s*,", "DATEPART('MONTH',"),
            (r"DATEPART\(\s*DAY\s*,", "DATEPART('DAY',"),
            (r"DATEDIFF\(\s*'SECOND'\s*,", "DATEDIFF(SECOND,"),
            (r"DATEDIFF\(\s*'MINUTE'\s*,", "DATEDIFF(MINUTE,"),
            (r"DATEDIFF\(\s*'HOUR'\s*,", "DATEDIFF(HOUR,"),
            (r"DATEDIFF\(\s*'DAY'\s*,", "DATEDIFF(DAY,"),
            (r"\s+NULLS\s+LAST\b", ""),
            (r"\s+NULLS\s+FIRST\b", ""),
        )

        normalized = sql
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)

        return normalized

    def _quote_sql_literal(self, sql: str) -> str:
        return "N'" + sql.replace("'", "''") + "'"

    def dry_run(self, sql: str) -> None:
        normalized_sql = self._normalize_tsql_for_execution(sql)
        try:
            error_message = self._describe_sql_for_error_message(normalized_sql)
            if error_message:
                raise WrenError(
                    error_code=ErrorCode.INVALID_SQL,
                    message=f"The sql dry run failed. {error_message}.",
                    phase=ErrorPhase.SQL_DRY_RUN,
                    metadata={DIALECT_SQL: normalized_sql},
                )
        except WrenError:
            raise
        except AttributeError as e:
            if self._is_none_lower_attribute_error(e):
                error_message = self._describe_sql_for_error_message(normalized_sql)
                raise WrenError(
                    error_code=ErrorCode.INVALID_SQL,
                    message=f"The sql dry run failed. {error_message}.",
                    phase=ErrorPhase.SQL_DRY_RUN,
                    metadata={DIALECT_SQL: normalized_sql},
                ) from e
            raise WrenError(
                error_code=ErrorCode.IBIS_PROJECT_ERROR,
                message=str(e),
                phase=ErrorPhase.SQL_DRY_RUN,
            ) from e
        except Exception as e:
            raise WrenError(
                error_code=ErrorCode.INVALID_SQL,
                message=f"The sql dry run failed. {e!s}.",
                phase=ErrorPhase.SQL_DRY_RUN,
                metadata={DIALECT_SQL: normalized_sql},
            ) from e

    def _describe_sql_for_error_message(self, sql: str) -> str:
        describe_sql = (
            "SELECT error_message "
            "FROM sys.dm_exec_describe_first_result_set("
            f"{self._quote_sql_literal(sql)}, NULL, 0)"
        )
        with closing(self.connection.raw_sql(describe_sql)) as cur:
            rows = cur.fetchall()
            if rows is None or len(rows) == 0:
                return ""
            return rows[0][0] or ""

    @staticmethod
    def _cursor_column_name(column, index: int) -> str:
        name = getattr(column, "name", None)
        if name is None and isinstance(column, (tuple, list)) and column:
            name = column[0]
        return str(name or f"column_{index + 1}")

    @staticmethod
    def _is_none_lower_attribute_error(error: AttributeError) -> bool:
        return "NoneType" in str(error) and "lower" in str(error)


def create_connector(connection_info) -> MSSqlConnector:
    return MSSqlConnector(connection_info)
