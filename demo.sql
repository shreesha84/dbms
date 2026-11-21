-- SIR Management System: End-to-End SQL Walkthrough
-- Run after applying db/schema.sql, loading anonymized voters, and running seed.py
-- against a disposable demo database.

DO $$
DECLARE
    v_epic       text;
    v_form       bigint;
    v_req        bigint;
    v_bad        bigint;
    v_bad_status form_status;
    v_applied    integer;
BEGIN
    -- Pick a seeded, non-migrated voter who already has an assigned BLO
    SELECT epic INTO STRICT v_epic
    FROM voter
    WHERE assigned_blo_id IS NOT NULL
      AND status <> 'MIGRATED'
    ORDER BY serial_no
    LIMIT 1;

    -- Process 5.0 prerequisite: SIR Administrator approves an active revision window
    INSERT INTO sir_config(start_date, end_date, approved, approved_by, approved_at)
    VALUES (current_date - 1, current_date + 1, true, 'admin', now());

    -- Invalid submission demonstration (Process 2.1 -> loops back to 1.0)
    v_bad := fn_submit_form(v_epic, '{"name":"","age":15}'::jsonb);
    v_bad_status := fn_validate_form(v_bad);
    RAISE NOTICE '[2.1 Invalid] form_id=%, status=%, reason=%',
        v_bad,
        v_bad_status,
        (SELECT error_reason FROM enumeration_form WHERE form_id = v_bad);

    -- Process 1.0: Voter submits enumeration form -> receives acknowledgement form_id
    v_form := fn_submit_form(v_epic, '{"name":"Demonstration Voter","age":35,"house_no":"D-10"}'::jsonb);
    RAISE NOTICE '[1.0] Acknowledgement: form_id=%', v_form;

    -- Process 2.1: Validate enumeration form against D1 Voter Records
    RAISE NOTICE '[2.1] Validation status=%', fn_validate_form(v_form);

    -- Process 2.2: Send field verification request to assigned BLO
    v_req := fn_send_verification_request(v_form);
    RAISE NOTICE '[2.2] Sent verification_request=% to BLO', v_req;

    -- Process 2.3: BLO submits field report -> updates D1 voter status & triggers audit_log
    PERFORM fn_record_verification(v_req, true, 'Demo field check passed at doorstep');
    RAISE NOTICE '[2.3] Updated voter status=%', (SELECT status FROM voter WHERE epic = v_epic);

    -- Process 4.0: Voter reports migration -> stored in D4 migration_record
    PERFORM fn_record_migration(v_epic, (SELECT house_no FROM voter WHERE epic = v_epic), 'D-99 New Block');
    RAISE NOTICE '[4.0] Migration recorded in D4 for epic=%', v_epic;

    -- Process 5.0: Apply electoral roll update inside transaction
    v_applied := fn_apply_roll_update();
    RAISE NOTICE '[5.0] Roll update applied to % voter(s); new status=%',
        v_applied, (SELECT status FROM voter WHERE epic = v_epic);
END $$;

-- Process 6.0: Generate SIR Reports & Audit Trail
SELECT * FROM part_summary;
SELECT * FROM section_summary ORDER BY part_no, section_no;
SELECT * FROM status_summary;
SELECT * FROM pending_by_blo ORDER BY blo_id;
SELECT audit_id, table_name, record_key, old_value, new_value, changed_at
FROM audit_log
ORDER BY audit_id DESC
LIMIT 10;
