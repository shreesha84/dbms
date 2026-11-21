"""Web-layer permission checks and PostgreSQL workflow integration tests.

Set TEST_DATABASE_URL to a disposable PostgreSQL database with db/schema.sql applied
to run the database integration tests.
"""
import os
from types import SimpleNamespace

import psycopg
import pytest
from starlette.requests import Request

from app import HTTPException, apply_roll_update, dashboard, home, require

DSN = os.getenv("TEST_DATABASE_URL")
dbtest = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


# ---------------------------------------------------------------------------
# Web-layer auth & role tests (run without PostgreSQL)
# ---------------------------------------------------------------------------

def test_role_denial_in_web_layer():
    request = SimpleNamespace(session={"user": {"role": "VOTER"}})
    with pytest.raises(HTTPException) as exc:
        require(request, "ADMIN")
    assert exc.value.status_code == 403


def test_unauthenticated_require_raises_401():
    request = SimpleNamespace(session={})
    with pytest.raises(HTTPException) as exc:
        require(request, "VOTER")
    assert exc.value.status_code == 401


def _fake_request(path: str = "/", session: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "session": session if session is not None else {},
    }
    return Request(scope)


def test_login_page_and_protected_routes():
    resp = home(_fake_request("/"))
    assert resp.status_code == 200
    assert "SIR Management System" in resp.body.decode("utf-8")

    dash = dashboard(_fake_request("/dashboard"))
    assert dash.status_code == 303
    assert dash.headers["location"] == "/"

    with pytest.raises(HTTPException) as exc:
        apply_roll_update(_fake_request("/admin/apply"))
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        dashboard(_fake_request("/dashboard", session={"user": {"role": "UNKNOWN"}}))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# PostgreSQL integration tests (require TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

@dbtest
def test_validation_audit_triggers_and_invalid_forms():
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO part VALUES (101, 174, 'Test Booth') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO section VALUES (101, 1, 'Test Section') ON CONFLICT DO NOTHING")
        conn.execute(
            "INSERT INTO blo(username, full_name, part_no) VALUES ('test-blo', 'Test BLO', 101) "
            "ON CONFLICT (username) DO NOTHING"
        )
        epic = "TST0000001"
        conn.execute(
            "INSERT INTO voter(epic, part_no, section_no, serial_no, name, relation_type, age, gender) "
            "VALUES (%s, 101, 1, 1, 'Initial Name', 'Others', 30, 'M') ON CONFLICT DO NOTHING",
            (epic,),
        )

        # Audit trigger records status change
        conn.execute("UPDATE voter SET status = 'VERIFIED' WHERE epic = %s", (epic,))
        audit_count = conn.execute(
            "SELECT count(*) FROM audit_log WHERE table_name = 'voter' AND record_key = %s "
            "AND old_value = 'PENDING' AND new_value = 'VERIFIED'",
            (epic,),
        ).fetchone()[0]
        assert audit_count >= 1

        # Valid enumeration form (including 3-digit age 100)
        f_ok = conn.execute(
            "SELECT fn_submit_form(%s, %s::jsonb)",
            (epic, '{"name":"Valid Centenarian","age":100}'),
        ).fetchone()[0]
        assert conn.execute("SELECT fn_validate_form(%s)", (f_ok,)).fetchone()[0] == "VALID"

        # Invalid enumeration forms: underage, over-max, non-numeric, blank name
        for bad_payload in (
            '{"name":"Too Young","age":17}',
            '{"name":"Too Old","age":125}',
            '{"name":"Bad Age","age":"abc"}',
            '{"name":"","age":30}',
        ):
            f_bad = conn.execute(
                "SELECT fn_submit_form(%s, %s::jsonb)", (epic, bad_payload)
            ).fetchone()[0]
            assert conn.execute("SELECT fn_validate_form(%s)", (f_bad,)).fetchone()[0] == "INVALID"

        conn.rollback()


@dbtest
def test_end_to_end_dfd_workflow_and_post_migration_guard():
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO part VALUES (102, 174, 'Workflow Booth') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO section VALUES (102, 1, 'Workflow Section') ON CONFLICT DO NOTHING")
        blo_id = conn.execute(
            "INSERT INTO blo(username, full_name, part_no) VALUES ('workflow-blo', 'Workflow BLO', 102) "
            "ON CONFLICT (username) DO UPDATE SET active = true RETURNING blo_id"
        ).fetchone()[0]
        epic = "WRK0000001"
        conn.execute(
            "INSERT INTO voter(epic, part_no, section_no, serial_no, name, relation_type, house_no, age, gender) "
            "VALUES (%s, 102, 1, 1, 'Workflow Voter', 'Others', 'Old-1', 30, 'F') ON CONFLICT DO NOTHING",
            (epic,),
        )

        # 3.0 Manage Booth Allocation
        assert conn.execute("SELECT fn_assign_booth_and_blo(102, %s)", (blo_id,)).fetchone()[0] >= 1
        assert (
            conn.execute(
                "SELECT count(*) FROM v_blo_assigned_voters WHERE blo_id = %s AND epic = %s",
                (blo_id, epic),
            ).fetchone()[0]
            == 1
        )

        # 1.0 -> 2.1 -> 2.2 -> 2.3 Verification flow
        form_id = conn.execute(
            "SELECT fn_submit_form(%s, %s::jsonb)",
            (epic, '{"name":"Workflow Voter","age":31,"house_no":"Old-1"}'),
        ).fetchone()[0]
        assert conn.execute("SELECT fn_validate_form(%s)", (form_id,)).fetchone()[0] == "VALID"
        req_id = conn.execute("SELECT fn_send_verification_request(%s)", (form_id,)).fetchone()[0]
        conn.execute("SELECT fn_record_verification(%s, true, 'Verified at doorstep')", (req_id,))
        assert conn.execute("SELECT status FROM voter WHERE epic = %s", (epic,)).fetchone()[0] == "VERIFIED"

        # 4.0 Record Migration -> 5.0 Apply Roll Update
        conn.execute("SELECT fn_record_migration(%s, 'Old-1', 'New-99')", (epic,))
        conn.execute(
            "INSERT INTO sir_config(start_date, end_date, approved, approved_by, approved_at) "
            "VALUES (current_date, current_date, true, 'admin', now())"
        )
        assert conn.execute("SELECT fn_apply_roll_update()").fetchone()[0] >= 1
        assert conn.execute("SELECT status FROM voter WHERE epic = %s", (epic,)).fetchone()[0] == "MIGRATED"
        assert conn.execute("SELECT house_no FROM voter WHERE epic = %s", (epic,)).fetchone()[0] == "New-99"

        # Re-calling fn_validate_form on an already-VALID form stays immutable
        assert conn.execute("SELECT fn_validate_form(%s)", (form_id,)).fetchone()[0] == "VALID"

        # Already-migrated voter cannot record a second migration
        with pytest.raises(psycopg.Error):
            with conn.transaction():
                conn.execute("SELECT fn_record_migration(%s, 'New-99', 'Another-100')", (epic,))

        # Post-migration immutability trigger blocks editing core voter details and status
        with pytest.raises(psycopg.Error):
            with conn.transaction():
                conn.execute("UPDATE voter SET name = 'Tampered' WHERE epic = %s", (epic,))
        with pytest.raises(psycopg.Error):
            with conn.transaction():
                conn.execute("UPDATE voter SET status = 'REJECTED' WHERE epic = %s", (epic,))

        conn.rollback()


@dbtest
def test_transaction_rollback_without_approved_sir_config():
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO part VALUES (103, 174, 'Rollback Booth') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO section VALUES (103, 1, 'Rollback Section') ON CONFLICT DO NOTHING")
        epic = "RBK0000001"
        conn.execute(
            "INSERT INTO voter(epic, part_no, section_no, serial_no, name, relation_type, house_no, age, gender, status) "
            "VALUES (%s, 103, 1, 1, 'Rollback Voter', 'Others', 'A-1', 28, 'M', 'VERIFIED') ON CONFLICT DO NOTHING",
            (epic,),
        )
        conn.execute("SELECT fn_record_migration(%s, 'A-1', 'B-2')", (epic,))
        conn.execute("DELETE FROM sir_config")

        # Without approved SIR config, fn_apply_roll_update must raise and leave state unchanged
        with pytest.raises(psycopg.Error):
            with conn.transaction():
                conn.execute("SELECT fn_apply_roll_update()")

        assert conn.execute("SELECT status FROM voter WHERE epic = %s", (epic,)).fetchone()[0] == "VERIFIED"
        assert (
            conn.execute(
                "SELECT applied_at FROM migration_record WHERE epic = %s", (epic,)
            ).fetchone()[0]
            is None
        )
        conn.rollback()


@dbtest
def test_database_role_permissions():
    with psycopg.connect(DSN) as conn:
        # Prepare fixture data as owner before testing role switching
        conn.execute("INSERT INTO part VALUES (104, 174, 'Role Booth') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO section VALUES (104, 1, 'Role Section') ON CONFLICT DO NOTHING")
        blo_id = conn.execute(
            "INSERT INTO blo(username, full_name, part_no) VALUES ('role-blo', 'Role BLO', 104) "
            "ON CONFLICT (username) DO UPDATE SET active = true RETURNING blo_id"
        ).fetchone()[0]
        epic = "ROL0000001"
        conn.execute(
            "INSERT INTO voter(epic, part_no, section_no, serial_no, name, relation_type, house_no, age, gender, assigned_blo_id) "
            "VALUES (%s, 104, 1, 1, 'Role Voter', 'Others', 'R-1', 29, 'F', %s) ON CONFLICT DO NOTHING",
            (epic, blo_id),
        )

        # voter_role: allowed to submit, validate, queue verification, and record migration
        with conn.transaction():
            conn.execute("SET LOCAL ROLE voter_role")
            f_id = conn.execute(
                "SELECT fn_submit_form(%s, %s::jsonb)",
                (epic, '{"name":"Role Voter Updated","age":30,"house_no":"R-2"}'),
            ).fetchone()[0]
            assert conn.execute("SELECT fn_validate_form(%s)", (f_id,)).fetchone()[0] == "VALID"
            req_id = conn.execute("SELECT fn_send_verification_request(%s)", (f_id,)).fetchone()[0]
            mig_id = conn.execute("SELECT fn_record_migration(%s, 'R-2', 'R-3')", (epic,)).fetchone()[0]
            assert req_id > 0 and mig_id > 0
            conn.execute("RESET ROLE")

        # blo_role: allowed to record verification (updates voter + inserts audit_log + checks migration_record)
        with conn.transaction():
            conn.execute("SET LOCAL ROLE blo_role")
            conn.execute("SELECT fn_record_verification(%s, true, 'Verified by role-blo')", (req_id,))
            conn.execute("RESET ROLE")

        # voter_role must not be allowed to delete voters or apply roll updates
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SET LOCAL ROLE voter_role")
                conn.execute("DELETE FROM voter")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SET LOCAL ROLE voter_role")
                conn.execute("SELECT fn_apply_roll_update()")

        # blo_role must not be allowed to apply roll updates
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SET LOCAL ROLE blo_role")
                conn.execute("SELECT fn_apply_roll_update()")

        conn.rollback()
