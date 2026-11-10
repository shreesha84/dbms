-- Run after schema/load/seed in a disposable demo database.
DO $$
DECLARE e text; f bigint; r bigint; bad bigint;
BEGIN
  SELECT epic INTO STRICT e FROM voter WHERE assigned_blo_id IS NOT NULL ORDER BY serial_no LIMIT 1;
  INSERT INTO sir_config(start_date,end_date,approved,approved_by,approved_at)
  VALUES(current_date-1,current_date+1,true,'admin',now());
  f:=fn_submit_form(e,'{"name":"Demonstration Voter","age":35,"house_no":"D-10"}');
  RAISE NOTICE '1.0 acknowledgement form_id=%',f;
  RAISE NOTICE '2.1 valid form result=%',fn_validate_form(f);
  r:=fn_send_verification_request(f);
  RAISE NOTICE '2.2 BLO verification_request=%',r;
  PERFORM fn_record_verification(r,true,'Demo field check passed');
  RAISE NOTICE '2.3 voter status=%',(SELECT status FROM voter WHERE epic=e);
  PERFORM fn_record_migration(e,(SELECT house_no FROM voter WHERE epic=e),'Demo migrated address');
  RAISE NOTICE '4.0 migration recorded';
  RAISE NOTICE '5.0 applied records=%',fn_apply_roll_update();
  bad:=fn_submit_form(e,'{"age":"bad"}');
  RAISE NOTICE 'Invalid form loops back for resubmission: status=%, reason=%',fn_validate_form(bad),(SELECT error_reason FROM enumeration_form WHERE form_id=bad);
END $$;
SELECT * FROM part_summary;
SELECT * FROM section_summary;
SELECT * FROM status_summary;
