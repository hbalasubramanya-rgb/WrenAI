from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any


def quote_mssql_identifier(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def quote_sqlite_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_mssql_literal(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def map_sqlite_type(sqlite_type: str) -> str:
    normalized = (sqlite_type or "").upper()

    if "BOOL" in normalized:
        return "BIT"
    if "INT" in normalized:
        return "BIGINT"
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT", "VARCHAR")):
        return "NVARCHAR(MAX)"
    if "BLOB" in normalized:
        return "VARBINARY(MAX)"
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return "FLOAT"
    if any(token in normalized for token in ("NUM", "DEC")):
        return "DECIMAL(38, 10)"
    if any(token in normalized for token in ("DATE", "TIME")):
        return "DATETIME2"

    return "NVARCHAR(MAX)"


def make_index_safe_type(mssql_type: str) -> str:
    if mssql_type == "NVARCHAR(MAX)":
        return "NVARCHAR(450)"
    if mssql_type == "VARBINARY(MAX)":
        return "VARBINARY(900)"
    return mssql_type


def validate_sqlite_db(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    if not db_path.is_file():
        raise RuntimeError(f"SQLite database path is not a file: {db_path}")


def get_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [row[0] for row in rows]


def get_columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(
        f"PRAGMA table_info({quote_sqlite_identifier(table)})"
    ).fetchall()


def create_schema(conn: Any, schema: str) -> None:
    cursor = conn.cursor()
    cursor.execute(
        f"""
        IF NOT EXISTS (
            SELECT 1
            FROM sys.schemas
            WHERE name = ?
        )
        EXEC('CREATE SCHEMA {quote_mssql_identifier(schema)}')
        """,
        schema,
    )
    conn.commit()


def table_exists(conn: Any, schema: str, table: str) -> bool:
    cursor = conn.cursor()
    row = cursor.execute(
        """
        SELECT 1
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ?
          AND TABLE_NAME = ?
          AND TABLE_TYPE = 'BASE TABLE'
        """,
        schema,
        table,
    ).fetchone()
    return row is not None


def drop_table(conn: Any, schema: str, table: str) -> None:
    full_table = (
        f"{quote_mssql_identifier(schema)}.{quote_mssql_identifier(table)}"
    )
    cursor = conn.cursor()
    cursor.execute(f"DROP TABLE {full_table}")
    conn.commit()


def create_table(
    mssql_conn: Any,
    sqlite_conn: sqlite3.Connection,
    schema: str,
    table: str,
    drop_existing: bool,
) -> None:
    if drop_existing and table_exists(mssql_conn, schema, table):
        drop_table(mssql_conn, schema, table)

    if table_exists(mssql_conn, schema, table):
        return

    columns = get_columns(sqlite_conn, table)
    if not columns:
        return

    column_definitions = []
    primary_key_columns = []

    for column in columns:
        name = column["name"]
        sqlite_type = column["type"]
        not_null = bool(column["notnull"])
        primary_key_position = int(column["pk"])

        mssql_type = map_sqlite_type(sqlite_type)
        if primary_key_position:
            mssql_type = make_index_safe_type(mssql_type)
        nullable = "NOT NULL" if not_null or primary_key_position else "NULL"

        column_definitions.append(
            f"{quote_mssql_identifier(name)} {mssql_type} {nullable}"
        )

        if primary_key_position:
            primary_key_columns.append((primary_key_position, name))

    primary_key_columns.sort()
    if primary_key_columns:
        primary_key_sql = ", ".join(
            quote_mssql_identifier(name) for _, name in primary_key_columns
        )
        constraint_name = f"PK_{table}"[:128]
        column_definitions.append(
            "CONSTRAINT "
            f"{quote_mssql_identifier(constraint_name)} "
            f"PRIMARY KEY ({primary_key_sql})"
        )

    full_table = (
        f"{quote_mssql_identifier(schema)}.{quote_mssql_identifier(table)}"
    )
    create_sql = (
        f"CREATE TABLE {full_table} (\n"
        f"  {',\n  '.join(column_definitions)}\n"
        ")"
    )

    cursor = mssql_conn.cursor()
    cursor.execute(create_sql)
    mssql_conn.commit()


def copy_table_data(
    mssql_conn: Any,
    sqlite_conn: sqlite3.Connection,
    schema: str,
    table: str,
    batch_size: int,
) -> int:
    columns = get_columns(sqlite_conn, table)
    column_names = [column["name"] for column in columns]

    if not column_names:
        return 0

    sqlite_cursor = sqlite_conn.cursor()
    sqlite_cursor.execute(
        "SELECT "
        + ", ".join(quote_sqlite_identifier(column) for column in column_names)
        + f" FROM {quote_sqlite_identifier(table)}"
    )

    full_table = (
        f"{quote_mssql_identifier(schema)}.{quote_mssql_identifier(table)}"
    )
    insert_sql = (
        f"INSERT INTO {full_table} ("
        + ", ".join(quote_mssql_identifier(column) for column in column_names)
        + ") VALUES ("
        + ", ".join("?" for _ in column_names)
        + ")"
    )

    mssql_cursor = mssql_conn.cursor()
    mssql_cursor.fast_executemany = True

    copied = 0
    while True:
        rows = sqlite_cursor.fetchmany(batch_size)
        if not rows:
            break

        mssql_cursor.executemany(insert_sql, rows)
        mssql_conn.commit()
        copied += len(rows)

    return copied


def clear_table(conn: Any, schema: str, table: str) -> None:
    full_table = (
        f"{quote_mssql_identifier(schema)}.{quote_mssql_identifier(table)}"
    )
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {full_table}")
    conn.commit()


def migrate(args: argparse.Namespace) -> None:
    sqlite_path = Path(args.sqlite_db).resolve()
    validate_sqlite_db(sqlite_path)

    connection_string = args.connection_string or os.environ.get("MSSQL_CONN")
    if not connection_string:
        raise RuntimeError(
            "Provide --connection-string or set the MSSQL_CONN environment variable."
        )
    connection_string = " ".join(connection_string.split())

    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError(
            "Python package pyodbc is not installed. Install it with: pip3 install pyodbc"
        ) from exc

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    mssql_conn = pyodbc.connect(connection_string)

    try:
        create_schema(mssql_conn, args.schema)
        tables = get_tables(sqlite_conn)

        if not tables:
            raise RuntimeError("No user tables found in the SQLite database.")

        print(f"Found {len(tables)} SQLite tables.")

        for table in tables:
            print(f"Creating table: {args.schema}.{table}")
            create_table(
                mssql_conn=mssql_conn,
                sqlite_conn=sqlite_conn,
                schema=args.schema,
                table=table,
                drop_existing=args.drop_existing,
            )

        for table in tables:
            if args.clear_existing and not args.drop_existing:
                print(f"Clearing existing rows: {args.schema}.{table}")
                clear_table(mssql_conn, args.schema, table)

            print(f"Copying data: {table}")
            copied = copy_table_data(
                mssql_conn=mssql_conn,
                sqlite_conn=sqlite_conn,
                schema=args.schema,
                table=table,
                batch_size=args.batch_size,
            )
            print(f"Copied {copied} rows into {args.schema}.{table}")
    finally:
        sqlite_conn.close()
        mssql_conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate SQLite tables and data into Microsoft SQL Server."
    )
    parser.add_argument("sqlite_db", help="Path to the source SQLite .db file")
    parser.add_argument(
        "--schema",
        default="dbo",
        help="Target SQL Server schema name",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows inserted per batch",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop target tables before creating and loading them",
    )
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Delete target table rows before loading data without dropping tables",
    )
    parser.add_argument(
        "--connection-string",
        help="MSSQL pyodbc connection string. Defaults to MSSQL_CONN env var.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    migrate(parse_args())
