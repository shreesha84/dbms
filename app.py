"""FastAPI + Jinja2 web interface for the SIR Management System.

All database access uses visible, parameterized SQL via psycopg3.
No ORM or JavaScript framework is used.
"""
from __future__ import annotations

import csv
import os
from contextlib import contextmanager
from io import StringIO

import psycopg
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from starlette.middleware.sessions import SessionMiddleware

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://sir:sir@localhost:5432/sir")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="SIR Management System")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-change-me-for-local-coursework"),
)
templates = Jinja2Templates(directory="templates")


@contextmanager
def db():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def current(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in")
    return user


def require(request: Request, role: str) -> dict:
    user = current(request)
    if user["role"] != role:
        raise HTTPException(status_code=403, detail="Role permission denied")
    return user


def set_flash(request: Request, category: str, text: str) -> None:
    request.session["flash"] = {"category": category, "text": text}


def pop_flash(request: Request) -> dict | None:
    return request.session.pop("flash", None)


def _pg_message(exc: psycopg.Error) -> str:
    diag = getattr(exc, "diag", None)
    if diag and diag.message_primary:
        return diag.message_primary
    return str(exc).splitlines()[0]


@app.exception_handler(psycopg.OperationalError)
def db_connection_error(request: Request, exc: psycopg.OperationalError):
    return templates.TemplateResponse(
        request,
        "message.html",
        {
            "title": "Database Connection Error",
            "category": "danger",
            "message": (
                f"Could not connect to PostgreSQL ({_pg_message(exc)}). "
                "Ensure the PostgreSQL service is running, DATABASE_URL is set correctly, "
                "and db/schema.sql has been applied."
            ),
            "back_url": "/",
            "back_label": "Back to sign in",
        },
        status_code=503,
    )


@app.exception_handler(psycopg.Error)
def db_query_error(request: Request, exc: psycopg.Error):
    return templates.TemplateResponse(
        request,
        "message.html",
        {
            "title": "Database Error",
            "category": "danger",
            "message": (
                f"Database error: {_pg_message(exc)}. "
                "Verify that db/schema.sql, etl/load_roll.py, and seed.py have been run."
            ),
            "back_url": "/",
            "back_label": "Back to sign in",
        },
        status_code=500,
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"title": "Sign in — SIR Management", "flash": pop_flash(request)},
    )


@app.get("/voter/status", response_class=HTMLResponse)
def voter_status(request: Request, epic: str):
    cleaned = epic.strip().upper()
    with db() as conn:
        voter = conn.execute(
            """
            SELECT v.epic, v.name, v.status, v.part_no, v.section_no, v.updated_at,
                   p.name AS booth_name
            FROM voter v
            JOIN part p ON p.part_no = v.part_no
            WHERE v.epic = %s
            """,
            (cleaned,),
        ).fetchone()

    if not voter:
        return templates.TemplateResponse(
            request,
            "message.html",
            {
                "title": "Voter status not found",
                "category": "warning",
                "message": f"No voter record was found for EPIC '{cleaned}'.",
                "back_url": "/",
                "back_label": "Back to sign in",
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "message.html",
        {
            "title": f"Voter status — {voter['epic']}",
            "category": "info",
            "message": (
                f"{voter['name']} ({voter['epic']}) — Status: {voter['status']} | "
                f"Part {voter['part_no']} ({voter['booth_name']}), Section {voter['section_no']} | "
                f"Last updated: {voter['updated_at']}"
            ),
            "back_url": "/",
            "back_label": "Back to sign in",
        },
    )


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with db() as conn:
        user = conn.execute(
            """
            SELECT user_id, username, password_hash, role, voter_epic, blo_id
            FROM app_user
            WHERE username = %s
            """,
            (username.strip(),),
        ).fetchone()

    if not user or not pwd.verify(password, user["password_hash"]):
        set_flash(request, "danger", "Invalid username or password.")
        return RedirectResponse("/", status_code=303)

    request.session["user"] = {
        key: user[key] for key in ("user_id", "username", "role", "voter_epic", "blo_id")
    }
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/", status_code=303)
    flash = pop_flash(request)

    if user["role"] == "VOTER":
        with db() as conn:
            voter = conn.execute(
                """
                SELECT v.*, p.name AS booth_name, s.section_name, b.full_name AS blo_name
                FROM voter v
                JOIN part p ON p.part_no = v.part_no
                JOIN section s ON s.part_no = v.part_no AND s.section_no = v.section_no
                LEFT JOIN blo b ON b.blo_id = v.assigned_blo_id
                WHERE v.epic = %s
                """,
                (user["voter_epic"],),
            ).fetchone()
            forms = conn.execute(
                """
                SELECT form_id, status, error_reason, payload, submitted_at, validated_at
                FROM enumeration_form
                WHERE epic = %s
                ORDER BY submitted_at DESC
                LIMIT 10
                """,
                (user["voter_epic"],),
            ).fetchall()
            migrations = conn.execute(
                """
                SELECT migration_id, from_address, to_address, reported_at, applied_at
                FROM migration_record
                WHERE epic = %s
                ORDER BY reported_at DESC
                LIMIT 10
                """,
                (user["voter_epic"],),
            ).fetchall()

        return templates.TemplateResponse(
            request,
            "voter.html",
            {
                "title": "Voter Dashboard — SIR Management",
                "user": user,
                "voter": voter,
                "forms": forms,
                "migrations": migrations,
                "flash": flash,
            },
        )

    if user["role"] == "BLO":
        with db() as conn:
            blo_info = conn.execute(
                """
                SELECT b.blo_id, b.username, b.full_name, b.part_no,
                       p.assembly_constituency, p.name AS booth_name
                FROM blo b
                JOIN part p ON p.part_no = b.part_no
                WHERE b.blo_id = %s
                """,
                (user["blo_id"],),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT *
                FROM v_blo_assigned_voters
                WHERE blo_id = %s
                ORDER BY serial_no
                """,
                (user["blo_id"],),
            ).fetchall()
            reqs = conn.execute(
                """
                SELECT vr.request_id, vr.form_id, vr.epic, vr.status, vr.created_at,
                       ef.payload, v.name AS current_name, v.house_no AS current_house_no
                FROM verification_request vr
                JOIN enumeration_form ef ON ef.form_id = vr.form_id
                JOIN voter v ON v.epic = vr.epic
                WHERE vr.blo_id = %s AND vr.status = 'OPEN'
                ORDER BY vr.created_at
                """,
                (user["blo_id"],),
            ).fetchall()

        return templates.TemplateResponse(
            request,
            "blo.html",
            {
                "title": "BLO Dashboard — SIR Management",
                "user": user,
                "blo": blo_info,
                "rows": rows,
                "requests": reqs,
                "flash": flash,
            },
        )

    with db() as conn:
        sums = conn.execute("SELECT * FROM part_summary ORDER BY part_no").fetchall()
        statuses = conn.execute("SELECT * FROM status_summary ORDER BY status").fetchall()
        sections = conn.execute(
            "SELECT * FROM section_summary ORDER BY part_no, section_no"
        ).fetchall()
        pending = conn.execute("SELECT * FROM pending_by_blo ORDER BY blo_id").fetchall()
        blos = conn.execute(
            "SELECT blo_id, full_name, part_no FROM blo WHERE active ORDER BY part_no, full_name"
        ).fetchall()
        configs = conn.execute(
            """
            SELECT config_id, start_date, end_date, approved, approved_by, approved_at
            FROM sir_config
            ORDER BY config_id DESC
            LIMIT 10
            """
        ).fetchall()

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "title": "Administrator Dashboard — SIR Management",
            "user": user,
            "sums": sums,
            "statuses": statuses,
            "sections": sections,
            "pending": pending,
            "blos": blos,
            "configs": configs,
            "flash": flash,
        },
    )


# Process 1.0: Receive Enumeration Form
@app.post("/voter/form")
def submit_form(
    request: Request,
    name: str = Form(""),
    age: str = Form(""),
    house_no: str = Form(""),
):
    user = require(request, "VOTER")
    payload = {"name": name.strip(), "age": age.strip(), "house_no": house_no.strip()}
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT fn_submit_form(%s, %s::jsonb) AS form_id",
                (user["voter_epic"], Jsonb(payload)),
            ).fetchone()
    except psycopg.Error as exc:
        set_flash(request, "danger", f"Could not submit enumeration form: {_pg_message(exc)}")
        return RedirectResponse("/dashboard", status_code=303)

    return RedirectResponse(f"/voter/form/{row['form_id']}", status_code=303)


# Process 2.1 (Validate) + Process 2.2 (Send Field Verification Request)
@app.get("/voter/form/{form_id}", response_class=HTMLResponse)
def validate_view(request: Request, form_id: int):
    user = require(request, "VOTER")
    with db() as conn:
        form_row = conn.execute(
            "SELECT form_id, epic FROM enumeration_form WHERE form_id = %s",
            (form_id,),
        ).fetchone()
        if not form_row or form_row["epic"] != user["voter_epic"]:
            raise HTTPException(status_code=404, detail="Enumeration form not found")

        status = conn.execute(
            "SELECT fn_validate_form(%s) AS status", (form_id,)
        ).fetchone()["status"]
        reason = conn.execute(
            "SELECT error_reason FROM enumeration_form WHERE form_id = %s", (form_id,)
        ).fetchone()["error_reason"]

        if status == "VALID":
            try:
                req_id = conn.execute(
                    "SELECT fn_send_verification_request(%s) AS request_id", (form_id,)
                ).fetchone()["request_id"]
                msg = (
                    f"Acknowledgement: Form #{form_id} passed validation and was sent to your "
                    f"assigned BLO (Verification Request #{req_id})."
                )
                category = "success"
            except psycopg.Error as exc:
                msg = (
                    f"Form #{form_id} is VALID, but could not be queued for BLO verification: "
                    f"{_pg_message(exc)}"
                )
                category = "warning"
        else:
            msg = (
                f"Form #{form_id} is INVALID: {reason or 'Please correct the details and resubmit.'}"
            )
            category = "danger"

    return templates.TemplateResponse(
        request,
        "message.html",
        {
            "title": f"Enumeration Form #{form_id} — {status}",
            "category": category,
            "message": msg,
            "back_url": "/dashboard",
            "back_label": "Back to dashboard",
        },
    )


# Process 4.0: Record Migration
@app.post("/voter/migration")
def migration(
    request: Request,
    from_address: str = Form(...),
    to_address: str = Form(...),
):
    user = require(request, "VOTER")
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT fn_record_migration(%s, %s, %s) AS migration_id",
                (user["voter_epic"], from_address, to_address),
            ).fetchone()
        set_flash(
            request,
            "success",
            f"Migration request #{row['migration_id']} recorded. It will be applied during the approved SIR roll update.",
        )
    except psycopg.Error as exc:
        set_flash(request, "danger", f"Could not record migration: {_pg_message(exc)}")
    return RedirectResponse("/dashboard", status_code=303)


# Process 2.3: BLO submits verification result & field report
@app.post("/blo/verification/{request_id}")
def verify(
    request: Request,
    request_id: int,
    verified: bool = Form(...),
    field_report: str = Form(...),
):
    user = require(request, "BLO")
    try:
        with db() as conn:
            owner = conn.execute(
                "SELECT blo_id FROM verification_request WHERE request_id = %s",
                (request_id,),
            ).fetchone()
            if not owner or owner["blo_id"] != user["blo_id"]:
                raise HTTPException(status_code=403, detail="Verification request is not assigned to you")

            conn.execute(
                "SELECT fn_record_verification(%s, %s, %s)",
                (request_id, verified, field_report),
            )
        outcome = "VERIFIED" if verified else "REJECTED"
        set_flash(request, "success", f"Verification request #{request_id} marked {outcome}.")
    except psycopg.Error as exc:
        set_flash(request, "danger", f"Verification update failed: {_pg_message(exc)}")
    return RedirectResponse("/dashboard", status_code=303)


# Process 3.0: Manage Booth & BLO Allocation
@app.post("/admin/assign")
def assign_blo(request: Request, part_no: int = Form(...), blo_id: int = Form(...)):
    require(request, "ADMIN")
    try:
        with db() as conn:
            count = conn.execute(
                "SELECT fn_assign_booth_and_blo(%s, %s) AS n",
                (part_no, blo_id),
            ).fetchone()["n"]
        set_flash(request, "success", f"Assigned {count} voter(s) in Part {part_no} to BLO #{blo_id}.")
    except psycopg.Error as exc:
        set_flash(request, "danger", f"Booth/BLO assignment failed: {_pg_message(exc)}")
    return RedirectResponse("/dashboard", status_code=303)


# Process 5.0: SIR Configuration & Approval
@app.post("/admin/config")
def config(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    approve: bool = Form(False),
):
    user = require(request, "ADMIN")
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO sir_config(start_date, end_date, approved, approved_by, approved_at)
                VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END)
                """,
                (start_date, end_date, approve, user["username"] if approve else None, approve),
            )
        state = "approved" if approve else "saved (unapproved)"
        set_flash(request, "success", f"SIR window {start_date} to {end_date} {state}.")
    except psycopg.Error as exc:
        set_flash(request, "danger", f"Could not save SIR configuration: {_pg_message(exc)}")
    return RedirectResponse("/dashboard", status_code=303)


# Process 5.0: Apply Electoral Roll Update
@app.post("/admin/apply")
def apply_roll_update(request: Request):
    require(request, "ADMIN")
    try:
        with db() as conn:
            count = conn.execute("SELECT fn_apply_roll_update() AS n").fetchone()["n"]
        set_flash(
            request,
            "success",
            f"Electoral roll update completed cleanly: {count} verified migration(s) applied.",
        )
    except psycopg.Error as exc:
        set_flash(request, "danger", f"Roll update blocked (transaction rolled back): {_pg_message(exc)}")
    return RedirectResponse("/dashboard", status_code=303)


# Process 6.0: Export SIR Summary Report (CSV)
@app.get("/admin/report.csv")
def report(request: Request):
    require(request, "ADMIN")
    with db() as conn:
        rows = conn.execute("SELECT * FROM part_summary ORDER BY part_no").fetchall()

    buf = StringIO()
    fieldnames = (
        list(rows[0].keys())
        if rows
        else ["part_no", "assembly_constituency", "booth_name", "electors", "male", "female", "third_gender"]
    )
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sir-report.csv"},
    )
