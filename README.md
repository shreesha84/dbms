# SIR (Special Intensive Revision) Management System

Third-year engineering DBMS project implementing electoral-roll enumeration, validation, BLO field verification, booth allocation, voter migration, approved electoral-roll updates, and administrative reporting.

**Tech stack:** PostgreSQL 15+, Python 3.11+, FastAPI, Jinja2 templates, parameterized SQL via `psycopg` (no ORM, no JavaScript framework), and `pytest`.

---

## 1. Prerequisites

- **Python 3.11+**
- **PostgreSQL 15+** (with `psql` on your `PATH`)
- **Optional (only if extracting a scanned/image-based PDF without a text layer):**
  - [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (`tesseract` on `PATH`, installed at `C:\Program Files\Tesseract-OCR\tesseract.exe`, or configured via `TESSERACT_CMD`). Poppler is not required because `pdfplumber` renders pages directly via `pypdfium2`.

---

## 2. Local Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

*(On Linux/macOS, activate with `source .venv/bin/activate`.)*

---

## 3. PostgreSQL Database Setup

Create the `sir` role and database using `psql` as a PostgreSQL superuser (e.g. `postgres`):

```sql
CREATE USER sir WITH PASSWORD 'sir' CREATEROLE;
CREATE DATABASE sir OWNER sir;
```

Set `DATABASE_URL` and apply the schema (tables, constraints, indexes, triggers, PL/pgSQL functions, views, and roles):

```powershell
$env:DATABASE_URL = "postgresql://sir:sir@localhost:5432/sir"
psql $env:DATABASE_URL -f db/schema.sql
```

---

## 4. Data Import, Anonymization, and Seeding

### 4.1 Extract & validate the electoral-roll PDF
The extractor checks for an embedded PDF text layer first and falls back to OCR when the PDF is scanned. It fails closed unless all integrity checks pass (494 voters, M=254 / F=239 / T=1, continuous serials 1..494, unique valid EPICs, and non-empty core fields).

```powershell
python etl/extract_roll.py .\2026-EROLLGEN-S10-174-SIR-DraftRoll-Revision1-ENG-271-WI.pdf
```

### 4.2 Anonymize for demo use
Replaces personally identifying fields (`name`, `relation_name`, `house_no`, `epic`) while preserving row count, gender counts, ages, serials, sections, and EPIC format/uniqueness:

```powershell
python etl/anonymize.py data/extracted/voters.csv data/anonymized/voters.csv
```

### 4.3 Load into PostgreSQL (safe to rerun)

```powershell
python etl/load_roll.py data/anonymized/voters.csv
```

### 4.4 Seed demo login accounts

```powershell
python seed.py
```

Default local demo credentials:
- **SIR Administrator:** `admin` / `admin123`
- **BLOs:** `blo1` / `blo1123`, `blo2` / `blo2123`
- **Voters:** `voter1` .. `voter10` / `voter123` (created after voter rows are loaded)

---

## 5. Run the Web Application

```powershell
uvicorn app:app --reload
```

Open `http://localhost:8000` in your browser.

---

## 6. SQL Walkthrough & Index Exercise

Run the end-to-end SQL walkthrough (demonstrating Processes 1.0–6.0 plus an invalid form submission) against a disposable demo database:

```powershell
psql $env:DATABASE_URL -f demo.sql
```

Run the optional 100k-row B-tree index comparison exercise (executes inside a `BEGIN ... ROLLBACK` block):

```powershell
psql $env:DATABASE_URL -f db/performance.sql
```

---

## 7. Running Tests

### Offline tests (ETL validation, anonymization, and web permission checks)
```powershell
pytest -v
```

### Full suite including PostgreSQL integration tests
Create a disposable test database, apply `db/schema.sql`, and set `TEST_DATABASE_URL`:

```powershell
createdb -U sir sir_test
psql "postgresql://sir:sir@localhost:5432/sir_test" -f db/schema.sql
$env:TEST_DATABASE_URL = "postgresql://sir:sir@localhost:5432/sir_test"
pytest -v
```

---

## 8. Privacy & Documentation

- Raw electoral-roll PDFs and generated CSVs (`data/extracted/*.csv`, `data/anonymized/*.csv`) are excluded via `.gitignore`. Never commit real voter PII.
- See [docs/design.md](docs/design.md) for the complete DFD-to-implementation mapping, ER diagram, normalization notes, transaction/trigger/role/index design, test outcomes, and limitations.
