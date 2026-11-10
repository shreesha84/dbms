"""Idempotently load the extracted/anonymized CSV into the configured database."""
import argparse,csv,os
import psycopg
from psycopg.rows import dict_row
def load(path,dsn):
    with open(path,encoding='utf-8',newline='') as f: rows=list(csv.DictReader(f))
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            part=int(os.getenv('PART_NO','271')); ac=int(os.getenv('ASSEMBLY_CONSTITUENCY','174'))
            cur.execute("INSERT INTO part VALUES(%s,%s,%s) ON CONFLICT(part_no) DO NOTHING",(part,ac,'Mahadevapura'))
            for r in rows:
                sn=int(r['section_no'] or 1); name=r['section_name'] or f'Section {sn}'
                cur.execute("INSERT INTO section VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",(part,sn,name))
                cur.execute("""INSERT INTO voter(epic,part_no,section_no,serial_no,name,relation_type,relation_name,house_no,age,gender)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(epic) DO UPDATE SET section_no=EXCLUDED.section_no,
                  serial_no=EXCLUDED.serial_no,name=EXCLUDED.name,relation_type=EXCLUDED.relation_type,
                  relation_name=EXCLUDED.relation_name,house_no=EXCLUDED.house_no,age=EXCLUDED.age,gender=EXCLUDED.gender""",
                  (r['epic'],part,sn,int(r['serial_no']),r['name'],r['relation_type'],r['relation_name'],r['house_no'],int(r['age']),r['gender']))
    print(f'Loaded {len(rows)} rows (idempotent upsert)')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv');p.add_argument('--dsn',default=os.getenv('DATABASE_URL','postgresql://sir:sir@localhost:5432/sir'));a=p.parse_args();load(a.csv,a.dsn)
