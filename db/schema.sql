-- SIR Management System schema and business logic (PostgreSQL 15+)
CREATE TYPE voter_status AS ENUM ('PENDING','VERIFIED','REJECTED','MIGRATED');
CREATE TYPE form_status AS ENUM ('SUBMITTED','VALID','INVALID');
CREATE TYPE user_role AS ENUM ('VOTER','BLO','ADMIN');

CREATE TABLE part(part_no integer PRIMARY KEY, assembly_constituency integer NOT NULL, name text NOT NULL);
CREATE TABLE section(part_no integer NOT NULL REFERENCES part, section_no integer NOT NULL, section_name text NOT NULL,
 PRIMARY KEY(part_no,section_no));
CREATE TABLE blo(blo_id bigserial PRIMARY KEY, username text NOT NULL UNIQUE, full_name text NOT NULL, part_no integer NOT NULL REFERENCES part, active boolean NOT NULL DEFAULT true);
CREATE TABLE voter(epic text PRIMARY KEY, part_no integer NOT NULL, section_no integer NOT NULL, serial_no integer NOT NULL,
 name text NOT NULL, relation_type text NOT NULL CHECK(relation_type IN ('Fathers','Husbands','Mothers','Others')),
 relation_name text NOT NULL DEFAULT '', house_no text NOT NULL DEFAULT '', age integer NOT NULL CHECK(age BETWEEN 18 AND 120),
 gender text NOT NULL CHECK(gender IN ('M','F','T')), status voter_status NOT NULL DEFAULT 'PENDING',
 assigned_blo_id bigint REFERENCES blo, updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(part_no,section_no) REFERENCES section(part_no,section_no), UNIQUE(part_no,serial_no));
CREATE INDEX idx_voter_part_section ON voter(part_no,section_no);
CREATE INDEX idx_voter_status ON voter(status);
CREATE INDEX idx_voter_name ON voter(name);
CREATE TABLE enumeration_form(form_id bigserial PRIMARY KEY, epic text NOT NULL REFERENCES voter, payload jsonb NOT NULL,
 status form_status NOT NULL DEFAULT 'SUBMITTED', error_reason text, submitted_at timestamptz NOT NULL DEFAULT now(), validated_at timestamptz);
CREATE TABLE verification_request(request_id bigserial PRIMARY KEY, form_id bigint NOT NULL REFERENCES enumeration_form,
 epic text NOT NULL REFERENCES voter, blo_id bigint NOT NULL REFERENCES blo, status text NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','VERIFIED','REJECTED')),
 field_report text, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz);
CREATE UNIQUE INDEX uq_verification_form ON verification_request(form_id);
CREATE TABLE migration_record(migration_id bigserial PRIMARY KEY, epic text NOT NULL REFERENCES voter, from_address text NOT NULL,
 to_address text NOT NULL, reported_at timestamptz NOT NULL DEFAULT now(), applied_at timestamptz);
CREATE UNIQUE INDEX uq_one_open_migration_per_voter ON migration_record(epic) WHERE applied_at IS NULL;
CREATE TABLE sir_config(config_id bigserial PRIMARY KEY, start_date date NOT NULL, end_date date NOT NULL CHECK(end_date>=start_date),
 approved boolean NOT NULL DEFAULT false, approved_by text, approved_at timestamptz);
CREATE TABLE app_user(user_id bigserial PRIMARY KEY, username text NOT NULL UNIQUE, password_hash text NOT NULL,
 role user_role NOT NULL, voter_epic text UNIQUE REFERENCES voter, blo_id bigint UNIQUE REFERENCES blo,
 CHECK((role='VOTER' AND voter_epic IS NOT NULL AND blo_id IS NULL) OR (role='BLO' AND blo_id IS NOT NULL AND voter_epic IS NULL) OR (role='ADMIN' AND voter_epic IS NULL AND blo_id IS NULL)));
CREATE TABLE audit_log(audit_id bigserial PRIMARY KEY, table_name text NOT NULL, record_key text NOT NULL,
 old_value text, new_value text, changed_at timestamptz NOT NULL DEFAULT now(), changed_by text DEFAULT current_user);

CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.updated_at=now(); RETURN NEW; END $$;
CREATE TRIGGER voter_touch BEFORE UPDATE ON voter FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE FUNCTION audit_voter_status() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF OLD.status IS DISTINCT FROM NEW.status THEN INSERT INTO audit_log(table_name,record_key,old_value,new_value) VALUES('voter',NEW.epic,OLD.status::text,NEW.status::text); END IF;
 RETURN NEW; END $$;
CREATE TRIGGER voter_status_audit AFTER UPDATE ON voter FOR EACH ROW EXECUTE FUNCTION audit_voter_status();
CREATE FUNCTION guard_applied_migration() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM migration_record WHERE epic=OLD.epic AND applied_at IS NOT NULL) AND
 (NEW.name,NEW.relation_name,NEW.house_no,NEW.age,NEW.gender) IS DISTINCT FROM (OLD.name,OLD.relation_name,OLD.house_no,OLD.age,OLD.gender)
 THEN RAISE EXCEPTION 'Voter % has an applied migration and cannot be edited',OLD.epic; END IF; RETURN NEW; END $$;
CREATE TRIGGER voter_migration_guard BEFORE UPDATE ON voter FOR EACH ROW EXECUTE FUNCTION guard_applied_migration();

CREATE FUNCTION fn_submit_form(p_epic text,p_payload jsonb) RETURNS bigint LANGUAGE plpgsql AS $$ DECLARE x bigint; BEGIN
 INSERT INTO enumeration_form(epic,payload) VALUES(p_epic,p_payload) RETURNING form_id INTO x; RETURN x; END $$;
CREATE FUNCTION fn_validate_form(p_form bigint) RETURNS form_status LANGUAGE plpgsql AS $$ DECLARE f enumeration_form%ROWTYPE; BEGIN
 SELECT * INTO STRICT f FROM enumeration_form WHERE form_id=p_form FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM voter WHERE epic=f.epic) THEN UPDATE enumeration_form SET status='INVALID',error_reason='EPIC not found',validated_at=now() WHERE form_id=p_form; RETURN 'INVALID'; END IF;
 IF coalesce(f.payload->>'name','')='' OR coalesce(f.payload->>'age','') !~ '^([1-9][0-9]?)$' THEN
 UPDATE enumeration_form SET status='INVALID',error_reason='Name and valid age are required',validated_at=now() WHERE form_id=p_form; RETURN 'INVALID'; END IF;
 UPDATE enumeration_form SET status='VALID',error_reason=NULL,validated_at=now() WHERE form_id=p_form; RETURN 'VALID'; END $$;
CREATE FUNCTION fn_send_verification_request(p_form bigint) RETURNS bigint LANGUAGE plpgsql AS $$ DECLARE v voter%ROWTYPE; x bigint; BEGIN
 SELECT * INTO STRICT v FROM voter WHERE epic=(SELECT epic FROM enumeration_form WHERE form_id=p_form);
 IF NOT EXISTS(SELECT 1 FROM enumeration_form WHERE form_id=p_form AND status='VALID') THEN RAISE EXCEPTION 'Form must be valid'; END IF;
 IF v.assigned_blo_id IS NULL THEN RAISE EXCEPTION 'No BLO assigned'; END IF;
 INSERT INTO verification_request(form_id,epic,blo_id) VALUES(p_form,v.epic,v.assigned_blo_id)
 ON CONFLICT(form_id) DO UPDATE SET form_id=EXCLUDED.form_id RETURNING request_id INTO x; RETURN x; END $$;
CREATE FUNCTION fn_record_verification(p_request bigint,p_ok boolean,p_report text) RETURNS void LANGUAGE plpgsql AS $$ DECLARE r verification_request%ROWTYPE; BEGIN
 SELECT * INTO STRICT r FROM verification_request WHERE request_id=p_request FOR UPDATE;
 UPDATE verification_request SET status=CASE WHEN p_ok THEN 'VERIFIED' ELSE 'REJECTED' END,field_report=p_report,completed_at=now() WHERE request_id=p_request;
 UPDATE voter SET status=CASE WHEN p_ok THEN 'VERIFIED'::voter_status ELSE 'REJECTED'::voter_status END WHERE epic=r.epic;
 END $$;
CREATE FUNCTION fn_assign_booth_and_blo(p_part integer,p_blo bigint) RETURNS integer LANGUAGE plpgsql AS $$ DECLARE n integer; BEGIN
 UPDATE voter SET assigned_blo_id=p_blo WHERE part_no=p_part; GET DIAGNOSTICS n=ROW_COUNT; RETURN n; END $$;
CREATE VIEW v_blo_assigned_voters AS SELECT b.blo_id,b.full_name AS blo_name,v.epic,v.serial_no,v.name,v.section_no,v.status FROM voter v JOIN blo b ON b.blo_id=v.assigned_blo_id;
CREATE FUNCTION fn_record_migration(p_epic text,p_from text,p_to text) RETURNS bigint LANGUAGE plpgsql AS $$ DECLARE x bigint; BEGIN
 INSERT INTO migration_record(epic,from_address,to_address) VALUES(p_epic,p_from,p_to) RETURNING migration_id INTO x; RETURN x; END $$;
CREATE FUNCTION fn_apply_roll_update() RETURNS integer LANGUAGE plpgsql AS $$ DECLARE n integer; BEGIN
 IF NOT EXISTS(SELECT 1 FROM sir_config WHERE approved AND current_date BETWEEN start_date AND end_date) THEN RAISE EXCEPTION 'No approved SIR configuration in date window'; END IF;
 UPDATE voter v SET house_no=m.to_address,status='MIGRATED' FROM migration_record m WHERE m.epic=v.epic AND m.applied_at IS NULL AND v.status='VERIFIED';
 GET DIAGNOSTICS n=ROW_COUNT; UPDATE migration_record SET applied_at=now() WHERE applied_at IS NULL AND epic IN (SELECT epic FROM voter WHERE status='MIGRATED'); RETURN n;
END $$;
CREATE VIEW part_summary AS SELECT part_no,count(*) AS electors,count(*) FILTER(WHERE gender='M') AS male,count(*) FILTER(WHERE gender='F') AS female,count(*) FILTER(WHERE gender='T') AS third_gender FROM voter GROUP BY part_no;
CREATE VIEW section_summary AS SELECT part_no,section_no,count(*) AS electors,count(*) FILTER(WHERE status='PENDING') AS pending,count(*) FILTER(WHERE status='VERIFIED') AS verified FROM voter GROUP BY part_no,section_no;
CREATE VIEW status_summary AS SELECT status,count(*) AS electors FROM voter GROUP BY status;
CREATE VIEW pending_by_blo AS SELECT b.blo_id,b.full_name,count(*) FILTER(WHERE v.status='PENDING') AS pending FROM blo b LEFT JOIN voter v ON b.blo_id=v.assigned_blo_id GROUP BY b.blo_id,b.full_name;

DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='voter_role') THEN CREATE ROLE voter_role NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='blo_role') THEN CREATE ROLE blo_role NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='admin_role') THEN CREATE ROLE admin_role NOLOGIN; END IF;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT ON voter,part,section TO voter_role;
GRANT EXECUTE ON FUNCTION fn_submit_form(text,jsonb),fn_record_migration(text,text,text) TO voter_role;
GRANT SELECT ON v_blo_assigned_voters,verification_request TO blo_role; GRANT EXECUTE ON FUNCTION fn_record_verification(bigint,boolean,text) TO blo_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO admin_role; GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO admin_role;
