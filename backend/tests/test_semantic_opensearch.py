"""OpenSearch operator boundary and optional actual authenticated k-NN service."""
import json
import os
from pydantic import SecretStr
import pytest
from sqlmodel import Session, select
from fastapi import HTTPException
from app.config import settings
from app.models.message import Message
from app.models.content_completion import ChannelSemanticIndex
from app.services import semantic_opensearch
from app.services.content_completion import export_collection_pointers
from tests.test_content_completion import content, board, headers


def configured(monkeypatch,url='https://search.internal:9200'):
    monkeypatch.setattr(settings,'semantic_opensearch_url',url)
    monkeypatch.setattr(settings,'semantic_opensearch_index','freecollab-content-proof')
    monkeypatch.setattr(settings,'semantic_opensearch_username','fixture')
    monkeypatch.setattr(settings,'semantic_opensearch_password',SecretStr('synthetic-test-only'))


def test_operator_destination_authentication_and_tls_are_mandatory(monkeypatch):
    configured(monkeypatch)
    for url in ['https://user:secret@search.example.com','https://search.example.com?q=secret','file:///etc/passwd','http://public.example.com','http://8.8.8.8:9200']:
        monkeypatch.setattr(settings,'semantic_opensearch_url',url)
        with pytest.raises(HTTPException) as error:semantic_opensearch.options()
        assert error.value.status_code==503 and 'secret' not in str(error.value.detail)
    configured(monkeypatch,'http://127.0.0.1:9200')
    assert semantic_opensearch.options()[0]=='http://127.0.0.1:9200'
    monkeypatch.setattr(settings,'semantic_opensearch_password',SecretStr(''))
    with pytest.raises(HTTPException):semantic_opensearch.options()


def test_purge_does_not_commit_and_rejects_partial_delete(monkeypatch):
    configured(monkeypatch);calls=[]
    class Backend:
        index='test'
        def ensure_index(self,**kwargs):return True
        def request(self,*args,**kwargs):calls.append((args,kwargs));return {'timed_out':False,'failures':[],'version_conflicts':0,'deleted':3}
        def close(self):pass
    class Transaction:
        def commit(self):pytest.fail('purge must not commit its caller')
    monkeypatch.setattr(semantic_opensearch,'Backend',Backend)
    semantic_opensearch.purge_channel(Transaction(),'channel')
    assert calls[0][0][2]=={'query':{'term':{'channel_id':'channel'}}}
    for partial in [{'timed_out':True,'failures':[],'version_conflicts':0},{'timed_out':False,'failures':[{'cause':'unavailable'}],'version_conflicts':0},{'timed_out':False,'failures':[],'version_conflicts':1}]:
        with pytest.raises(HTTPException):semantic_opensearch.check_deleted(partial)


def test_collection_export_skips_changed_missing_and_foreign_resources(content):
    d=content
    made=d.client.post('/channels/a/content-collections',headers=headers(d),json={'title':'Export materials','items':[{'kind':'file','id':'file-a'}]})
    assert made.status_code==201
    with Session(d.engine) as session:
        assert len(export_collection_pointers(session,['a'],'owner'))==1
        from app.models.file import File
        file=session.get(File,'file-a');file.channel_id='b';session.add(file);session.commit()
        assert export_collection_pointers(session,['a'],'owner')==[]


@pytest.mark.skipif(not os.getenv('CONTENT_OPENSEARCH_TEST_URL'),reason='Dedicated actual OpenSearch fixture not selected')
def test_actual_opensearch_knn_revision_delete_auth_and_native_acl(content,monkeypatch):
    d=content;configured(monkeypatch,os.environ['CONTENT_OPENSEARCH_TEST_URL'])
    monkeypatch.setattr(settings,'semantic_opensearch_username',os.environ['CONTENT_OPENSEARCH_TEST_USERNAME'])
    monkeypatch.setattr(settings,'semantic_opensearch_password',SecretStr(os.environ['CONTENT_OPENSEARCH_TEST_PASSWORD']))
    monkeypatch.setattr(settings,'semantic_opensearch_ca_bundle',os.environ['CONTENT_OPENSEARCH_TEST_CA'])
    monkeypatch.setattr(settings,'semantic_opensearch_timeout',15)
    with Session(d.engine) as session:
        for index,text in enumerate(['cat pet kitten','cat pet home','railway train station','train station travel']):session.add(Message(id='vector-'+str(index),channel_id='a',author_id='owner',content=text,encrypted=False))
        session.commit()
    path='/workspaces/wa/semantic-search';query={'query':'kitten','channel_ids':['a'],'limit':10}
    first=d.client.post(path,headers=headers(d),json=query)
    assert first.status_code==200,first.text
    assert first.json()['engine']=='opensearch-lsa-knn-v1'
    assert any(item['id']=='vector-1' for item in first.json()['items'])
    backend=semantic_opensearch.Backend()
    try:
        mirrored=backend.request('POST','/'+backend.index+'/_search',{'size':20,'query':{'match_all':{}}})['hits']['hits']
        assert len(mirrored)==4
        assert all(set(item['_source'])=={'channel_id','source_id','kind','version','generation','vector'} for item in mirrored)
        assert 'kitten' not in json.dumps(mirrored)
    finally:backend.close()
    with Session(d.engine) as session:
        row=session.get(Message,'vector-1');session.delete(row);session.commit()
    latest=d.client.post(path,headers=headers(d),json=query);assert latest.status_code==200,latest.text
    backend=semantic_opensearch.Backend()
    try:
        mirrored=backend.request('POST','/'+backend.index+'/_search',{'size':20,'query':{'match_all':{}}})['hits']['hits']
        assert all(item['_source']['source_id']!='vector-1' for item in mirrored)
    finally:backend.close()
    monkeypatch.setattr(settings,'semantic_opensearch_password',SecretStr('wrong-fixture-password'))
    rejected=d.client.post(path,headers=headers(d),json=query)
    assert rejected.status_code==503 and 'wrong-fixture-password' not in rejected.text
    monkeypatch.setattr(settings,'semantic_opensearch_password',SecretStr(os.environ['CONTENT_OPENSEARCH_TEST_PASSWORD']))
    with Session(d.engine) as session:
        from app.models.channel import ChannelMember
        session.delete(session.get(ChannelMember,('a','owner')));session.commit()
    assert d.client.post(path,headers=headers(d),json=query).status_code==403
    # Remote erasure is real; caller transaction ownership stays with the privacy layer.
    with Session(d.engine) as session:semantic_opensearch.purge_channel(session,'a')
    backend=semantic_opensearch.Backend()
    try:assert backend.request('POST','/'+backend.index+'/_count',{'query':{'term':{'channel_id':'a'}}})['count']==0
    finally:backend.close()


def test_public_demo_cannot_inherit_an_external_vector_configuration(tmp_path):
    root=tmp_path/'public-demo';root.mkdir()
    safe={'demo_instance':True,'demo_data_path':str(root),'database_url':'sqlite:///'+str(root/'public-demo.sqlite'),
        'storage_path':str(root/'files'),'message_encryption_key_file':str(root/'message-keys.json'),
        'account_security_key_file':str(root/'account-security.key'),'storage_backend':'local','mail_delivery_mode':'disabled',
        'redis_url':None,'meetings_sfu_provider':'disabled','meetings_ice_servers':[],'meetings_turn_urls':[],
        'semantic_opensearch_url':'','semantic_opensearch_username':'','semantic_opensearch_password':SecretStr(''),
        'semantic_opensearch_ca_bundle':'','semantic_opensearch_index':'freecollab-semantic-v1'}
    for flag in ['matrix_enabled','connect_federation_enabled','oidc_enabled','webhooks_enabled','sync_enabled','otrust_enabled','cloud_files_enabled','external_meetings_enabled','copilot_enabled','push_enabled','native_push_enabled','native_calls_enabled','enable_experimental_features','enterprise_search_enabled','data_controls_enabled']:safe[flag]=False
    clean=settings.model_copy(update=safe);assert clean.validate_public_demo() is clean
    for key,value in [('semantic_opensearch_url','https://search.internal'),('semantic_opensearch_username','operator'),('semantic_opensearch_password',SecretStr('fixture')),('semantic_opensearch_ca_bundle','/operator/ca.pem'),('semantic_opensearch_index','other-index')]:
        with pytest.raises(ValueError,match='semantic external vector backend'):clean.model_copy(update={key:value}).validate_public_demo()
