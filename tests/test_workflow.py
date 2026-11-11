import copy
import io
import json
import time
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfWriter
from vendorflow.app import create_app
from vendorflow.engine import evaluate, valid_aba, valid_iban

ROOT = Path(__file__).resolve().parents[1]


def example(name='01-happy'):
    return json.loads((ROOT / 'examples' / (name + '.json')).read_text())


def evaluate_example(item, prior=None):
    return evaluate(item['submission'], {k: {'text':v} for k,v in item['documents'].items()}, prior or [], lambda *args:None, date(2026,11,11))


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING':True, 'DATA_DIR':str(tmp_path)})
    yield app
    app.extensions['executor'].shutdown(wait=True)


def wait(client, run_id):
    deadline = time.monotonic()+5
    while time.monotonic()<deadline:
        response = client.get('/api/runs/'+run_id)
        assert response.status_code == 200
        run=response.json
        if run['status'] not in {'queued','running'}:
            return run
        time.sleep(.01)
    pytest.fail('Run did not finish within 5 seconds')


@pytest.mark.parametrize('name,status', [('01-happy','approved'),('02-missing','pending'),('03-owner','pending'),('04-checksum','pending')])
def test_scenarios(name,status):
    result=evaluate_example(example(name))
    assert result['status']==status
    if status!='approved':
        assert result['actions']
        assert all(action in result['communication']['body'] for action in result['actions'])


def test_shared_bank_rejected():
    prior=[{'status':'approved','payload':example()['submission']}]
    result=evaluate_example(example('05-shared'),prior)
    assert result['status']=='rejected'
    assert any(f['code']=='shared_bank' for f in result['findings'])


def test_duplicate_entity_pending():
    result=evaluate_example(example(),[{'status':'approved','payload':example()['submission']}])
    assert result['status']=='pending'
    assert any(f['code']=='duplicate_entity' for f in result['findings'])


@pytest.mark.parametrize('change,code', [
    (lambda x:x['documents'].__setitem__('tax',x['documents']['tax'].replace('12-3456789','98-7654321')),'tax_identity'),
    (lambda x:x['documents'].__setitem__('bank',x['documents']['bank'].replace('1234567890','9999999999')),'bank_evidence'),
    (lambda x:x['documents'].__setitem__('compliance',x['documents']['compliance'].replace('declared: no','declared: yes')),'sanctions')])
def test_conflicts_reject(change,code):
    item=example();change(item);result=evaluate_example(item)
    assert result['status']=='rejected'
    assert any(f['code']==code and f['outcome']=='rejected' for f in result['findings'])


def test_wrong_document_and_unknown_country():
    item=example();item['documents']['tax']=item['documents']['bank']
    result=evaluate_example(item)
    assert result['status']!='approved'
    assert any(f['code']=='tax_type' and f['outcome']=='pending' for f in result['findings'])
    item=example();item['submission']['country']='IN'
    result=evaluate_example(item)
    assert result['status']=='pending'
    assert any(f['code']=='country' and f['outcome']=='pending' for f in result['findings'])


def test_uk_happy_path():
    item=example();item['submission'].update(country='GB',tax_id='1234567890')
    item['submission']['bank']={'account_holder':'Northstar Office Supply LLC','iban':'GB82 WEST 1234 5698 7654 32'}
    item['documents']['tax']=item['documents']['tax'].replace('Country: US','Country: GB').replace('12-3456789','1234567890')
    item['documents']['bank']='Document type: bank\nAccount holder: Northstar Office Supply LLC\nIBAN: GB82WEST12345698765432'
    assert evaluate_example(item)['status']=='approved'
    assert valid_iban('GB82WEST12345698765432')
    assert not valid_iban('GB83WEST12345698765432')
    assert valid_aba('021000021')
    assert not valid_aba('000000000')


def test_api_end_to_end_persistence_and_redaction(app):
    client=app.test_client()
    response=client.post('/api/runs',json=example())
    assert response.status_code==202
    run=wait(client,response.json['id'])
    assert run['status']=='approved'
    assert len(run['events'])==6
    assert len(run['documents'])==3
    assert all(len(d['sha256'])==64 for d in run['documents'])
    assert '1234567890' not in json.dumps(run)
    assert '12-3456789' not in json.dumps(run)
    assert client.get('/api/runs').json['counts']=={'approved':1}
    report=client.get('/api/runs/'+run['id']+'/report')
    assert 'attachment' in report.headers['Content-Disposition']
    assert report.json['result']['status']=='approved'
    other=create_app({'DATA_DIR':app.config['DATA_DIR'],'TESTING':True})
    try:
        assert other.test_client().get('/api/runs/'+run['id']).json['status']=='approved'
    finally:
        other.extensions['executor'].shutdown(wait=True)


def test_serialized_duplicate_runs(app):
    client=app.test_client()
    first=client.post('/api/runs',json=example()).json['id']
    second=client.post('/api/runs',json=example()).json['id']
    assert wait(client,first)['status']=='approved'
    assert wait(client,second)['status']=='pending'
    third=client.post('/api/runs',json=example('05-shared')).json['id']
    assert wait(client,third)['status']=='rejected'


def test_upload_txt_and_unreadable_pdf(app):
    client=app.test_client();item=example()
    data={'submission':json.dumps(item['submission'])}
    for kind,text in item['documents'].items():
        data[kind]=(io.BytesIO(text.encode()),kind+'.txt')
    response=client.post('/api/runs',data=data)
    assert wait(client,response.json['id'])['status']=='approved'
    writer=PdfWriter();writer.add_blank_page(width=300,height=300);buffer=io.BytesIO();writer.write(buffer);buffer.seek(0)
    response=client.post('/api/runs',data={'submission':json.dumps(item['submission']),'tax':(buffer,'tax.pdf')})
    run=wait(client,response.json['id'])
    assert run['status']=='pending'
    assert any(f['code']=='tax_document' and f['outcome']=='pending' for f in run['result']['findings'])


@pytest.mark.parametrize('body', [None,[],{}, {'submission':[]},{'submission':{'bank':'bad'}},{'submission':{'legal_name':42}},{'submission':{},'documents':{'tax':42}}])
def test_invalid_json(app,body):
    response=app.test_client().post('/api/runs',data=json.dumps(body),content_type='application/json')
    assert response.status_code==400


def test_invalid_file_limits_and_cross_origin(app):
    client=app.test_client()
    for filename,content in [('file.pdf',b'broken'),('file.exe',b'MZ'),('file.txt',b'\xff'),('big.txt',b'a'*(2*1024*1024+1))]:
        response=client.post('/api/runs',data={'submission':'{}','tax':(io.BytesIO(content),filename)})
        assert response.status_code==400
    assert client.post('/api/runs',json=example(),headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/runs/missing').status_code==404
    assert client.post('/api/runs/missing/retry').status_code==409


def test_auth(tmp_path):
    app=create_app({'TESTING':True,'DATA_DIR':str(tmp_path),'TOKEN':'test-token'})
    try:
        client=app.test_client()
        assert client.get('/health').status_code==200
        assert client.get('/api/runs').status_code==401
        assert client.get('/api/runs',headers={'Authorization':'Bearer test-token'}).status_code==200
        assert client.get('/').headers['Content-Security-Policy']
    finally:
        app.extensions['executor'].shutdown(wait=True)


def test_pdf_text_is_extracted_and_validated(app):
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    item=example(); writer=PdfWriter(); page=writer.add_blank_page(612,792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    text=item['documents']['tax']
    stream=DecodedStreamObject()
    stream.set_data(('BT /F1 12 Tf 50 750 Td 16 TL '+ ' '.join('('+line+') Tj T*' for line in text.splitlines())+' ET').encode())
    page[NameObject('/Contents')]=writer._add_object(stream)
    buffer=io.BytesIO();writer.write(buffer);buffer.seek(0)
    data={'submission':json.dumps(item['submission']),'tax':(buffer,'registration.pdf')}
    for kind in ['bank','compliance']:
        data[kind]=(io.BytesIO(item['documents'][kind].encode()),kind+'.txt')
    client=app.test_client();response=client.post('/api/runs',data=data)
    run=wait(client,response.json['id'])
    assert run['status']=='approved'
    assert any(d['filename']=='registration.pdf' for d in run['documents'])


def test_interrupted_run_resumes(tmp_path):
    import sqlite3
    app=create_app({'DATA_DIR':str(tmp_path),'TESTING':True})
    app.extensions['executor'].shutdown(wait=True)
    item=example()
    with sqlite3.connect(tmp_path/'vendorflow.sqlite3') as conn:
        conn.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)',('interrupted','2026-11-11T00:00:00Z','running',json.dumps(item['submission']),json.dumps({k:dict(text=v,filename=k+'.txt',sha256='a'*64) for k,v in item['documents'].items()}),'[]',None))
    recovered=create_app({'DATA_DIR':str(tmp_path),'TESTING':True})
    try:
        run=wait(recovered.test_client(),'interrupted')
        assert run['status']=='approved'
        assert len(run['events'])==6
    finally:
        recovered.extensions['executor'].shutdown(wait=True)


def test_processing_failure_can_be_retried(app,monkeypatch):
    import vendorflow.app as module
    original=module.evaluate
    def broken(*args,**kwargs):
        raise RuntimeError('test-only processing failure')
    monkeypatch.setattr(module,'evaluate',broken)
    client=app.test_client();run_id=client.post('/api/runs',json=example()).json['id']
    assert wait(client,run_id)['status']=='error'
    monkeypatch.setattr(module,'evaluate',original)
    assert client.post('/api/runs/'+run_id+'/retry').status_code==202
    assert wait(client,run_id)['status']=='approved'


def test_expiry_boundary_and_unsigned_declaration():
    item=example();item['documents']['compliance']=item['documents']['compliance'].replace('2099-12-31','2026-11-11')
    assert evaluate_example(item)['status']=='approved'
    item['documents']['compliance']=item['documents']['compliance'].replace('Signed by: Alex Morgan','Signed by: ')
    result=evaluate_example(item)
    assert result['status']=='pending'
    assert any(f['code']=='signed' and f['outcome']=='pending' for f in result['findings'])


def test_missing_identity_values_are_pending_not_conflicts():
    item=example();item['submission']['tax_id']='';item['submission']['bank']['account_number']=''
    result=evaluate_example(item)
    assert result['status']=='pending'
    item=example();item['documents']['tax']=item['documents']['tax'].replace('Tax ID: 12-3456789','Tax ID:')
    assert evaluate_example(item)['status']=='pending'
