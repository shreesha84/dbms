-- SIR (Special Intensive Revision) Management System
-- PostgreSQL 15+ schema, constraints, triggers, PL/pgSQL workflows, views, and roles.
-- Source of truth: Level 0, Level 1, and Level 2 Data Flow Diagrams (DFD).

-- ============================================================================
-- 1. Domain Types
-- ============================================================================

CREATE TYPE voter_status AS ENUM ('PENDING', 'VERIFIED', 'REJECTED', 'MIGRATED');
CREATE TYPE form_status  AS ENUM ('SUBMITTED', 'VALID', 'INVALID');
CREATE TYPE user_role    AS ENUM ('VOTER', 'BLO', 'ADMIN');

-- ============================================================================
-- 2. Core Tables (Mapped to DFD Data Stores D1-D4 + Supporting Tables)
-- ============================================================================

-- D3: Booth & BLO Records (Part = Polling Booth / Part, Section, BLO)
CREATE TABLE part (
    part_no               integer PRIMARY KEY,
    assembly_constituency integer NOT NULL,
    name                  text NOT NULL CHECK (length(trim(name)) > 0)
);

CREATE TABLE section (
    part_no      integer NOT NULL REFERENCES part(part_no),
    section_no   integer NOT NULL,
    section_name text NOT NULL CHECK (length(trim(section_name)) > 0),
    PRIMARY KEY (part_no, section_no)
);

CREATE TABLE blo (
    blo_id    bigserial PRIMARY KEY,
    username  text NOT NULL UNIQUE,
    full_name text NOT NULL CHECK (length(trim(full_name)) > 0),
    part_no   integer NOT NULL REFERENCES part(part_no),
    active    boolean NOT NULL DEFAULT true
);

-- D1: Voter Records
CREATE TABLE voter (
    epic            text PRIMARY KEY,
    part_no         integer NOT NULL,
    section_no      integer NOT NULL,
    serial_no       integer NOT NULL CHECK (serial_no > 0),
    name            text NOT NULL CHECK (length(trim(name)) > 0),
    relation_type   text NOT NULL CHECK (relation_type IN ('Fathers', 'Husbands', 'Mothers', 'Others')),
    relation_name   text NOT NULL DEFAULT '',
    house_no        text NOT NULL DEFAULT '',
    age             integer NOT NULL CHECK (age BETWEEN 18 AND 120),
    gender          text NOT NULL CHECK (gender IN ('M', 'F', 'T')),
    status          voter_status NOT NULL DEFAULT 'PENDING',
    assigned_blo_id bigint REFERENCES blo(blo_id),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (part_no, section_no) REFERENCES section(part_no, section_no),
    UNIQUE (part_no, serial_no)
);

CREATE INDEX idx_voter_part_section ON voter(part_no, section_no);
CREATE INDEX idx_voter_status       ON voter(status);
CREATE INDEX idx_voter_name         ON voter(name);
CREATE INDEX idx_voter_assigned_blo ON voter(assigned_blo_id);

-- D2: Enumeration Forms (Process 1.0 & 2.1)
CREATE TABLE enumeration_form (
    form_id      bigserial PRIMARY KEY,
    epic         text NOT NULL REFERENCES voter(epic),
    payload      jsonb NOT NULL,
    status       form_status NOT NULL DEFAULT 'SUBMITTED',
    error_reason text,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    validated_at timestamptz
);

CREATE INDEX idx_enumeration_form_epic ON enumeration_form(epic, submitted_at DESC);

-- Process 2.2 & 2.3: Field Verification Requests & BLO Reports
CREATE TABLE verification_request (
    request_id   bigserial PRIMARY KEY,
    form_id      bigint NOT NULL REFERENCES enumeration_form(form_id),
    epic         text NOT NULL REFERENCES voter(epic),
    blo_id       bigint NOT NULL REFERENCES blo(blo_id),
    status       text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'VERIFIED', 'REJECTED')),
    field_report text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE UNIQUE INDEX uq_verification_form ON verification_request(form_id);
CREATE INDEX idx_verification_blo_status ON verification_request(blo_id, status);

-- D4: Migration Records (Process 4.0 & 5.0)
CREATE TABLE migration_record (
    migration_id bigserial PRIMARY KEY,
    epic         text NOT NULL REFERENCES voter(epic),
    from_address text NOT NULL CHECK (length(trim(from_address)) > 0),
    to_address   text NOT NULL CHECK (length(trim(to_address)) > 0),
    reported_at  timestamptz NOT NULL DEFAULT now(),
    applied_at   timestamptz,
    CHECK (trim(from_address) <> trim(to_address))
);

CREATE UNIQUE INDEX uq_one_open_migration_per_voter
    ON migration_record(epic)
    WHERE applied_at IS NULL;

-- Process 5.0: SIR Configuration & Administrator Approval
CREATE TABLE sir_config (
    config_id   bigserial PRIMARY KEY,
    start_date  date NOT NULL,
    end_date    date NOT NULL CHECK (end_date >= start_date),
    approved    boolean NOT NULL DEFAULT false,
    approved_by text,
    approved_at timestamptz
);

-- Application Authentication & Role Linkage (Voter, BLO, SIR Administrator)
CREATE TABLE app_user (
    user_id       bigserial PRIMARY KEY,
    username      text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role          user_role NOT NULL,
    voter_epic    text UNIQUE REFERENCES voter(epic),
    blo_id        bigint UNIQUE REFERENCES blo(blo_id),
    CHECK (
        (role = 'VOTER' AND voter_epic IS NOT NULL AND blo_id IS NULL) OR
        (role = 'BLO'   AND blo_id IS NOT NULL     AND voter_epic IS NULL) OR
        (role = 'ADMIN' AND voter_epic IS NULL     AND blo_id IS NULL)
    )
);

-- Audit Trail for Voter Status Transitions
CREATE TABLE audit_log (
    audit_id   bigserial PRIMARY KEY,
    table_name text NOT NULL,
    record_key text NOT NULL,
    old_value  text,
    new_value  text,
    changed_at timestamptz NOT NULL DEFAULT now(),
    changed_by text NOT NULL DEFAULT current_user
);

CREATE INDEX idx_audit_log_record ON audit_log(table_name, record_key, changed_at DESC);

-- ============================================================================
-- 3. Triggers (Timestamps, Status Audit, Post-Migration Immutability Guard)
-- ============================================================================

CREATE FUNCTION touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER voter_touch
    BEFORE UPDATE ON voter
    FOR EACH ROW
    EXECUTE FUNCTION touch_updated_at();

CREATE FUNCTION audit_voter_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        INSERT INTO audit_log(table_name, record_key, old_value, new_value)
        VALUES ('voter', NEW.epic, OLD.status::text, NEW.status::text);
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER voter_status_audit
    AFTER UPDATE ON voter
    FOR EACH ROW
    EXECUTE FUNCTION audit_voter_status();

CREATE FUNCTION guard_applied_migration() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM migration_record
        WHERE epic = OLD.epic AND applied_at IS NOT NULL
    ) AND (
        NEW.part_no, NEW.section_no, NEW.serial_no, NEW.name,
        NEW.relation_type, NEW.relation_name, NEW.house_no,
        NEW.age, NEW.gender, NEW.status
    ) IS DISTINCT FROM (
        OLD.part_no, OLD.section_no, OLD.serial_no, OLD.name,
        OLD.relation_type, OLD.relation_name, OLD.house_no,
        OLD.age, OLD.gender, OLD.status
    )
    THEN
        RAISE EXCEPTION 'Voter % has an applied migration and cannot be edited', OLD.epic;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER voter_migration_guard
    BEFORE UPDATE ON voter
    FOR EACH ROW
    EXECUTE FUNCTION guard_applied_migration();

-- ============================================================================
-- 4. PL/pgSQL Workflow Functions (Mapped to DFD Processes 1.0 - 5.0)
-- ============================================================================

-- Process 1.0: Receive Enumeration Form -> store in D2 & return acknowledgement ID
CREATE FUNCTION fn_submit_form(p_epic text, p_payload jsonb) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    v_form_id bigint;
BEGIN
    INSERT INTO enumeration_form(epic, payload)
    VALUES (p_epic, p_payload)
    RETURNING form_id INTO v_form_id;

    RETURN v_form_id;
END;
$$;

-- Process 2.1: Validate Enumeration Form against D1 Voter Records
CREATE FUNCTION fn_validate_form(p_form bigint) RETURNS form_status
LANGUAGE plpgsql AS $$
DECLARE
    f       enumeration_form%ROWTYPE;
    v_name  text;
    v_age_t text;
    v_age   integer;
BEGIN
    SELECT * INTO STRICT f
    FROM enumeration_form
    WHERE form_id = p_form
    FOR UPDATE;

    -- Keep previously validated forms immutable on repeated calls.
    IF f.status <> 'SUBMITTED' THEN
        RETURN f.status;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM voter WHERE epic = f.epic) THEN
        UPDATE enumeration_form
        SET status = 'INVALID', error_reason = 'EPIC not found in voter records', validated_at = now()
        WHERE form_id = p_form;
        RETURN 'INVALID';
    END IF;

    IF EXISTS (SELECT 1 FROM migration_record WHERE epic = f.epic AND applied_at IS NOT NULL) THEN
        UPDATE enumeration_form
        SET status = 'INVALID', error_reason = 'Voter already has an applied migration', validated_at = now()
        WHERE form_id = p_form;
        RETURN 'INVALID';
    END IF;

    v_name  := trim(coalesce(f.payload->>'name', ''));
    v_age_t := trim(coalesce(f.payload->>'age', ''));

    IF v_name = '' THEN
        UPDATE enumeration_form
        SET status = 'INVALID', error_reason = 'Voter name is required', validated_at = now()
        WHERE form_id = p_form;
        RETURN 'INVALID';
    END IF;

    IF v_age_t !~ '^[0-9]{1,3}$' THEN
        UPDATE enumeration_form
        SET status = 'INVALID', error_reason = 'Age must be a whole number between 18 and 120', validated_at = now()
        WHERE form_id = p_form;
        RETURN 'INVALID';
    END IF;

    v_age := v_age_t::integer;
    IF v_age < 18 OR v_age > 120 THEN
        UPDATE enumeration_form
        SET status = 'INVALID', error_reason = 'Age must be between 18 and 120', validated_at = now()
        WHERE form_id = p_form;
        RETURN 'INVALID';
    END IF;

    UPDATE enumeration_form
    SET status = 'VALID', error_reason = NULL, validated_at = now()
    WHERE form_id = p_form;

    RETURN 'VALID';
END;
$$;

-- Process 2.2: Send Field Verification Request to assigned BLO
CREATE FUNCTION fn_send_verification_request(p_form bigint) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    v   voter%ROWTYPE;
    req bigint;
BEGIN
    SELECT * INTO STRICT v
    FROM voter
    WHERE epic = (SELECT epic FROM enumeration_form WHERE form_id = p_form);

    IF NOT EXISTS (SELECT 1 FROM enumeration_form WHERE form_id = p_form AND status = 'VALID') THEN
        RAISE EXCEPTION 'Enumeration form % must be VALID before field verification', p_form;
    END IF;

    IF v.assigned_blo_id IS NULL THEN
        RAISE EXCEPTION 'No BLO is assigned to voter %; administrator must allocate a BLO first', v.epic;
    END IF;

    INSERT INTO verification_request(form_id, epic, blo_id)
    VALUES (p_form, v.epic, v.assigned_blo_id)
    ON CONFLICT (form_id) DO UPDATE SET form_id = EXCLUDED.form_id
    RETURNING request_id INTO req;

    RETURN req;
END;
$$;

-- Process 2.3: Update Verification Status in D1 based on BLO Field Report
CREATE FUNCTION fn_record_verification(p_request bigint, p_ok boolean, p_report text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    r verification_request%ROWTYPE;
    f enumeration_form%ROWTYPE;
BEGIN
    IF length(trim(coalesce(p_report, ''))) = 0 THEN
        RAISE EXCEPTION 'Field verification report is required';
    END IF;

    SELECT * INTO STRICT r
    FROM verification_request
    WHERE request_id = p_request
    FOR UPDATE;

    IF r.status <> 'OPEN' THEN
        RAISE EXCEPTION 'Verification request % is already %', p_request, r.status;
    END IF;

    SELECT * INTO STRICT f
    FROM enumeration_form
    WHERE form_id = r.form_id;

    UPDATE verification_request
    SET status       = CASE WHEN p_ok THEN 'VERIFIED' ELSE 'REJECTED' END,
        field_report = trim(p_report),
        completed_at = now()
    WHERE request_id = p_request;

    IF p_ok THEN
        UPDATE voter
        SET name     = COALESCE(NULLIF(trim(f.payload->>'name'), ''), name),
            age      = COALESCE(NULLIF(trim(f.payload->>'age'), '')::integer, age),
            house_no = COALESCE(NULLIF(trim(f.payload->>'house_no'), ''), house_no),
            status   = 'VERIFIED'
        WHERE epic = r.epic;
    ELSE
        UPDATE voter
        SET status = 'REJECTED'
        WHERE epic = r.epic;
    END IF;
END;
$$;

-- Process 3.0: Manage Booth Allocation (assign voters in a part to an active BLO)
CREATE FUNCTION fn_assign_booth_and_blo(p_part integer, p_blo bigint) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    b blo%ROWTYPE;
    n integer;
BEGIN
    SELECT * INTO b FROM blo WHERE blo_id = p_blo;
    IF NOT FOUND OR NOT b.active THEN
        RAISE EXCEPTION 'Active BLO % not found', p_blo;
    END IF;

    IF b.part_no <> p_part THEN
        RAISE EXCEPTION 'BLO % is registered for part %, not part %', p_blo, b.part_no, p_part;
    END IF;

    UPDATE voter
    SET assigned_blo_id = p_blo
    WHERE part_no = p_part;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;

-- Process 4.0: Record Migration in D4
CREATE FUNCTION fn_record_migration(p_epic text, p_from text, p_to text) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    v_from text := trim(coalesce(p_from, ''));
    v_to   text := trim(coalesce(p_to, ''));
    v_id   bigint;
BEGIN
    IF v_from = '' OR v_to = '' THEN
        RAISE EXCEPTION 'Both current and new addresses are required';
    END IF;

    IF v_from = v_to THEN
        RAISE EXCEPTION 'New address must differ from current address';
    END IF;

    IF EXISTS (SELECT 1 FROM migration_record WHERE epic = p_epic AND applied_at IS NOT NULL)
       OR EXISTS (SELECT 1 FROM voter WHERE epic = p_epic AND status = 'MIGRATED')
    THEN
        RAISE EXCEPTION 'Voter % already has an applied migration', p_epic;
    END IF;

    INSERT INTO migration_record(epic, from_address, to_address)
    VALUES (p_epic, v_from, v_to)
    RETURNING migration_id INTO v_id;

    RETURN v_id;
END;
$$;

-- Process 5.0: Update Electoral Roll (atomic update within caller transaction)
CREATE FUNCTION fn_apply_roll_update() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM sir_config
        WHERE approved
          AND current_date BETWEEN start_date AND end_date
    ) THEN
        RAISE EXCEPTION 'No approved SIR configuration covers today (%)', current_date;
    END IF;

    WITH updated_voters AS (
        UPDATE voter v
        SET house_no = m.to_address,
            status   = 'MIGRATED'
        FROM migration_record m
        WHERE m.epic = v.epic
          AND m.applied_at IS NULL
          AND v.status = 'VERIFIED'
        RETURNING m.migration_id
    ),
    marked_migrations AS (
        UPDATE migration_record m
        SET applied_at = now()
        FROM updated_voters u
        WHERE m.migration_id = u.migration_id
        RETURNING m.migration_id
    )
    SELECT count(*) INTO n FROM marked_migrations;

    RETURN n;
END;
$$;

-- ============================================================================
-- 5. Views (Mapped to DFD Process 3.0 Booth Details & Process 6.0 SIR Reports)
-- ============================================================================

-- Process 3.0 -> BLO: Assigned Voter List & Booth Details
CREATE VIEW v_blo_assigned_voters AS
SELECT
    b.blo_id,
    b.full_name AS blo_name,
    p.part_no,
    p.assembly_constituency,
    p.name      AS booth_name,
    s.section_no,
    s.section_name,
    v.epic,
    v.serial_no,
    v.name,
    v.house_no,
    v.age,
    v.gender,
    v.status
FROM voter v
JOIN blo b     ON b.blo_id = v.assigned_blo_id
JOIN part p    ON p.part_no = v.part_no
JOIN section s ON s.part_no = v.part_no AND s.section_no = v.section_no;

-- Process 6.0: Part-level demographic summary
CREATE VIEW part_summary AS
SELECT
    p.part_no,
    p.assembly_constituency,
    p.name AS booth_name,
    count(v.epic)                                AS electors,
    count(v.epic) FILTER (WHERE v.gender = 'M')  AS male,
    count(v.epic) FILTER (WHERE v.gender = 'F')  AS female,
    count(v.epic) FILTER (WHERE v.gender = 'T')  AS third_gender
FROM part p
LEFT JOIN voter v ON v.part_no = p.part_no
GROUP BY p.part_no, p.assembly_constituency, p.name;

-- Process 6.0: Section-level verification progress
CREATE VIEW section_summary AS
SELECT
    s.part_no,
    s.section_no,
    s.section_name,
    count(v.epic)                                  AS electors,
    count(v.epic) FILTER (WHERE v.status = 'PENDING')  AS pending,
    count(v.epic) FILTER (WHERE v.status = 'VERIFIED') AS verified,
    count(v.epic) FILTER (WHERE v.status = 'REJECTED') AS rejected,
    count(v.epic) FILTER (WHERE v.status = 'MIGRATED') AS migrated
FROM section s
LEFT JOIN voter v ON v.part_no = s.part_no AND v.section_no = s.section_no
GROUP BY s.part_no, s.section_no, s.section_name;

-- Process 6.0: Overall status distribution
CREATE VIEW status_summary AS
SELECT
    status,
    count(*) AS electors
FROM voter
GROUP BY status;

-- Process 6.0: Booth & BLO workload statistics (D3 -> 6.0)
CREATE VIEW pending_by_blo AS
SELECT
    b.blo_id,
    b.full_name,
    b.part_no,
    count(v.epic)                                  AS total_assigned,
    count(v.epic) FILTER (WHERE v.status = 'PENDING')  AS pending,
    count(v.epic) FILTER (WHERE v.status = 'VERIFIED') AS verified
FROM blo b
LEFT JOIN voter v ON b.blo_id = v.assigned_blo_id
GROUP BY b.blo_id, b.full_name, b.part_no;

-- ============================================================================
-- 6. Database Roles & Least-Privilege Grants (Coursework Level)
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'voter_role') THEN
        CREATE ROLE voter_role NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'blo_role') THEN
        CREATE ROLE blo_role NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admin_role') THEN
        CREATE ROLE admin_role NOLOGIN;
    END IF;
    -- Allow the schema owner (e.g. sir with CREATEROLE) to SET LOCAL ROLE in tests.
    EXECUTE format('GRANT voter_role, blo_role, admin_role TO %I', current_user);
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

-- Voter role: read own roll context and invoke voter submission/migration workflows
GRANT SELECT ON voter, part, section, enumeration_form, migration_record, verification_request TO voter_role;
GRANT INSERT, UPDATE ON enumeration_form, verification_request TO voter_role;
GRANT INSERT ON migration_record TO voter_role;
GRANT USAGE, SELECT ON SEQUENCE enumeration_form_form_id_seq,
                                migration_record_migration_id_seq,
                                verification_request_request_id_seq TO voter_role;
GRANT EXECUTE ON FUNCTION
    fn_submit_form(text, jsonb),
    fn_validate_form(bigint),
    fn_send_verification_request(bigint),
    fn_record_migration(text, text, text)
TO voter_role;

-- BLO role: read assigned voters/requests and submit field verification outcomes
GRANT SELECT ON v_blo_assigned_voters, verification_request, enumeration_form, voter, migration_record, part, section TO blo_role;
GRANT UPDATE ON verification_request, voter TO blo_role;
GRANT INSERT ON audit_log TO blo_role;
GRANT USAGE, SELECT ON SEQUENCE audit_log_audit_id_seq TO blo_role;
GRANT EXECUTE ON FUNCTION fn_record_verification(bigint, boolean, text) TO blo_role;

-- Admin role: full access to schema objects and administrative workflows
GRANT ALL ON ALL TABLES IN SCHEMA public TO admin_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO admin_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO admin_role;
