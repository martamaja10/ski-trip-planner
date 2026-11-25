"""Run the documented SQL queries in sql/ against the processed Parquet dataset (DuckDB).

Usage (from the repository root):
    python scripts/run_sql_queries.py

Each .sql file answers one question. Query results are printed and saved as CSV files
under data/interim/sql_results/. The consistency checks (05_*) must all be true.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


def split_statements(sql_text: str) -> list[str]:
    statements = []
    for part in sql_text.split(";"):
        # drop comment-only lines so the statement type can be recognised
        lines = [ln for ln in part.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        if lines:
            statements.append("\n".join(lines))
    return statements


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--parquet", type=Path, default=Path("data/processed/weather_hourly.parquet"))
    parser.add_argument("--sql-dir", type=Path, default=Path("sql"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/interim/sql_results"))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")  # keeps CAST(timestamp AS DATE) on UTC days
    con.execute(f"CREATE VIEW hourly AS SELECT * FROM read_parquet('{args.parquet.as_posix()}')")

    for sql_file in sorted(args.sql_dir.glob("*.sql")):
        results = None
        for statement in split_statements(sql_file.read_text()):
            result = con.execute(statement)
            if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                results = result.df()
        if results is None:
            print(f"{sql_file.name}: no result set")
            continue

        out_path = args.out_dir / f"{sql_file.stem}.csv"
        results.to_csv(out_path, index=False)
        print(f"\n=== {sql_file.name}: {len(results):,} rows -> {out_path}")
        print(results.head(5).to_string(index=False))

        if sql_file.name.startswith("05_"):
            failed = [c for c in results.columns if not bool(results[c].iloc[0])]
            if failed:
                raise SystemExit(f"Consistency checks failed: {failed}")
            print("All consistency checks passed.")


if __name__ == "__main__":
    main()
