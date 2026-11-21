"""Unit tests for electoral-roll ETL validation, card parsing, and anonymization."""
import csv
from pathlib import Path

from etl.anonymize import anonymize
from etl.extract_roll import parse_card, validate


def make_valid_rows():
    rows = []
    for n in range(1, 495):
        gender = "M" if n <= 254 else ("F" if n <= 493 else "T")
        rows.append(
            {
                "serial_no": n,
                "epic": f"ABC{n:07d}",
                "section_no": "1",
                "section_name": "Demo Section",
                "name": f"Real Person {n}",
                "relation_type": "Fathers",
                "relation_name": f"Real Father {n}",
                "house_no": f"H-{n}",
                "age": str(20 + (n % 50)),
                "gender": gender,
            }
        )
    return rows


def test_valid_fixture():
    assert validate(make_valid_rows()) == []


def test_gender_validation():
    rows = make_valid_rows()
    rows[0]["gender"] = "F"
    assert any("gender counts" in err for err in validate(rows))


def test_serial_gap_and_row_count():
    rows = make_valid_rows()
    rows.pop(20)
    errors = validate(rows)
    assert any("row count" in err for err in errors)
    assert any("serials are not continuous" in err for err in errors)


def test_duplicate_epic():
    rows = make_valid_rows()
    rows[1]["epic"] = rows[0]["epic"]
    assert any("duplicate EPIC" in err for err in validate(rows))


def test_invalid_epic_format():
    rows = make_valid_rows()
    rows[0]["epic"] = "BAD-EPIC"
    assert any("invalid EPIC format" in err for err in validate(rows))


def test_missing_core_fields():
    rows = make_valid_rows()
    rows[0]["name"] = ""
    assert any("missing core fields" in err for err in validate(rows))


def test_invalid_age_bounds():
    rows = make_valid_rows()
    rows[0]["age"] = "17"
    assert any("invalid age" in err for err in validate(rows))

    rows2 = make_valid_rows()
    rows2[0]["age"] = "121"
    assert any("invalid age" in err for err in validate(rows2))


def test_parse_card_labeled():
    lines = [
        "Name : Asha Rao",
        "Fathers Name : Mohan Rao",
        "House Number : 14-B",
        "Age : 34",
        "Gender : Female",
    ]
    parsed = parse_card(lines, "XYZ1234567", 12, "2", "East Block")
    assert parsed["serial_no"] == 12
    assert parsed["epic"] == "XYZ1234567"
    assert parsed["name"] == "Asha Rao"
    assert parsed["relation_type"] == "Fathers"
    assert parsed["relation_name"] == "Mohan Rao"
    assert parsed["house_no"] == "14-B"
    assert parsed["age"] == "34"
    assert parsed["gender"] == "F"


def test_anonymize_preserves_structure_and_replaces_pii(tmp_path: Path):
    src = tmp_path / "extracted" / "voters.csv"
    dst = tmp_path / "anonymized" / "nested" / "voters.csv"
    src.parent.mkdir(parents=True)

    original = make_valid_rows()
    with src.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(original[0].keys()))
        writer.writeheader()
        writer.writerows(original)

    written = anonymize(src, dst, seed=42)
    assert written == 494
    assert dst.exists()

    with dst.open("r", encoding="utf-8", newline="") as fh:
        anon = list(csv.DictReader(fh))

    assert validate(anon) == []
    assert [r["age"] for r in anon] == [r["age"] for r in original]
    assert [r["gender"] for r in anon] == [r["gender"] for r in original]
    assert [int(r["serial_no"]) for r in anon] == [int(r["serial_no"]) for r in original]
    assert all(a["name"] != o["name"] for a, o in zip(anon, original))
    assert all(a["epic"].startswith("DEM") for a in anon)
