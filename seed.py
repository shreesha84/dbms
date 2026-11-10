"""Create demo BLO/admin accounts and assign voters after CSV load."""
import os
import psycopg
from passlib.context import CryptContext
pwd=CryptContext(schemes=['bcrypt'],deprecated='auto'); dsn=os.getenv('DATABASE_URL','postgresql://sir:sir@localhost:5432/sir')
with psycopg.connect(dsn) as c:
    with c.cursor() as q:
        q.execute("INSERT INTO blo(username,full_name,part_no) VALUES('blo1','Demo BLO One',271),('blo2','Demo BLO Two',271) ON CONFLICT(username) DO NOTHING")
        q.execute("INSERT INTO app_user(username,password_hash,role) VALUES(%s,%s,'ADMIN') ON CONFLICT(username) DO NOTHING",('admin',pwd.hash(os.getenv('ADMIN_PASSWORD','admin123'))))
        for name in ('blo1','blo2'):
            q.execute('SELECT blo_id FROM blo WHERE username=%s',(name,)); bid=q.fetchone()[0]
            q.execute('INSERT INTO app_user(username,password_hash,role,blo_id) VALUES(%s,%s,\'BLO\',%s) ON CONFLICT(username) DO NOTHING',(name,pwd.hash(name+'123'),bid))
        q.execute('SELECT epic FROM voter ORDER BY serial_no LIMIT 10')
        for i,row in enumerate(q.fetchall()):
            q.execute('UPDATE voter SET assigned_blo_id=(SELECT blo_id FROM blo WHERE username=%s) WHERE epic=%s',('blo1' if i<5 else 'blo2',row[0]))
            q.execute('INSERT INTO app_user(username,password_hash,role,voter_epic) VALUES(%s,%s,\'VOTER\',%s) ON CONFLICT(username) DO NOTHING',('voter'+str(i+1),pwd.hash('voter123'),row[0]))
print('Seeded admin/admin123, BLO blo1/blo1123 and blo2/blo2123, and up to 10 demo voter accounts (voter1..voter10/voter123).')
