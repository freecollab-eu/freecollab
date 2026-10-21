#!/usr/bin/env python3
"""Private actual Chromium previews/comments + HTTP Office WebDAV saveback.

CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/file-experience-browser.py --secure
--secure uses explicit Matrix environment or the retained homeserver with new
private identities. No desktop Microsoft Office binary is available/tested.
"""
import argparse
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import wave
from zipfile import ZIP_DEFLATED, ZipFile

from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('private_document_helpers',Path(__file__).with_name('shared-documents-browser.py'))
documents=importlib.util.module_from_spec(spec);spec.loader.exec_module(documents);helpers=documents.helpers
PASSWORD='Private-file-experience!2026'
DOCX='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
XLSX='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PPTX='application/vnd.openxmlformats-officedocument.presentationml.presentation'
REPORT=Path(os.getenv('FILE_EXPERIENCE_PROOF_REPORT',str(ROOT.parent/'.freecollab/proof-results/file-experience-browser.json')))


def package(parts):
    output=io.BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for name,value in {'[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',**parts}.items():archive.writestr(name,value)
    return output.getvalue()


def docx(text):
    return package({'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>'})


def upload(base,user,channel,name,content,mime):
    boundary='file-proof-'+secrets.token_hex(12)
    body=('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="'+name+'"\r\nContent-Type: '+mime+'\r\n\r\n').encode()+content+('\r\n--'+boundary+'--\r\n').encode()
    with urlopen(Request(base+'/files/channels/'+channel,data=body,method='POST',headers={'Authorization':'Bearer '+user['access_token'],'Content-Type':'multipart/form-data; boundary='+boundary}),timeout=45) as response:return json.load(response)


def dav(base,path,method,content=None,headers=None):
    with urlopen(Request(base+path,method=method,data=content,headers=headers or {}),timeout=45) as response:return response.status,dict(response.headers),response.read()


def login(page,app,user,email,workspace):
    page.goto(app,wait_until='domcontentloaded')
    page.evaluate('args=>localStorage.setItem(...args)',['freecollab.workspace:'+user['user_id'],workspace])
    page.get_by_role('navigation').get_by_role('button',name='Open App',exact=True).click()
    page.get_by_label('Email',exact=True).fill(email);page.get_by_label('Password',exact=True).fill(PASSWORD)
    page.get_by_role('button',name='Sign In',exact=True).click()
    page.get_by_role('button',name='Channel files',exact=True).click()
    return page.get_by_role('dialog',name='Channel files',exact=True)


def open_preview(page,name):
    page.get_by_role('button',name='Preview '+name,exact=True).click()
    return page.get_by_role('dialog',name='Preview '+name,exact=True)


def secure_proof(base,workspace,users,pages,results,private):
    left,right=pages;alice,bob=users
    channel=helpers.api(base,'POST','/channels',alice,{'name':'Encrypted file studio','workspace_id':workspace['id'],'member_ids':[bob['user_id']]})['id']
    for page in pages:
        page.set_default_timeout(90000)
        page.evaluate('''async()=>{window.fileProofSecure=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();window.fileProofExperience=(await import('/src/lib/file-experience-api.ts')).fileExperienceApi;window.fileProofProvider=(await import('/src/lib/secure/files.ts')).secureFileProvider;window.fileProofCloud=(await import('/src/lib/cloud-files-api.ts')).cloudFilesApi;}''')
    left.evaluate('channel=>fileProofSecure.enableChannel(channel,"Private file studio")',channel)
    right.evaluate('channel=>fileProofSecure.getMessages(channel)',channel)
    for page,other in [(left,bob),(right,alice)]:
        devices=documents.poll(lambda:page.evaluate('channel=>fileProofSecure.getDevices(channel)',channel),lambda rows:any(value['user_id']==other['user_id'] for value in rows),'Trusted recipient device missing')
        for device in devices:page.evaluate('device=>fileProofSecure.verifyDevice(device,device.fingerprint)',device)
    native_requests=[]
    for page in pages:page.on('request',lambda request:native_requests.append(request.url) if '/file-experience/' in request.url and '/channels/'+channel+'/' in request.url else None)
    private_text='<script>window.fileProofXss=1</script>Only trusted devices read this code.'
    record=documents.browser_json(left,'args=>fileProofProvider.upload(args.channel,new File([args.text],"private-source.html",{type:"text/html"}),{requestId:crypto.randomUUID()})',{'channel':channel,'text':private_text})
    received=documents.poll(lambda:documents.browser_json(right,'channel=>fileProofProvider.list(channel)',channel),lambda rows:any(value['id']==record['id'] for value in rows),'Encrypted file metadata did not reach recipient')
    selected=next(value for value in received if value['id']==record['id'])
    right.evaluate('''async file=>{const{mountFile}=await import('/file-proof.tsx');mountFile(file);}''',selected)
    panel=right.get_by_role('dialog',name='Preview private-source.html',exact=True)
    expect(panel.locator('.fc-file-code')).to_have_text(private_text)
    assert right.evaluate('()=>window.fileProofXss===undefined')
    panel.get_by_label('Add a file comment',exact=True).fill('Private encrypted recipient review.')
    panel.get_by_role('button',name='Post comment',exact=True).click()
    expect(panel.get_by_text('Private encrypted recipient review.',exact=True)).to_be_visible()
    comments=documents.poll(lambda:documents.browser_json(left,'file=>fileProofExperience.comments(file)',record),lambda rows:any(value['text']=='Private encrypted recipient review.' and value['created_by']==bob['user_id'] for value in rows),'Encrypted peer file comment missing')
    assert len(comments)==1 and not native_requests
    assert panel.get_by_role('button',name='Set up Office editing',exact=True).count()==0
    wire=right.evaluate('''async channel=>{const events=await fileProofSecure.events(channel);const comment=events.find(event=>event.getType()==='eu.freecollab.file.comment');return {type:comment.getWireType(),wire:JSON.stringify(comment.getWireContent())}}''',channel)
    assert wire['type']=='m.room.encrypted' and 'Private encrypted recipient review.' not in wire['wire']
    assert helpers.api(base,'GET','/files/channels/'+channel,alice)==[]
    panel.get_by_role('button',name='Close file preview',exact=True).click()
    results['real_matrix_file_preview_and_authenticated_comments_client_only_encrypted_wire']=True
    link=documents.browser_json(left,'args=>fileProofCloud.addLink(...args)',[workspace['id'],channel,{'provider':'box','title':'Private chosen external file','url':'https://app.box.com/file/20','external_access_acknowledged':True}])
    resolved=documents.browser_json(right,'args=>fileProofCloud.links(...args)',[workspace['id'],channel]);assert any(value['id']==link['id'] and value['fields']['title']=='Private chosen external file' for value in resolved['items'])
    opaque=helpers.api(base,'GET','/workspaces/'+workspace['id']+'/channels/'+channel+'/cloud-files/links',alice)['items'][0]
    assert opaque['content_mode']=='matrix' and 'fields' not in opaque
    wire=right.evaluate('''async args=>{const event=await fileProofSecure.getDecryptedEvent(args.channel,args.id);return{type:event.getWireType(),wire:JSON.stringify(event.getWireContent())}}''',{'channel':channel,'id':opaque['matrix_event_id']})
    assert wire['type']=='m.room.encrypted' and 'Private chosen external file' not in wire['wire'] and 'app.box.com' not in wire['wire']
    with sqlite3.connect(private/'proof.db') as db:
        stored=db.execute('SELECT content_mode,encrypted_payload,matrix_event_id FROM cloudfilelink WHERE id=?',(link['id'],)).fetchone();assert stored[0]=='matrix' and stored[1] is None and stored[2]==opaque['matrix_event_id']
    results['real_matrix_cloud_file_link_self_trust_peer_resolution_opaque_db_api_wire']=True
    print('PASS real Matrix file HTML rendered as inert code, authenticated peer comment, zero native comment/plaintext requests and m.room.encrypted wire',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--secure',action='store_true');options=parser.parse_args()
    processes=[];streams=[];results={};stage='private services startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-file-experience-') as directory:
        private=Path(directory)
        try:
            api_port,app_port=helpers.port(),helpers.port();base='http://127.0.0.1:'+str(api_port);app='http://127.0.0.1:'+str(app_port)
            environment=os.environ.copy();environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),
                ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),
                MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message-keys.json'),MATRIX_ENABLED='false',PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),MAIL_DELIVERY_MODE='disabled',OIDC_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',CLOUD_FILES_ENABLED='true',PYTHONPATH=str(ROOT/'backend'))
            environment['NO_PROXY']=','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            if options.secure:environment.update(documents.retained_matrix_environment())
            python=os.getenv('BACKEND_PYTHON') or str(ROOT/'backend/.venv/bin/python')
            source=private/'api.py';source.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',proxy_headers=False,access_log=False)\n')
            frontend=private/'frontend';shutil.copytree(ROOT/'frontend',frontend,ignore=shutil.ignore_patterns('node_modules','dist','.git','__pycache__'))
            (frontend/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
            (frontend/'file-proof.tsx').write_text('''import React from 'react';
import {createRoot} from 'react-dom/client';
import {FilePreview} from './src/components/FilePreview';
export function mountFile(file:any){const host=document.createElement('div');document.body.append(host);const root=createRoot(host);root.render(<FilePreview file={file} onClose={()=>{root.unmount();host.remove();}}/>);}
''')
            vite=helpers.vite_launcher(private,base,app_port);vite.write_text(vite.read_text().replace('root:'+json.dumps(str(ROOT/'frontend')),'root:'+json.dumps(str(frontend))).replace(json.dumps(str(private/'vite-cache')),json.dumps(str(frontend/'.vite-cache'))).replace('server:{host:','server:{fs:{allow:'+json.dumps([str(private),str(ROOT/'frontend')])+'},host:'))
            for command,name,cwd in [([python,str(source)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',frontend)]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(base+'/ready',processes);helpers.ready(app+'/',processes)
            users=[];emails=[]
            for name in ['File Alice','File Bob']:
                email='file-'+secrets.token_hex(6)+'@example.com';emails.append(email);users.append(helpers.api(base,'POST','/auth/register',data={'name':name,'email':email,'password':PASSWORD}))
            alice,bob=users;workspace=helpers.api(base,'POST','/workspaces',alice,{'name':'File experience studio'})
            invite=helpers.api(base,'POST','/workspaces/'+workspace['id']+'/invites',alice,{'role':'member'});helpers.api(base,'POST','/workspaces/invites/accept',bob,{'token':invite['token']})
            channel=helpers.api(base,'POST','/channels',alice,{'name':'Project files','workspace_id':workspace['id'],'member_ids':[bob['user_id']]})['id']
            video=private/'sample.webm';subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=gray:s=160x90:d=1','-c:v','libvpx','-an',str(video)],check=True,capture_output=True)
            audio=io.BytesIO()
            with wave.open(audio,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b'\0\0'*8000)
            original=docx('Passive Word content');newest=docx('Saved back through real WebDAV')
            fixtures=[('source.html',b'<img src="https://never-fetch.invalid/pixel" onerror="window.fileProofXss=1">','text/html'),('data.csv',b'name,note\nAlice,"literal, value"\nBob,=1+1\n','text/csv'),('sample.wav',audio.getvalue(),'audio/wav'),('sample.webm',video.read_bytes(),'video/webm'),
                ('brief.docx',original,DOCX),('sheet.xlsx',package({'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c t="inlineStr"><is><t>Passive sheet cell</t></is></c></row></sheetData></worksheet>'}),XLSX),
                ('slides.pptx',package({'ppt/slides/slide1.xml':'<p:sld xmlns:p="urn:p" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Passive slide title</a:t></p:sld>'}),PPTX),
                ('pixel.png',base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR1sAAAAASUVORK5CYII='),'image/png')]
            stored={name:upload(base,alice,channel,name,body,mime) for name,body,mime in fixtures}
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox'])
                contexts=[browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce',accept_downloads=True) for _ in users];pages=[context.new_page() for context in contexts];errors=[];external=[]
                for page in pages:
                    page.set_default_timeout(45000);page.on('pageerror',lambda failure:errors.append(type(failure).__name__));page.on('request',lambda request:external.append(request.url) if 'never-fetch.invalid' in request.url else None)
                try:
                    stage='safe native HTML, CSV, audio/video and OOXML previews'
                    left,right=pages;panel=login(left,app,alice,emails[0],workspace['id'])
                    preview=open_preview(left,'source.html');expect(preview.locator('.fc-file-code')).to_have_text(fixtures[0][1].decode());assert left.evaluate('()=>window.fileProofXss===undefined');assert preview.locator('.fc-file-code img,.fc-file-code script,iframe,object,embed').count()==0
                    preview.get_by_role('button',name='Close file preview',exact=True).click()
                    preview=open_preview(left,'data.csv');expect(preview.get_by_role('cell',name='literal, value',exact=True)).to_be_visible();expect(preview.get_by_role('cell',name='=1+1',exact=True)).to_be_visible();preview.get_by_role('button',name='Close file preview',exact=True).click()
                    for name,tag in [('sample.wav','audio'),('sample.webm','video')]:
                        preview=open_preview(left,name);expect(preview.locator(tag)).to_be_visible();left.wait_for_function('tag=>{const media=document.querySelector(".fc-safe-file-preview "+tag);return media&&media.readyState>=1&&Number.isFinite(media.duration)}',arg=tag);preview.get_by_role('button',name='Close file preview',exact=True).click()
                    for name,text in [('brief.docx','Passive Word content'),('sheet.xlsx','Passive sheet cell'),('slides.pptx','Passive slide title')]:
                        preview=open_preview(left,name);expect(preview.get_by_text(text,exact=True)).to_be_visible();assert preview.locator('iframe,object,embed').count()==0;preview.get_by_role('button',name='Close file preview',exact=True).click()
                    assert not external;results['inert_html_csv_real_audio_video_and_passive_docx_xlsx_pptx_previews']=True
                    stage='gallery thumbnails and immutable native file comments'
                    panel.get_by_role('button',name='Gallery',exact=True).click();gallery=panel.get_by_role('list',name='File gallery',exact=True);expect(gallery.locator('img')).to_be_visible();gallery.get_by_role('button',name='Open brief.docx',exact=True).click()
                    preview=left.get_by_role('dialog',name='Preview brief.docx',exact=True);preview.get_by_label('Add a file comment',exact=True).fill('Review belongs to the original file version.');preview.get_by_role('button',name='Post comment',exact=True).click();expect(preview.get_by_text('Review belongs to the original file version.',exact=True)).to_be_visible()
                    second=login(right,app,bob,emails[1],workspace['id']);peer=open_preview(right,'brief.docx');expect(peer.get_by_text('Review belongs to the original file version.',exact=True)).to_be_visible();assert peer.get_by_role('button',name='Set up Office editing',exact=True).count()==0;peer.get_by_role('button',name='Close file preview',exact=True).click()
                    results['gallery_real_authenticated_image_thumbnail_and_cross_member_file_comments']=True
                    stage='reauthenticated Office handoff and real network WebDAV saveback'
                    preview.get_by_role('button',name='Set up Office editing',exact=True).click();preview.get_by_label('Current password',exact=True).fill(PASSWORD)
                    with left.expect_response(lambda response:'/office-sessions' in response.url and response.request.method=='POST') as created:preview.get_by_role('button',name='Create Office session',exact=True).click()
                    assert created.value.status==201;grant=created.value.json();handoff=preview.get_by_role('link',name='Open in desktop Office',exact=True).get_attribute('href');assert handoff.startswith('ms-word:ofe|u|'+app+'/api/office-edit/') and alice['access_token'] not in handoff
                    status,headers,content=dav(base,grant['path'],'GET');assert status==200 and content==original;etag=headers.get('etag') or headers.get('ETag')
                    lockbody=b'<d:lockinfo xmlns:d="DAV:"><d:lockscope><d:exclusive/></d:lockscope><d:locktype><d:write/></d:locktype></d:lockinfo>'
                    status,headers,_=dav(base,grant['path'],'LOCK',lockbody,{'Depth':'0'});assert status==200;lock=headers.get('lock-token') or headers.get('Lock-Token')
                    status,saved,_=dav(base,grant['path'],'PUT',newest,{'If-Match':etag,'If':'('+lock+')','Content-Type':DOCX});assert status==204
                    assert dav(base,grant['path'],'GET')[2]==newest
                    assert helpers.api(base,'GET','/files/'+stored['brief.docx']['id'],alice,raw=True)==original
                    dav(base,grant['path'],'UNLOCK',headers={'Lock-Token':lock})
                    preview.get_by_role('button',name='Refresh saved versions',exact=True).click();expect(preview).not_to_be_visible();panel.get_by_role('button',name='List',exact=True).click();preview=open_preview(left,'brief.docx');expect(preview.get_by_text('Saved back through real WebDAV',exact=True)).to_be_visible();expect(preview.get_by_text('Review belongs to the original file version.',exact=True)).to_be_visible();expect(preview.get_by_text('On another version of this document',exact=True)).to_be_visible()
                    results['reauthenticated_no_jwt_office_protocol_handoff_real_webdav_new_version_exact_old_bytes']=True
                    stage='320px mobile file discussion and preview'
                    left.set_viewport_size({'width':320,'height':900});assert left.evaluate('document.documentElement.scrollWidth<=innerWidth+1');assert preview.evaluate('element=>element.scrollWidth<=element.clientWidth+1')
                    REPORT.parent.mkdir(parents=True,exist_ok=True);left.screenshot(path=str(REPORT.with_name('file-experience-mobile.png')))
                    preview.get_by_role('button',name='Close file preview',exact=True).click();panel.get_by_role('button',name='Close channel panel',exact=True).click()
                    second.get_by_role('button',name='Close channel panel',exact=True).click()
                    print('PASS native actual Chromium safe previews/media/Office text, gallery/thumbs/comments, reauthentication and real network WebDAV immutable saveback',flush=True)
                    if options.secure:
                        stage='real Matrix encrypted previews and authenticated file comments';secure_proof(base,workspace,users,pages,results,private)
                    stage='personal Swedish preference reflected in owned file UI'
                    helpers.api(base,'PUT','/preferences/language',alice,{'locale':'sv'});left.reload(wait_until='domcontentloaded')
                    left.wait_for_function('document.documentElement.lang==="sv"')
                    label=left.evaluate('async()=>{const{t}=await import("/src/i18n/index.tsx");return t("Channel files")}');left.get_by_role('button',name=label,exact=True).click()
                    name=left.evaluate('async()=>{const{t}=await import("/src/i18n/index.tsx");return t("Preview {value0}",{value0:"brief.docx"})}');left.get_by_role('button',name=name,exact=True).click()
                    expect(left.get_by_role('heading',name='Filkommentarer',exact=True)).to_be_visible();expect(left.get_by_label('Lägg till en filkommentar',exact=True)).to_be_visible()
                    assert not errors, 'Unexpected Chromium page exception';results['swedish_per_account_ui_and_mobile_320px_zero_browser_exceptions']=True
                    with sqlite3.connect(private/'proof.db') as db:
                        rows=db.execute('SELECT encrypted_text FROM filecomment').fetchall();assert len(rows)==1 and 'Review belongs' not in rows[0][0]
                    print('PASS native file previews, inert HTML/CSV, real media codecs, bounded Office text, gallery/thumbs, authenticated comments, Office handoff, real HTTP DAV new immutable version, Swedish account UI and320px',flush=True)
                finally:
                    for context in contexts:context.close()
                    browser.close()
        except Exception as failure:
            detail=re.sub(r'/office-edit/[0-9a-f]+/[A-Za-z0-9_-]+/','/office-edit/[redacted]/',str(failure).replace(PASSWORD,'[redacted]'));detail=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+','[redacted]',detail)
            print('FAIL file experience stage: '+stage+'; '+type(failure).__name__+'\n'+detail[:2500],file=sys.stderr,flush=True)
            if (private/'vite.log').exists():print((private/'vite.log').read_text()[-2500:],file=sys.stderr,flush=True)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams:stream.close()
    REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'scope':'Private actual Chromium/API plus network WebDAV'+('; actual retained Matrix with new private identities' if options.secure else '')+'; no desktop vendor Office execution or live external providers','results':results,'cleanup':'Own API/Vite/browser/tempDB/blobs/keys removed; retained Matrix keys/container preserved'},indent=2)+'\n')


if __name__=='__main__':main()
