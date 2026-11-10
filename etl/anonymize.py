"""Create privacy-preserving demo CSV while retaining elector structure."""
import argparse,csv
from pathlib import Path
from faker import Faker
fake=Faker('en_IN')
def anonymize(source,dest):
    with open(source,encoding='utf-8',newline='') as f: rows=list(csv.DictReader(f))
    for r in rows:
        r['name']=fake.name(); r['relation_name']=fake.name(); r['house_no']=str(fake.building_number())
        # EPIC is personal identifying data too; retain its shape and uniqueness.
        r['epic']=f"DEM{int(r['serial_no']):07d}"
    with open(dest,'w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    print(f'Anonymized {len(rows)} rows -> {dest}')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('dest',type=Path);a=p.parse_args();anonymize(a.source,a.dest)
