"""Extract an ECI roll PDF into normalized CSV rows; OCR is used when needed."""
from __future__ import annotations
import argparse, csv, re, sys
from pathlib import Path
import pdfplumber

CARD = re.compile(r"^(\d+)\s+([A-Z]{3}\d{7,8})$")
HEADER = re.compile(r"Section No\.?\s*and\s*Name\s*(\d+)\s*[-:]\s*(.+)", re.I)
REL = re.compile(r"^(Fathers|Husbands|Mothers|Others)\s*[:.]?\s*(.*)$", re.I)

def page_text(page):
    txt = page.extract_text() or ""
    if txt.strip(): return txt
    try:
        from pdf2image import convert_from_path
        import pytesseract
        return pytesseract.image_to_string(convert_from_path(page.pdf.stream.name, first_page=page.page_number, last_page=page.page_number)[0])
    except Exception as exc:
        raise RuntimeError("No PDF text layer; install pdf2image, pytesseract, and Tesseract OCR") from exc

def parse_card(lines, epic, serial, section_no, section_name):
    # Card labels vary slightly across roll editions; parse labels first, then continuation lines.
    fields = {"serial_no": int(serial), "epic": epic, "section_no": section_no, "section_name": section_name,
              "name":"", "relation_type":"Others", "relation_name":"", "house_no":"", "age":"", "gender":""}
    clean=[re.sub(r"\s+"," ",x).strip() for x in lines if x.strip()]
    labels={"Name": "name", "House Number":"house_no", "House No":"house_no", "Age":"age", "Gender":"gender"}
    for i,line in enumerate(clean):
        m=re.match(r"^(Name|House Number|House No|Age|Gender)\s*[:.]?\s*(.*)$",line,re.I)
        if m:
            key=next((v for k,v in labels.items() if k.lower()==m.group(1).lower()),None)
            if key: fields[key]=m.group(2).strip()
        m=REL.match(line)
        if m: fields["relation_type"]=m.group(1).title(); fields["relation_name"]=m.group(2).strip()
    # Accommodate plain text extraction without labels by using demographic tokens and adjacent lines.
    if not fields["age"]:
        for line in clean:
            m=re.search(r"\b(\d{2,3})\s*(?:Years?|Yrs?)\b",line,re.I)
            if m: fields["age"]=m.group(1)
    if not fields["gender"]:
        for line in clean:
            m=re.search(r"\b(Male|Female|Third Gender|M|F|T)\b",line,re.I)
            if m: fields["gender"]={"male":"M","female":"F","third gender":"T"}.get(m.group(1).lower(),m.group(1).upper()); break
    if not fields["name"]:
        for line in clean:
            if not re.search(r"(Name|House|Age|Gender|Fathers|Husbands|Mothers|Others|Photo|Available)",line,re.I) and not re.search(r"\d{2,3}\s*(Years?|Yrs?)",line,re.I):
                fields["name"]=line; break
    for k in ("name","relation_name","house_no"): fields[k]=fields[k].strip()
    return fields

def extract(pdf_path):
    rows=[]
    with pdfplumber.open(pdf_path) as pdf:
        sec_no,sec_name="",""
        for page in pdf.pages[2:21]:
            text=page_text(page)
            for line in text.splitlines():
                h=HEADER.search(line)
                if h: sec_no,sec_name=h.group(1),h.group(2).strip()
            # Columns preserve card adjacency and wrap-around lines.
            for col in range(3):
                x0=page.width*col/3; x1=page.width*(col+1)/3
                crop=page.crop((x0,0,x1,page.height))
                lines=(crop.extract_text() or "").splitlines()
                if not lines: lines=text.splitlines()
                starts=[]
                for i,line in enumerate(lines):
                    m=CARD.match(line.strip())
                    if m: starts.append((i,m.group(1),m.group(2)))
                for j,(i,serial,epic) in enumerate(starts):
                    end=starts[j+1][0] if j+1<len(starts) else len(lines)
                    rows.append(parse_card(lines[i+1:end],epic,serial,sec_no,sec_name))
    # A card can be captured by two columns only at a crop boundary; deduplicate on EPIC.
    unique={r['epic']:r for r in rows}
    return sorted(unique.values(),key=lambda r:r['serial_no'])

def validate(rows):
    errors=[]
    if len(rows)!=494: errors.append(f"row count {len(rows)} != 494")
    counts={g:sum(r['gender']==g for r in rows) for g in ('M','F','T')}
    if counts!={'M':254,'F':239,'T':1}: errors.append(f"gender counts {counts} != M254/F239/T1")
    serials=[r['serial_no'] for r in rows]
    if sorted(serials)!=list(range(1,495)): errors.append("serials are not exactly 1..494")
    epics=[r['epic'] for r in rows]
    if len(epics)!=len(set(epics)): errors.append("duplicate EPIC values")
    missing=[r['epic'] for r in rows if not all(r[k] for k in ('name','age','gender'))]
    if missing: errors.append(f"missing core fields for {len(missing)} cards: {missing[:5]}")
    return errors

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('pdf',type=Path); ap.add_argument('--out',type=Path,default=Path('data/extracted')); args=ap.parse_args()
    rows=extract(args.pdf); errors=validate(rows)
    if errors:
        for err in errors: print('VALIDATION FAILURE:',err,file=sys.stderr)
        raise SystemExit(1)
    args.out.mkdir(parents=True,exist_ok=True)
    with (args.out/'voters.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]); w.writeheader(); w.writerows(rows)
    print(f"Validated {len(rows)} voters: M=254 F=239 T=1; wrote {args.out/'voters.csv'}")
if __name__=='__main__': main()
