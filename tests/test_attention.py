import json
from test_app import env
from test_note_rollup import add
from app.db import Company

def test_attention_groups_notes_and_preserves_membership(env):
    app,c,h,cid=env
    add(app,cid,'resNFe');add(app,cid,'nfeProc');add(app,cid,'resNFe',key='1'*44)
    with app.session_factory.begin() as s:
        hidden=Company(name='HIDDEN',document='11222333000181');s.add(hidden);s.flush();other=hidden.id
    add(app,other,'resNFe',key='2'*44)
    result=c.get('/api/overview').json
    row=result['items'][0]
    assert row['notes']=={'complete':1,'pending':1,'total':2}
    codes={x['code'] for x in row['attention']}
    assert 'pending_xml' in codes and 'certificate_missing' in codes
    assert row['can_manage'] is True
    assert 'HIDDEN' not in json.dumps(result)
    assert app.test_client().get('/api/overview').status_code==401

def test_attention_does_not_call_retry_a_permanent_failure():
    from app.attention import build_attention
    row={'certificate':'Selecionado','valid_until':'2099-01-01T00:00:00Z','online':True,'last':{'state':'failed'},'retry_at':200,'notes':{'pending':0},'documents':{},'batches':{},'capture_enabled':True,'can_manage':True}
    codes={x['code'] for x in build_attention(row,100)}
    assert 'automatic_retry' in codes and 'query_failed' not in codes
    row['retry_at']=0
    assert 'query_failed' in {x['code'] for x in build_attention(row,100)}
    row['valid_until']='2020-01-01T00:00:00Z'
    assert 'certificate_expired' in {x['code'] for x in build_attention(row,2000000000)}
