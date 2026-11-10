"""Integration coverage. Set TEST_DATABASE_URL to an isolated DB with schema.sql applied."""
import os,pytest,psycopg
from types import SimpleNamespace
from app import require,HTTPException
DSN=os.getenv('TEST_DATABASE_URL')
dbtest=pytest.mark.skipif(not DSN,reason='TEST_DATABASE_URL not configured')
@dbtest
def test_functions_triggers_and_permissions():
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO part VALUES(1,174,'test') ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO section VALUES(1,1,'test') ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO blo(username,full_name,part_no) VALUES('test-blo','Test BLO',1) ON CONFLICT(username) DO NOTHING")
        epic='TST00000001'; c.execute("INSERT INTO voter(epic,part_no,section_no,serial_no,name,relation_type,age,gender) VALUES(%s,1,1,1,'A','Others',30,'M') ON CONFLICT DO NOTHING",(epic,))
        c.execute("UPDATE voter SET status='VERIFIED' WHERE epic=%s",(epic,))
        assert c.execute("SELECT count(*) FROM audit_log WHERE record_key=%s",(epic,)).fetchone()[0]>=1
        form=c.execute("SELECT fn_submit_form(%s,%s::jsonb)",(epic,'{"name":"A","age":30}')).fetchone()[0]
        assert c.execute('SELECT fn_validate_form(%s)',(form,)).fetchone()[0]=='VALID'
        bad=c.execute("SELECT fn_submit_form(%s,%s::jsonb)",(epic,'{"age":"bad"}')).fetchone()[0]
        assert c.execute('SELECT fn_validate_form(%s)',(bad,)).fetchone()[0]=='INVALID'
        with pytest.raises(psycopg.Error): c.execute('SELECT fn_apply_roll_update()')
        c.rollback()
def test_role_denial_in_web_layer():
    request=SimpleNamespace(session={'user':{'role':'VOTER'}})
    with pytest.raises(HTTPException) as exc: require(request,'ADMIN')
    assert exc.value.status_code==403

@dbtest
def test_booth_request_migration_and_roll_update():
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO part VALUES(2,174,'workflow') ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO section VALUES(2,1,'workflow') ON CONFLICT DO NOTHING")
        blo=c.execute("INSERT INTO blo(username,full_name,part_no) VALUES('workflow-blo','Workflow BLO',2) ON CONFLICT(username) DO UPDATE SET active=true RETURNING blo_id").fetchone()[0]
        epic='WRK00000001'
        c.execute("INSERT INTO voter(epic,part_no,section_no,serial_no,name,relation_type,age,gender) VALUES(%s,2,1,1,'Workflow','Others',30,'F') ON CONFLICT DO NOTHING",(epic,))
        assert c.execute('SELECT fn_assign_booth_and_blo(2,%s)',(blo,)).fetchone()[0]>=1
        form=c.execute("SELECT fn_submit_form(%s,%s::jsonb)",(epic,'{"name":"Workflow","age":30}')).fetchone()[0]
        assert c.execute('SELECT fn_validate_form(%s)',(form,)).fetchone()[0]=='VALID'
        req=c.execute('SELECT fn_send_verification_request(%s)',(form,)).fetchone()[0]
        c.execute("SELECT fn_record_verification(%s,true,'Checked')",(req,))
        c.execute("SELECT fn_record_migration(%s,'old','new')",(epic,))
        c.execute("INSERT INTO sir_config(start_date,end_date,approved) VALUES(current_date,current_date,true)")
        assert c.execute('SELECT fn_apply_roll_update()').fetchone()[0]==1
        assert c.execute('SELECT status FROM voter WHERE epic=%s',(epic,)).fetchone()[0]=='MIGRATED'
        assert c.execute('SELECT count(*) FROM migration_record WHERE epic=%s AND applied_at IS NOT NULL',(epic,)).fetchone()[0]>=1
