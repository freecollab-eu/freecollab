#!/usr/bin/env python3
"""Run actual OpenSearch 2.19.1 k-NN proof with authenticated, verified local TLS.

Requires the official SHA512-verified bundle extracted in /tmp by the caller.
No container/cluster/provider credentials or external service data are used.
"""
import hashlib,json,os,re,secrets,signal,socket,socketserver,select,threading,subprocess,sys,tarfile,tempfile,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import ipaddress
import httpx
import ssl
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
ROOT=Path(__file__).resolve().parents[2]
SERVICE=Path(os.getenv('CONTENT_OPENSEARCH_BUNDLE','/tmp/freecollab-opensearch-service/opensearch-2.19.1'))
REPORT=ROOT.parent/'.freecollab/proof-results/gap-completion-20261025/content-search/opensearch/report.json'
SOURCES=['backend/app/services/search_sources.py','backend/app/services/semantic_opensearch.py','backend/app/services/semantic_index.py','backend/app/api/content_completion.py','backend/app/models/content_completion.py','backend/tests/test_semantic_opensearch.py','backend/tests/run_semantic_opensearch_proof.py']
def port():
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
def main():
 REPORT.parent.mkdir(parents=True,exist_ok=True);before={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in SOURCES};process=None
 with tempfile.TemporaryDirectory(prefix='freecollab-opensearch-private-') as directory:
  private=Path(directory);password='Content-proof!'+secrets.token_urlsafe(32);environment=os.environ.copy();environment.update(OPENSEARCH_INITIAL_ADMIN_PASSWORD=password,OPENSEARCH_JAVA_OPTS='-Xms256m -Xmx256m -XX:ActiveProcessorCount=2')
  try:
   # Restore only this disposable fixture's original demo inputs on retries.
   with tarfile.open('/tmp/freecollab-opensearch-2.19.1.tar.gz') as archive:
    for relative in ['config/opensearch.yml','config/opensearch-security/internal_users.yml']:
     (SERVICE/relative).write_bytes(archive.extractfile('opensearch-2.19.1/'+relative).read())
   log=(private/'service.log').open('w');installed=subprocess.run(['bash',str(SERVICE/'plugins/opensearch-security/tools/install_demo_configuration.sh'),'-y'],cwd=SERVICE,env=environment,stdout=log,stderr=log,timeout=45);assert installed.returncode==0,'OpenSearch demo fixture initialization failed'
   now=datetime.now(timezone.utc);key=rsa.generate_private_key(public_exponent=65537,key_size=2048);name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'FreeCollab disposable test CA')])
   ca=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=2)).not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256())
   serverkey=rsa.generate_private_key(public_exponent=65537,key_size=2048);server=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])).issuer_name(name).public_key(serverkey.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=2)).not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).sign(key,hashes.SHA256())
   config=SERVICE/'config';(config/'localhost.pem').write_bytes(server.public_bytes(serialization.Encoding.PEM));(config/'localhost-key.pem').write_bytes(serverkey.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));(config/'localhost-key.pem').chmod(0o600);(config/'local-root.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
   text=(config/'opensearch.yml').read_text();http_port=port()
   values={'plugins.security.ssl.http.pemcert_filepath':'localhost.pem','plugins.security.ssl.http.pemkey_filepath':'localhost-key.pem','plugins.security.ssl.http.pemtrustedcas_filepath':'local-root.pem','network.host':'127.0.0.1','http.port':str(http_port),'transport.port':str(port()),'discovery.type':'single-node','bootstrap.memory_lock':'false','plugins.security.allow_default_init_securityindex':'true','path.data':str(private/'data'),'path.logs':str(private/'logs')}
   for setting,value in values.items():
    pattern=r'^'+re.escape(setting)+r':.*$'
    if re.search(pattern,text,re.MULTILINE):text=re.sub(pattern,setting+': '+value,text,flags=re.MULTILINE)
    else:text+='\n'+setting+': '+value+'\n'
   (config/'opensearch.yml').write_text(text)
   process=subprocess.Popen([str(SERVICE/'bin/opensearch')],cwd=SERVICE,env=environment,stdout=log,stderr=log,start_new_session=True)
   url='https://localhost:'+str(http_port);tls=ssl.create_default_context(cafile=str(config/'local-root.pem'));info=None
   deadline=time.monotonic()+100
   with httpx.Client(verify=tls,auth=('admin',password),trust_env=False,timeout=2) as client:
    while time.monotonic()<deadline:
     assert process.poll() is None,'OpenSearch fixture exited before readiness'
     try:
      response=client.get(url)
      if response.status_code==200:info=response.json();break
     except httpx.HTTPError:pass
     time.sleep(.4)
   assert info and info['version']['number']=='2.19.1','OpenSearch fixture did not become ready'
   proxy_hits=[]
   class Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
     request=b''
     while b'\r\n\r\n' not in request and len(request)<8192:
      part=self.request.recv(4096)
      if not part:return
      request+=part
     first=request.split(b'\r\n',1)[0].decode('ascii');expected='CONNECT localhost:'+str(http_port)+' HTTP/1.1'
     if first!=expected:self.request.sendall(b'HTTP/1.1 403 Forbidden\r\n\r\n');return
     proxy_hits.append(True)
     with socket.create_connection(('127.0.0.1',http_port),timeout=3) as upstream:
      self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n');connections=[self.request,upstream]
      while True:
       readable,_,_=select.select(connections,[],[],20)
       if not readable:return
       for incoming in readable:
        data=incoming.recv(65536)
        if not data:return
        (upstream if incoming is self.request else self.request).sendall(data)
   proxy=socketserver.ThreadingTCPServer(('127.0.0.1',0),Tunnel);proxy.daemon_threads=True;threading.Thread(target=proxy.serve_forever,daemon=True).start();proxy_url='http://127.0.0.1:'+str(proxy.server_address[1])
   run_env=os.environ.copy();run_env.update(HTTPS_PROXY=proxy_url,https_proxy=proxy_url,NO_PROXY='',no_proxy='',CONTENT_OPENSEARCH_TEST_URL=url,CONTENT_OPENSEARCH_TEST_USERNAME='admin',CONTENT_OPENSEARCH_TEST_PASSWORD=password,CONTENT_OPENSEARCH_TEST_CA=str(config/'local-root.pem'))
   tested=subprocess.run([sys.executable,'-m','pytest','tests/test_semantic_opensearch.py','-q'],cwd=ROOT/'backend',env=run_env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
   output=tested.stdout.replace(password,'[fixture secret removed]');(REPORT.parent/'pytest.log').write_text(output)
   proxy.shutdown();proxy.server_close();assert proxy_hits,'Product vector transport bypassed the configured HTTPS proxy'
   assert tested.returncode==0,'Actual OpenSearch adapter regressions failed; see isolated proof pytest.log'
   assert before=={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in SOURCES},'Owned inputs changed during actual OpenSearch proof'
   REPORT.write_text(json.dumps({'ok':True,'service':'OpenSearch','version':info['version']['number'],'authenticated':True,'certificate_and_hostname_verified':True,'inherited_https_proxy_verified':True,'proxy_tunnels':len(proxy_hits),'source_sha256':before,'scope':__doc__,'checks':['real lucene k-NN indirect retrieval','typed vector-only index without source text','incremental current-source deletion','wrong backend credentials fail closed','current native membership denial','real channel vector erasure','operator URL/TLS policy','caller transaction neutrality']},indent=2)+'\n');print(json.dumps({'ok':True,'report':str(REPORT)}))
  except Exception as error:
   REPORT.write_text(json.dumps({'ok':False,'error':str(error)[:2000],'source_sha256':before},indent=2)+'\n')
   if (private/'service.log').exists():(REPORT.parent/'service-failure.log').write_text((private/'service.log').read_text().replace(password,'[fixture secret removed]'))
   raise
  finally:
   if process and process.poll() is None:
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
   if 'log' in locals():log.close()
   (SERVICE/'config/localhost-key.pem').unlink(missing_ok=True)
if __name__=='__main__':main()
