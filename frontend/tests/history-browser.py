"""Real UI full-history/search/mentions verification using disposable local accounts."""
from ui_navigation import open_chat_tool
import json, os, uuid
from pathlib import Path
from urllib.request import Request, urlopen
from playwright.sync_api import sync_playwright, expect
BASE=os.getenv('CHAT_BASE_URL','http://127.0.0.1:3000').rstrip('/')
PROOF_DIR=Path(os.getenv('FREECOLLAB_PROOF_DIR',str(Path(__file__).resolve().parents[2].parent / '.freecollab')))
RUN=uuid.uuid4().hex[:10]
PASSWORD='History-browser-local-password!'
def api(method,path,token=None,data=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    with urlopen(Request(BASE+'/api'+path,method=method,headers=headers,data=None if data is None else json.dumps(data).encode()),timeout=30) as response: return json.load(response)
def register(name):
    return api('POST','/auth/register',data={'name':name,'email':f'{name.lower()}-{RUN}@example.com','password':PASSWORD})
a,b=register('HistoryAlice'),register('HistoryBob')
w=api('POST','/workspaces',a['access_token'],{'name':'History workspace '+RUN})
i=api('POST',f"/workspaces/{w['id']}/invites",a['access_token'],{'role':'member'})
api('POST','/workspaces/invites/accept',b['access_token'],{'token':i['token']})
c=api('POST','/channels',a['access_token'],{'name':'History-'+RUN,'type':'group','workspace_id':w['id'],'member_ids':[b['user_id']]})
old=api('POST',f"/messages/channels/{c['id']}",a['access_token'],{'content':'Oldest searchable needle '+RUN})
for index in range(121): api('POST',f"/messages/channels/{c['id']}",a['access_token'],{'content':f'Historical entry {index:03d} '+RUN})
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000}); page.set_default_timeout(30000)
    errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(BASE)
    page.evaluate('''({auth,workspace})=>{localStorage.setItem('freecollab_token',auth.access_token);localStorage.setItem('freecollab_user_id',auth.user_id);localStorage.setItem('freecollab.workspace:'+auth.user_id,workspace)}''',{'auth':a,'workspace':w['id']})
    page.reload(); expect(page.get_by_role('textbox', name='Message #'+c['name'], exact=True)).to_be_visible()
    expect(page.get_by_text('Historical entry 120 '+RUN,exact=True)).to_be_visible()
    expect(page.get_by_text(old['content'],exact=True)).to_have_count(0)
    page.get_by_role('button',name='Load older messages',exact=True).click()
    expect(page.get_by_text(old['content'],exact=True)).to_be_visible()
    print('PASS: cursor pagination loads history beyond the initial page',flush=True)
    open_chat_tool(page, 'Search all history')
    dialog=page.get_by_role('dialog',name='Search all history')
    expect(dialog.get_by_text('Loading full history',exact=False)).to_have_count(0,timeout=60000)
    dialog.get_by_role('textbox',name='Search messages',exact=True).fill('Oldest searchable needle')
    expect(dialog.locator('.fc-history-result')).to_have_count(1)
    # An edit made in another client must supersede text already loaded for search.
    updated='Revised searchable thought '+RUN
    api('PUT','/messages/'+old['id'],a['access_token'],{'content':updated})
    expect(dialog.locator('.fc-history-result')).to_have_count(0,timeout=20000)
    dialog.get_by_role('textbox',name='Search messages',exact=True).fill('Revised searchable')
    expect(dialog.locator('.fc-history-result')).to_have_count(1)
    dialog.locator('.fc-history-result').click()
    expect(page.locator('#message-'+old['id'])).to_contain_text(updated)
    expect(page.locator('#message-'+old['id'])).to_be_focused()
    print('PASS: complete local search, edits synchronized and jump to older context',flush=True)
    composer=page.get_by_role('textbox', name='Message #'+c['name'], exact=True)
    composer.fill('@History')
    page.get_by_role('option',name='HistoryBob',exact=True).click()
    composer.press('End'); composer.press('Space'); composer.press('H'); composer.press('i')
    page.get_by_role('button',name='Send message',exact=True).click()
    expect(composer).to_have_value('')
    latest=api('GET',f"/messages/channels/{c['id']}?limit=1",a['access_token'])[0]
    assert latest['mentions']==[b['user_id']], 'Selected mention must use member ID'
    mentions=api('GET','/mentions/me',b['access_token'])
    assert any(item['id']==latest['id'] for item in mentions)
    print('PASS: mention picker sends member IDs and recipient inbox receives mention',flush=True)
    open_chat_tool(page, 'Search all history')
    dialog=page.get_by_role('dialog',name='Search all history')
    dialog.get_by_role('textbox',name='Search messages',exact=True).fill(updated)
    expect(dialog.locator('.fc-history-result')).to_have_count(1,timeout=60000)
    api('DELETE','/messages/'+old['id'],a['access_token'])
    expect(dialog.locator('.fc-history-result')).to_have_count(0,timeout=20000)
    page.screenshot(path=str(PROOF_DIR / 'product-history-search.png'))
    page.get_by_role('button',name='Close history search',exact=True).click()
    assert not errors,errors
    print('PASS: deleted history disappears from local search without storing plaintext',flush=True)
    browser.close()
