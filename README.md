# SIR Management System

College DBMS project implementing electoral-roll enumeration, verification, booth/BLO allocation, migration, roll updates, and reports with PostgreSQL 15, FastAPI, Jinja2, and visible parameterized SQL.

## Run locally

1. Install PostgreSQL 15+ and Python 3.11, then run `python -m pip install -r requirements.txt`.
2. Create database/user `sir` (or set `DATABASE_URL`) and apply `psql "$env:DATABASE_URL" -f db/schema.sql` (PowerShell example: `psql $env:DATABASE_URL -f db/schema.sql`).
3. Extract the supplied electoral-roll PDF: `python etl/extract_roll.py .\2026-EROLLGEN-S10-174-SIR-DraftRoll-Revision1-ENG-271-WI.pdf`.
4. The extractor validates 494 voters, M/F/T counts, serial continuity and unique EPICs before it writes. Anonymize the resulting CSV for a demo: `python etl/anonymize.py data/extracted/voters.csv data/anonymized/voters.csv`.
5. Load it: `python etl/load_roll.py data/anonymized/voters.csv`; then `python seed.py`.
6. Start the UI with `uvicorn app:app --reload`; open http://localhost:8000.

Docker: `docker compose up --build`. This initializes schema and demo users but does not extract or load raw electoral data; mount/load anonymized CSV separately. Default demo credentials: admin/admin123, blo1/blo1123, blo2/blo2123, and voter1..voter10/voter123 (requires voter CSV load).

## Privacy and tests

Raw PDF is ignored by git, as are generated CSVs. Never commit identifiable rolls. `etl/anonymize.py` changes names, relation names, house numbers, and EPICs while preserving counts, ages, gender, serials, and EPIC format/uniqueness. Run `pytest`; database integration checks skip unless `TEST_DATABASE_URL` points to a disposable DB with the schema.

## Database design and mapping

See [docs/design.md](docs/design.md) for DFD mapping, Mermaid ERD, normalization, role/trigger/index discussion, tests and limitations. `demo.sql` gives SQL examples; run app forms to obtain actual IDs and continue the lifecycle.
