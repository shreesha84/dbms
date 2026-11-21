"""Extract an ECI electoral-roll PDF into normalized CSV rows with OCR fallback.

Fails closed unless the extracted rows pass all validation checks:
- Exact expected elector count (494)
- Exact gender counts (M=254, F=239, T=1)
- Continuous serial numbers (1..494)
- Unique, valid EPIC identifiers
- Non-empty core fields (name, age 18..120, gender)
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
from pathlib import Path

import pdfplumber
from PIL import Image

EXPECTED_TOTAL = 494
EXPECTED_GENDER_COUNTS = {"M": 254, "F": 239, "T": 1}

CARD_RE = re.compile(r"^[\|\[\(\s]*(\d{1,4})\s+([A-Z01]{3}[0-9A-Z/]{7,8})\b")
EPIC_RE = re.compile(r"^[A-Z]{3}\d{7,8}$")
HEADER_RE = re.compile(r"Section No\.?\s*and\s*Name\s*[:.]?\s*(\d+)\s*[-:]\s*(.+)", re.I)
REL_RE = re.compile(r"^(Fathers|Husbands|Mothers|Others)\s*(?:Name)?\s*[:.=;¢]?\s*(.*)$", re.I)

# Standard 3x10 ECI draft-roll card grid coordinates on a 1983x2806 native page.
BASE_W, BASE_H = 1983, 2806
GRID_COLS = [(47, 665), (680, 1299), (1314, 1932)]
GRID_ROWS = [
    (94, 344),
    (358, 608),
    (622, 872),
    (887, 1136),
    (1151, 1400),
    (1415, 1665),
    (1679, 1929),
    (1943, 2193),
    (2207, 2458),
    (2473, 2722),
]

FIELDNAMES = [
    "serial_no",
    "epic",
    "section_no",
    "section_name",
    "name",
    "relation_type",
    "relation_name",
    "house_no",
    "age",
    "gender",
]


def _find_tesseract() -> str | None:
    """Locate tesseract executable from env, PATH, or standard Windows install paths."""
    env_cmd = os.getenv("TESSERACT_CMD")
    if env_cmd and Path(env_cmd).exists():
        return env_cmd

    found = shutil.which("tesseract")
    if found:
        return found

    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _has_ink(crop: Image.Image, threshold: int = 180, min_dark: int = 80) -> bool:
    """Return True if a grayscale crop contains enough dark pixels to hold text."""
    gray = crop.convert("L")
    hist = gray.histogram()
    return sum(hist[:threshold]) >= min_dark


def _erase_borders(crop: Image.Image, threshold: int = 180, ratio: float = 0.80) -> Image.Image:
    """Erase full-height vertical and full-width horizontal box border lines from a crop."""
    gray = crop.convert("L")
    w, h = gray.size
    px = gray.load()
    bad_cols = [x for x in range(w) if sum(1 for y in range(h) if px[x, y] < threshold) > h * ratio]
    bad_rows = [y for y in range(h) if sum(1 for x in range(w) if px[x, y] < threshold) > w * ratio]
    for x in bad_cols:
        for y in range(h):
            px[x, y] = 255
    for y in bad_rows:
        for x in range(w):
            px[x, y] = 255
    return gray.convert("RGB")


def _build_border_free_page_strip(
    img: Image.Image,
) -> tuple[Image.Image | None, list[Image.Image]]:
    """Crop every populated voter card without its border lines and stack into one strip."""
    w, h = img.size
    sx, sy = w / BASE_W, h / BASE_H

    card_w, card_h, gap = int(650 * sx), int(248 * sy), int(24 * sy)
    cards: list[Image.Image] = []
    serial_crops: list[Image.Image] = []

    for ry0, ry1 in GRID_ROWS:
        y0, y1 = int(ry0 * sy), int(ry1 * sy)
        for rx0, rx1 in GRID_COLS:
            x0, x1 = int(rx0 * sx), int(rx1 * sx)
            body_crop = img.crop((x0 + int(8 * sx), y0 + int(50 * sy), x0 + int(462 * sx), y1 - int(4 * sy)))
            if not _has_ink(body_crop):
                continue

            serial_crop = _erase_borders(
                img.crop((x0 + int(10 * sx), y0 + int(8 * sy), x0 + int(214 * sx), y0 + int(47 * sy)))
            )
            epic_crop = img.crop((x0 + int(245 * sx), y0 + int(8 * sy), x1 - int(8 * sx), y0 + int(48 * sy)))

            card_img = Image.new("RGB", (card_w, card_h), "white")
            card_img.paste(serial_crop, (int(10 * sx), int(6 * sy)))
            card_img.paste(epic_crop, (int(230 * sx), int(6 * sy)))
            card_img.paste(body_crop, (int(10 * sx), int(54 * sy)))
            cards.append(card_img)
            serial_crops.append(serial_crop)

    if not cards:
        return None, []

    strip = Image.new("RGB", (card_w, len(cards) * (card_h + gap) + gap), "white")
    for idx, card_img in enumerate(cards):
        strip.paste(card_img, (0, gap + idx * (card_h + gap)))
    return strip, serial_crops


def _ocr_page_columns(page) -> tuple[str, list[list[str]], list[Image.Image]]:
    """Render a scanned PDF page via pdfplumber/pypdfium2 and OCR header + border-free cards."""
    tesseract_cmd = _find_tesseract()
    if not tesseract_cmd:
        raise RuntimeError(
            "PDF has no embedded text layer and Tesseract OCR was not found. "
            "Install Tesseract OCR (or set TESSERACT_CMD) and rerun this script."
        )
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "OCR fallback requires pytesseract (see requirements.txt)."
        ) from exc

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    # Native 72 DPI matches 1983x2806 ECI roll scans 1:1 with zero resampling blur.
    resolution = 72 if page.width >= 1500 else 200
    img = page.to_image(resolution=resolution).original
    w, h = img.size

    header_text = pytesseract.image_to_string(
        img.crop((0, 0, w, int(h * 0.033))),
        config="--psm 6",
    )
    strip, serial_crops = _build_border_free_page_strip(img)
    if strip is None:
        return header_text, [], []

    strip_text = pytesseract.image_to_string(strip, config="--psm 6")
    return header_text, [strip_text.splitlines()], serial_crops


def _reocr_serial(serial_crop: Image.Image) -> int | None:
    """Re-OCR a single serial box at 2x nearest-neighbor scale with digit whitelist."""
    import pytesseract

    tesseract_cmd = _find_tesseract()
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    up = serial_crop.resize((serial_crop.width * 2, serial_crop.height * 2), Image.Resampling.NEAREST)
    txt = pytesseract.image_to_string(
        up, config="--psm 7 -c tessedit_char_whitelist=0123456789"
    ).strip()
    return int(txt) if txt.isdigit() else None


def _normalize_gender(raw: str) -> str:
    token = raw.strip().lower()
    if token in ("male", "m"):
        return "M"
    if token in ("female", "f"):
        return "F"
    if token in ("third gender", "third", "t", "other"):
        return "T"
    return token.upper()


def _clean_epic(raw: str) -> str:
    """Clean minor OCR noise in EPIC prefix (letters) and suffix (digits)."""
    epic = raw.strip().upper()
    if len(epic) >= 10:
        prefix = epic[:3].replace("1", "I").replace("0", "O")
        suffix = (
            epic[3:]
            .replace("O", "0")
            .replace("I", "1")
            .replace("L", "1")
            .replace("S", "5")
            .replace("/", "7")
        )
        epic = prefix + suffix
    return epic


def parse_card(
    lines: list[str],
    epic: str,
    serial: str | int,
    section_no: str,
    section_name: str,
) -> dict[str, str | int]:
    """Parse a single electoral-roll voter card into normalized fields."""
    fields: dict[str, str | int] = {
        "serial_no": int(serial),
        "epic": _clean_epic(epic),
        "section_no": section_no,
        "section_name": section_name,
        "name": "",
        "relation_type": "Others",
        "relation_name": "",
        "house_no": "",
        "age": "",
        "gender": "",
    }
    clean = [re.sub(r"\s+", " ", line).strip() for line in lines if line.strip()]

    for line in clean:
        age_m = re.search(r"\bAge\s*[:.=;¢]?\s*(\d{2,3})\b", line, re.I)
        if age_m and not fields["age"]:
            fields["age"] = age_m.group(1)

        gen_m = re.search(r"\bGender\s*[:.=;¢]?\s*(Male|Female|Third Gender|Third|M|F|T)\b", line, re.I)
        if gen_m and not fields["gender"]:
            fields["gender"] = _normalize_gender(gen_m.group(1))

        name_m = re.match(r"^Name\s*[:.=;¢\-]?\s+(.+)$", line, re.I)
        if not name_m:
            name_m = re.match(r"^Name\s*[:.=;¢]\s*(.+)$", line, re.I)
        if name_m and not fields["name"]:
            val = re.sub(r"\bPhoto\b.*$", "", name_m.group(1), flags=re.I).strip()
            fields["name"] = val

        house_m = re.match(r"^House\s*(?:Number|No\.?)\s*[:.=;¢]?\s*(.*)$", line, re.I)
        if house_m and not fields["house_no"]:
            val = re.sub(r"\bPhoto\b.*$", "", house_m.group(1), flags=re.I).strip()
            fields["house_no"] = val

        rel_m = REL_RE.match(line)
        if rel_m:
            fields["relation_type"] = rel_m.group(1).title()
            val = re.sub(r"\bPhoto\b.*$", "", rel_m.group(2), flags=re.I).strip()
            fields["relation_name"] = val

    if not fields["age"]:
        for line in clean:
            m = re.search(r"\b(\d{2,3})\s*(?:Years?|Yrs?)\b", line, re.I)
            if m:
                fields["age"] = m.group(1)
                break

    if not fields["gender"]:
        for line in clean:
            m = re.search(r"\b(Male|Female|Third Gender|M|F|T)\b", line, re.I)
            if m:
                fields["gender"] = _normalize_gender(m.group(1))
                break

    if not fields["name"]:
        for line in clean:
            if not re.search(
                r"(Name|House|Age|Gender|Fathers|Husbands|Mothers|Others|Photo|Available)",
                line,
                re.I,
            ) and not re.search(r"\d{2,3}\s*(Years?|Yrs?)", line, re.I):
                fields["name"] = line
                break

    for key in ("name", "relation_name", "house_no"):
        fields[key] = str(fields[key]).strip()
    return fields


def _extract_cards_from_lines(
    lines: list[str], section_no: str, section_name: str
) -> list[dict[str, str | int]]:
    starts: list[tuple[int, str, str]] = []
    for idx, line in enumerate(lines):
        m = CARD_RE.match(line.strip())
        if m:
            starts.append((idx, m.group(1), m.group(2)))

    cards: list[dict[str, str | int]] = []
    for pos, (start_idx, serial, epic) in enumerate(starts):
        end_idx = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        cards.append(parse_card(lines[start_idx + 1 : end_idx], epic, serial, section_no, section_name))
    return cards


def extract(pdf_path: Path) -> list[dict[str, str | int]]:
    """Extract voter rows from electoral roll pages (pages 3..21 of a 22-page draft roll)."""
    rows: list[dict[str, str | int]] = []
    seen_pairs: set[tuple[int, str]] = set()

    with pdfplumber.open(pdf_path) as pdf:
        sec_no, sec_name = "1", "Section 1"
        roll_pages = pdf.pages[2:21] if len(pdf.pages) >= 21 else pdf.pages

        for page in roll_pages:
            raw_text = (page.extract_text() or "").strip()
            serial_crops: list[Image.Image] = []
            if raw_text:
                header_text = raw_text
                columns: list[list[str]] = []
                for col in range(3):
                    x0 = page.width * col / 3
                    x1 = page.width * (col + 1) / 3
                    crop_text = page.crop((x0, 0, x1, page.height)).extract_text() or ""
                    columns.append(crop_text.splitlines())
            else:
                header_text, columns, serial_crops = _ocr_page_columns(page)

            for line in header_text.splitlines():
                hm = HEADER_RE.search(line)
                if hm:
                    sec_no, sec_name = hm.group(1), hm.group(2).strip()

            page_cards: list[dict[str, str | int]] = []
            for col_lines in columns:
                page_cards.extend(_extract_cards_from_lines(col_lines, sec_no, sec_name))

            # If a single serial dropped a digit on 1x OCR (causing out-of-order serial on the page),
            # re-OCR just that card's serial box at 2x nearest-neighbor scale.
            if serial_crops and len(page_cards) == len(serial_crops):
                for idx, card in enumerate(page_cards):
                    prev_s = int(page_cards[idx - 1]["serial_no"]) if idx > 0 else None
                    next_s = int(page_cards[idx + 1]["serial_no"]) if idx + 1 < len(page_cards) else None
                    cur_s = int(card["serial_no"])
                    out_of_order = (prev_s is not None and cur_s <= prev_s) or (
                        next_s is not None and cur_s >= next_s
                    )
                    if out_of_order:
                        fixed = _reocr_serial(serial_crops[idx])
                        if fixed is not None:
                            card["serial_no"] = fixed

            for card in page_cards:
                pair = (int(card["serial_no"]), str(card["epic"]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    rows.append(card)

    return sorted(rows, key=lambda r: int(r["serial_no"]))


def validate(rows: list[dict[str, str | int]]) -> list[str]:
    """Validate extracted rows against expected roll totals and integrity rules."""
    errors: list[str] = []

    if len(rows) != EXPECTED_TOTAL:
        errors.append(f"row count {len(rows)} != {EXPECTED_TOTAL}")

    counts = {g: sum(r.get("gender") == g for r in rows) for g in ("M", "F", "T")}
    if counts != EXPECTED_GENDER_COUNTS:
        errors.append(
            f"gender counts {counts} != M={EXPECTED_GENDER_COUNTS['M']}/"
            f"F={EXPECTED_GENDER_COUNTS['F']}/T={EXPECTED_GENDER_COUNTS['T']}"
        )

    serials = [int(r["serial_no"]) for r in rows]
    if sorted(serials) != list(range(1, EXPECTED_TOTAL + 1)):
        errors.append(f"serials are not continuous 1..{EXPECTED_TOTAL}")

    epics = [str(r.get("epic", "")).strip().upper() for r in rows]
    if len(epics) != len(set(epics)):
        errors.append("duplicate EPIC values detected")

    bad_epics = [e for e in epics if not EPIC_RE.match(e)]
    if bad_epics:
        errors.append(f"invalid EPIC format for {len(bad_epics)} rows: {bad_epics[:5]}")

    missing = [
        r.get("epic")
        for r in rows
        if not all(str(r.get(k, "")).strip() for k in ("name", "age", "gender"))
    ]
    if missing:
        errors.append(f"missing core fields for {len(missing)} cards: {missing[:5]}")

    bad_ages: list[str] = []
    for r in rows:
        age_str = str(r.get("age", "")).strip()
        if not age_str.isdigit() or not (18 <= int(age_str) <= 120):
            bad_ages.append(str(r.get("epic")))
    if bad_ages:
        errors.append(f"invalid age (must be 18..120) for {len(bad_ages)} cards: {bad_ages[:5]}")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and validate an ECI electoral-roll PDF.")
    parser.add_argument("pdf", type=Path, help="Path to the electoral-roll PDF")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/extracted"),
        help="Output directory for validated voters.csv",
    )
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"ERROR: PDF file not found: {args.pdf}", file=sys.stderr)
        raise SystemExit(1)

    try:
        rows = extract(args.pdf)
    except RuntimeError as exc:
        print(f"EXTRACTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

    errors = validate(rows)
    if errors:
        for err in errors:
            print("VALIDATION FAILURE:", err, file=sys.stderr)
        raise SystemExit(1)

    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / "voters.csv"
    with out_file.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Validated {len(rows)} voters "
        f"(M={EXPECTED_GENDER_COUNTS['M']}, F={EXPECTED_GENDER_COUNTS['F']}, "
        f"T={EXPECTED_GENDER_COUNTS['T']}); wrote {out_file}"
    )


if __name__ == "__main__":
    main()
