"""Create a privacy-preserving demo CSV while retaining roll structure and counts.

Replaces personally identifying fields (name, relation_name, house_no, epic)
while preserving row count, serial numbers, sections, ages, gender distribution,
and unique EPIC format.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from faker import Faker


def anonymize(source: Path, dest: Path, seed: int = 42) -> int:
    fake = Faker("en_IN")
    fake.seed_instance(seed)

    with source.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        raise ValueError(f"Source CSV is empty: {source}")

    fieldnames = list(rows[0].keys())
    for row in rows:
        row["name"] = fake.name()
        row["relation_name"] = fake.name()
        row["house_no"] = str(fake.building_number())
        row["epic"] = f"DEM{int(row['serial_no']):07d}"

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Anonymize extracted electoral-roll CSV for demos.")
    parser.add_argument("source", type=Path, help="Input CSV path")
    parser.add_argument("dest", type=Path, help="Output anonymized CSV path")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic Faker seed (default: 42)")
    args = parser.parse_args()

    count = anonymize(args.source, args.dest, seed=args.seed)
    print(f"Anonymized {count} rows -> {args.dest}")


if __name__ == "__main__":
    main()
