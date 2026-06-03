import argparse
import csv
import os
import sqlite3
from pathlib import Path


def quote_sqlite_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def ensure_parent_dir(path: Path) -> None:
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def export_sql_dump(db_path: Path, output_path: Path) -> None:
    ensure_parent_dir(output_path)

    with sqlite3.connect(db_path) as conn:
        with output_path.open("w", encoding="utf-8", newline="\n") as output:
            for line in conn.iterdump():
                output.write(f"{line}\n")


def export_db_backup(db_path: Path, output_path: Path) -> None:
    ensure_parent_dir(output_path)

    with sqlite3.connect(db_path) as source:
        with sqlite3.connect(output_path) as target:
            source.backup(target)


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


def export_table_csv(
    conn: sqlite3.Connection,
    table_name: str,
    output_path: Path,
) -> int:
    ensure_parent_dir(output_path)

    cursor = conn.execute(f"SELECT * FROM {quote_sqlite_identifier(table_name)}")
    column_names = [description[0] for description in cursor.description]

    row_count = 0
    with output_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(column_names)

        for row in cursor:
            writer.writerow(row)
            row_count += 1

    return row_count


def export_csv(db_path: Path, output_dir: Path, table: str | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        tables = [table] if table else get_tables(conn)

        if not tables:
            raise RuntimeError("No user tables found in the SQLite database.")

        existing_tables = set(get_tables(conn))
        missing_tables = [name for name in tables if name not in existing_tables]
        if missing_tables:
            missing = ", ".join(missing_tables)
            raise RuntimeError(f"Table not found: {missing}")

        for table_name in tables:
            output_path = output_dir / f"{table_name}.csv"
            count = export_table_csv(conn, table_name, output_path)
            print(f"Exported {count} rows from {table_name} to {output_path}")


def validate_db_path(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    if not db_path.is_file():
        raise RuntimeError(f"SQLite database path is not a file: {db_path}")


def default_output_path(db_path: Path, mode: str) -> Path:
    if mode == "dump":
        return db_path.with_suffix(".sql")
    if mode == "backup":
        return db_path.with_name(f"{db_path.stem}_backup{db_path.suffix or '.db'}")
    return Path(f"{db_path.stem}_csv_export")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a SQLite database as a SQL dump, DB backup, or CSV files."
    )
    parser.add_argument("sqlite_db", help="Path to the source SQLite .db file")
    parser.add_argument(
        "--mode",
        choices=["dump", "backup", "csv"],
        default="dump",
        help="Export mode: dump creates .sql, backup creates .db copy, csv exports tables",
    )
    parser.add_argument(
        "--output",
        help="Output file for dump/backup mode, or output directory for csv mode",
    )
    parser.add_argument(
        "--table",
        help="For csv mode only: export a single table instead of all tables",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.sqlite_db).resolve()
    validate_db_path(db_path)

    output_path = (
        Path(args.output).resolve()
        if args.output
        else default_output_path(db_path, args.mode).resolve()
    )

    if args.table and args.mode != "csv":
        raise RuntimeError("--table can only be used with --mode csv")

    if args.mode == "dump":
        export_sql_dump(db_path, output_path)
        print(f"SQL dump exported to {output_path}")
    elif args.mode == "backup":
        export_db_backup(db_path, output_path)
        print(f"SQLite backup exported to {output_path}")
    else:
        export_csv(db_path, output_path, args.table)
        print(f"CSV export completed in {output_path}")


if __name__ == "__main__":
    main()
