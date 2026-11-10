"""FastAPI/Jinja SIR workflow UI. Database interactions are parameterized SQL."""
import os,csv
from contextlib import contextmanager
from io import StringIO
from fastapi import FastAPI,Form,Request,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse,StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import psycopg
from psycopg.rows import dict_row
from passlib.context import CryptContext
DATABASE_URL=os.getenv('DATABASE_URL','postgresql://sir:sir@localhost:5432/sir')
pwd=CryptContext(schemes=['bcrypt'],deprecated='auto')
app=FastAPI(title='SIR Management System'); app.add_middleware(SessionMiddleware,secret_key=os.getenv('SESSION_SECRET','dev-change-me'))
templates=Jinja2Templates(directory='templates')
@contextmanager
def db():
    with psycopg.connect(DATABASE_URL,row_factory=dict_row) as conn: yield conn
def current(request):
    user=request.session.get('user')
    if not user: raise HTTPException(401,'Please sign in')
    return user
def require(request,role):
    user=current(request)
    if user['role']!=role: raise HTTPException(403,'Role permission denied')
    return user
@app.get('/',response_class=HTMLResponse)
def home(request:Request): return templates.TemplateResponse('login.html',{'request':request})
@app.get('/voter/status',response_class=HTMLResponse)
def voter_status(request:Request,epic:str):
    with db() as c:v=c.execute('SELECT epic,status,updated_at FROM voter WHERE epic=%s',(epic.strip().upper(),)).fetchone()
    if not v: raise HTTPException(404,'EPIC not found')
    return templates.TemplateResponse('message.html',{'request':request,'title':'Voter status','message':f"{v['epic']}: {v['status']} (updated {v['updated_at']})"})
@app.post('/login')
def login(request:Request,username:str=Form(...),password:str=Form(...)):
    with db() as c: u=c.execute('SELECT user_id,username,password_hash,role,voter_epic,blo_id FROM app_user WHERE username=%s',(username,)).fetchone()
    if not u or not pwd.verify(password,u['password_hash']): raise HTTPException(401,'Invalid credentials')
    request.session['user']={k:u[k] for k in ('user_id','username','role','voter_epic','blo_id')}; return RedirectResponse('/dashboard',303)
@app.post('/logout')
def logout(request:Request): request.session.clear(); return RedirectResponse('/',303)
@app.get('/dashboard',response_class=HTMLResponse)
def dashboard(request:Request):
    u=current(request)
    if u['role']=='VOTER':
        with db() as c:v=c.execute('SELECT * FROM voter WHERE epic=%s',(u['voter_epic'],)).fetchone()
        return templates.TemplateResponse('voter.html',{'request':request,'user':u,'voter':v})
    if u['role']=='BLO':
        with db() as c: rows=c.execute('SELECT * FROM v_blo_assigned_voters WHERE blo_id=%s',(u['blo_id'],)).fetchall(); reqs=c.execute("SELECT * FROM verification_request WHERE blo_id=%s AND status='OPEN' ORDER BY created_at",(u['blo_id'],)).fetchall()
        return templates.TemplateResponse('blo.html',{'request':request,'user':u,'rows':rows,'requests':reqs})
    with db() as c:
        sums=c.execute('SELECT * FROM part_summary').fetchall(); statuses=c.execute('SELECT * FROM status_summary').fetchall()
        sections=c.execute('SELECT * FROM section_summary ORDER BY part_no,section_no').fetchall()
        pending=c.execute('SELECT * FROM pending_by_blo ORDER BY blo_id').fetchall()
        blos=c.execute('SELECT blo_id,full_name FROM blo WHERE active ORDER BY full_name').fetchall()
    return templates.TemplateResponse('admin.html',{'request':request,'user':u,'sums':sums,'statuses':statuses,'sections':sections,'pending':pending,'blos':blos})
@app.post('/voter/form')
def submit_form(request:Request,name:str=Form(...),age:int=Form(...),house_no:str=Form('')):
    u=require(request,'VOTER'); payload={'name':name,'age':age,'house_no':house_no}
    with db() as c: row=c.execute('SELECT fn_submit_form(%s,%s::jsonb) AS form_id',(u['voter_epic'],psycopg.types.json.Jsonb(payload))).fetchone()
    return RedirectResponse(f'/voter/form/{row["form_id"]}',303)
@app.get('/voter/form/{form_id}',response_class=HTMLResponse)
def validate_view(request:Request,form_id:int):
    u=require(request,'VOTER')
    with db() as c:
        r=c.execute('SELECT epic FROM enumeration_form WHERE form_id=%s',(form_id,)).fetchone()
        if not r or r['epic']!=u['voter_epic']:raise HTTPException(404)
        status=c.execute('SELECT fn_validate_form(%s) AS status',(form_id,)).fetchone()['status']; reason=c.execute('SELECT error_reason FROM enumeration_form WHERE form_id=%s',(form_id,)).fetchone()['error_reason']
        if status=='VALID':c.execute('SELECT fn_send_verification_request(%s)',(form_id,))
    return templates.TemplateResponse('message.html',{'request':request,'title':'Form '+status,'message':reason or ('Submitted and sent for field verification.' if status=='VALID' else 'Please correct and resubmit.')})
@app.post('/voter/migration')
def migration(request:Request,from_address:str=Form(...),to_address:str=Form(...)):
    u=require(request,'VOTER')
    with db() as c:c.execute('SELECT fn_record_migration(%s,%s,%s)',(u['voter_epic'],from_address,to_address))
    return RedirectResponse('/dashboard',303)
@app.post('/blo/verification/{request_id}')
def verify(request:Request,request_id:int,verified:bool=Form(...),field_report:str=Form(...)):
    require(request,'BLO')
    with db() as c:c.execute('SELECT fn_record_verification(%s,%s,%s)',(request_id,verified,field_report))
    return RedirectResponse('/dashboard',303)
@app.post('/admin/config')
def config(request:Request,start_date:str=Form(...),end_date:str=Form(...),approve:bool=Form(False)):
    u=require(request,'ADMIN')
    with db() as c:c.execute('INSERT INTO sir_config(start_date,end_date,approved,approved_by,approved_at) VALUES(%s,%s,%s,%s,CASE WHEN %s THEN now() END)',(start_date,end_date,approve,u['username'],approve))
    return RedirectResponse('/dashboard',303)
@app.post('/admin/assign')
def assign_blo(request:Request,part_no:int=Form(...),blo_id:int=Form(...)):
    require(request,'ADMIN')
    with db() as c:c.execute('SELECT fn_assign_booth_and_blo(%s,%s)',(part_no,blo_id))
    return RedirectResponse('/dashboard',303)
@app.post('/admin/apply')
def apply(request:Request):
    require(request,'ADMIN')
    with db() as c:count=c.execute('SELECT fn_apply_roll_update() AS n').fetchone()['n']
    return RedirectResponse(f'/dashboard?updated={count}',303)
@app.get('/admin/report.csv')
def report(request:Request):
    require(request,'ADMIN')
    with db() as c:rows=c.execute('SELECT * FROM part_summary').fetchall()
    f=StringIO(); w=csv.DictWriter(f,fieldnames=rows[0].keys() if rows else ['part_no','electors']); w.writeheader(); w.writerows(rows); f.seek(0)
    return StreamingResponse(iter([f.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=sir-report.csv'})
