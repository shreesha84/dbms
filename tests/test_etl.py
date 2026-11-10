from etl.extract_roll import validate
def rows():
    out=[]
    for n,g in [(i,'M' if i<=254 else 'F' if i<=493 else 'T') for i in range(1,495)]:
        out.append({'serial_no':n,'epic':f'ABC{n:07d}','gender':g,'name':'Person','age':'30'})
    return out
def test_valid_fixture(): assert validate(rows())==[]
def test_gender_validation():
    x=rows();x[0]['gender']='F'; assert any('gender counts' in e for e in validate(x))
def test_serial_gap():
    x=rows();x.pop(20); assert any('row count' in e for e in validate(x))
