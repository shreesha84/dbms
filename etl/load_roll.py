"""Idempotently load an extracted or anonymized electoral-roll CSV into PostgreSQL."""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import psycopg

REQUIRED_COLUMNS = {
    "epic",
    "serial_no",
    "name",
    "relation_type",
    "relation_name",
    "house_no",
    "age",
    "gender",
}


def load(csv_path: Path, dsn: str) -> int:
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
            missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
            raise ValueError(f"CSV is missing required columns: {missing}")
        rows = list(reader)

    if not rows:
        raise ValueError(f"CSV contains no voter rows: {csv_path}")

    part_no = int(os.getenv("PART_NO", "271"))
    assembly_constituency = int(os.getenv("ASSEMBLY_CONSTITUENCY", "174"))
    part_name = os.getenv("PART_NAME", "Mahadevapura")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO part(part_no, assembly_constituency, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (part_no) DO NOTHING
                """,
                (part_no, assembly_constituency, part_name),
            )

            for row in rows:
                section_no = int(row.get("section_no") or 1)
                section_name = (row.get("section_name") or f"Section {section_no}").strip()
                cur.execute(
                    """
                    INSERT INTO section(part_no, section_no, section_name)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (part_no, section_no) DO NOTHING
                    """,
                    (part_no, section_no, section_name),
                )
                cur.execute(
                    """
                    INSERT INTO voter(
                        epic, part_no, section_no, serial_no, name,
                        relation_type, relation_name, house_no, age, gender
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (epic) DO NOTHING
                    """,
                    (
                        row["epic"].strip().upper(),
                        part_no,
                        section_no,
                        int(row["serial_no"]),
                        row["name"].strip(),
                        row["relation_type"].strip() or "Others",
                        row["relation_name"].strip(),
                        row["house_no"].strip(),
                        int(row["age"]),
                        row["gender"].strip().upper(),
                    ),
                )

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotently load electoral-roll CSV into PostgreSQL.")
    parser.add_argument("csv", type=Path, help="Path to validated or anonymized voters.csv")
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL", "postgresql://sir:sir@localhost:5432/sir"),
        help="PostgreSQL connection URL (default: $DATABASE_URL)",
    )
    args = parser.parse_args()

    count = load(args.csv, args.dsn)
    print(f"Processed {count} voter rows into PostgreSQL (existing voters preserved).")


if __name__ == "__main__":
    main()
