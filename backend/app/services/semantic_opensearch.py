"""Opt-in authenticated OpenSearch k-NN mirror of native vectors and opaque refs.

No content text or encrypted-channel vectors are sent. Operator configuration is
trusted deployment input, never a URL received from a product user's request.
"""
from hashlib import sha256
import ipaddress
import json
import math
import re
import ssl
import time
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from ..config import settings
from ..models.content_completion import ChannelSemanticIndex
from . import search_sources
from .access import lock_channel_writes

DIMENSION = 12
PROTOCOL = 'freecollab-native-lsa-knn/1'
FAILURE = 'The configured semantic vector backend is unavailable or incompatible'

def configured():
    return bool(settings.semantic_opensearch_url.strip())


def options():
    try:
        raw = settings.semantic_opensearch_url.strip(); url = urlsplit(raw)
        if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password or url.query or url.fragment or any(char.isspace() for char in raw):
            raise ValueError()
        # Private local HTTP is useful for an operator-owned container network;
        # public destinations always require certificate-verified HTTPS.
        if url.scheme == 'http':
            try:
                address = ipaddress.ip_address(url.hostname)
                private = address.is_loopback or address.is_private
            except ValueError:
                private = url.hostname in {'localhost', 'opensearch'} or url.hostname.endswith(('.internal', '.local'))
            if not private:
                raise ValueError()
        index = settings.semantic_opensearch_index
        username = settings.semantic_opensearch_username
        password = settings.semantic_opensearch_password.get_secret_value()
        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,62}', index) or not username or ':' in username or not password or any(ord(char) < 32 for char in username):
            raise ValueError()
        tls = ssl.create_default_context()
        if settings.semantic_opensearch_ca_bundle:
            tls.load_verify_locations(cafile=settings.semantic_opensearch_ca_bundle)
        base = raw.rstrip('/')
        identity = sha256((base + '\0' + index + '\0' + username).encode()).hexdigest()
        return base, index, (username, password), tls, identity
    except (ValueError, OSError, TypeError):
        raise HTTPException(503, FAILURE) from None


class Backend:
    def __init__(self):
        self.base, self.index, authentication, tls, self.identity = options()
        self.deadline = time.monotonic() + settings.semantic_opensearch_timeout
        self.client = httpx.Client(auth=authentication, verify=tls, follow_redirects=False, trust_env=True)

    def close(self):
        self.client.close()

    def request(self, method, suffix, body=None, *, missing=False, creation=False, ndjson=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise HTTPException(503, FAILURE)
        try:
            payload = body.encode() if ndjson else json.dumps(body, separators=(',', ':')).encode() if body is not None else None
            with self.client.stream(method, self.base + suffix, content=payload,
                    headers={'Content-Type':'application/x-ndjson' if ndjson else 'application/json'}, timeout=remaining) as response:
                if response.status_code == 404 and missing:
                    return None
                # A parallel channel can win initial index creation. Its mapping
                # is still verified by ensure_index before any document write.
                if creation and response.status_code in (400, 409):
                    return None
                if not 200 <= response.status_code < 300:
                    raise ValueError()
                content = bytearray()
                for part in response.iter_bytes():
                    content.extend(part)
                    if len(content) > 2 * 1024 * 1024 or time.monotonic() > self.deadline:
                        raise ValueError()
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
            # Backend response text, credentials and configured destinations do
            # not become a user-visible error or an application log entry.
            raise HTTPException(503, FAILURE) from None

    def ensure_index(self, *, create=True):
        info = self.request('GET', '/')
        if not isinstance(info.get('version'), dict) or info['version'].get('distribution') != 'opensearch':
            raise HTTPException(503, FAILURE)
        path = '/' + self.index
        mapping = self.request('GET', path + '/_mapping', missing=True)
        if mapping is None and not create:
            return False
        if mapping is None:
            self.request('PUT', path, {'settings':{'index':{'knn':True}}, 'mappings':{
                'dynamic':'strict', '_meta':{'freecollab_semantic_protocol':PROTOCOL},
                'properties':{**{key:{'type':'keyword'} for key in ['channel_id','source_id','kind','version','generation']},
                    'vector':{'type':'knn_vector','dimension':DIMENSION,
                        'method':{'name':'hnsw','space_type':'cosinesimil','engine':'lucene'}}}}}, creation=True)
            mapping = self.request('GET', path + '/_mapping')
        try:
            schema = mapping[self.index]['mappings']
            vector = schema['properties']['vector']
            if schema['_meta']['freecollab_semantic_protocol'] != PROTOCOL or schema.get('dynamic') != 'strict' or vector['type'] != 'knn_vector' or vector['dimension'] != DIMENSION or vector.get('method', {}).get('engine') != 'lucene':
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise HTTPException(503, FAILURE) from None
        return True

    def publish(self, channel_id, items, model, generation):
        lines=[]
        for index,item in enumerate(items):
            vector = document_vector(model,index)
            if not any(vector):
                continue
            identifier = sha256((channel_id+'\0'+item['kind']+'\0'+item['id']).encode()).hexdigest()
            lines.extend([json.dumps({'index':{'_index':self.index,'_id':identifier}}),json.dumps({
                'channel_id':channel_id,'source_id':item['id'],'kind':item['kind'],'version':item['version'],
                'generation':generation,'vector':vector})])
        if lines:
            result = self.request('POST','/_bulk?refresh=wait_for','\n'.join(lines)+'\n',ndjson=True)
            outcomes=result.get('items', [])
            if result.get('errors') is not False or not isinstance(outcomes,list) or len(outcomes) != len(lines)//2 or any(not isinstance(item,dict) or not isinstance(item.get('index'),dict) or item['index'].get('status') not in (200,201) or 'error' in item['index'] for item in outcomes):
                raise HTTPException(503, FAILURE)
        removed = self.request('POST','/'+self.index+'/_delete_by_query?refresh=true&conflicts=proceed',{
            'query':{'bool':{'filter':[{'term':{'channel_id':channel_id}}],
                'must_not':[{'term':{'generation':generation}}]}}})
        check_deleted(removed)

    def search(self, channel_id, generation, vector, limit):
        if not any(vector):
            return []
        result = self.request('POST','/'+self.index+'/_search',{
            'size':limit,'_source':['channel_id','source_id','kind','version','generation'],
            'query':{'knn':{'vector':{'vector':vector,'k':limit,'filter':{'bool':{'filter':[
                {'term':{'channel_id':channel_id}},{'term':{'generation':generation}}]}}}}}})
        try:
            hits=result['hits']['hits']
            if not isinstance(hits,list) or len(hits)>limit or any(not isinstance(hit,dict) or not isinstance(hit.get('_source'),dict) for hit in hits):
                raise ValueError()
            return hits
        except (KeyError,TypeError,ValueError):
            raise HTTPException(503,FAILURE) from None


def check_deleted(result):
    if result is not None and (result.get('timed_out') is not False or result.get('failures') != [] or result.get('version_conflicts') != 0):
        raise HTTPException(503, FAILURE)


def padded(values):
    result=[float(value) for value in values]+[0.0]*max(0,DIMENSION-len(values))
    if len(result)!=DIMENSION or any(not math.isfinite(value) for value in result):
        raise HTTPException(503,FAILURE)
    length=math.sqrt(sum(value*value for value in result))
    return [value/length for value in result] if length>1e-12 else [0.0]*DIMENSION


def document_vector(model,index):
    return padded([axis[index]*math.sqrt(value) for axis,value in zip(model['axes'],model['eigenvalues'])])


def query_vector(model,query):
    from collections import Counter
    from .semantic_index import words
    counts=Counter(words(query));vector={term:(1+math.log(count))*model['idf'][term] for term,count in counts.items() if term in model['idf']}
    length=math.sqrt(sum(value*value for value in vector.values())) or 1;vector={term:value/length for term,value in vector.items()}
    affinities=[sum(value*row.get(term,0) for term,value in vector.items()) for row in model['vectors']]
    return padded([sum(x*y for x,y in zip(axis,affinities))/math.sqrt(value) for axis,value in zip(model['axes'],model['eigenvalues'])])


def retrieve(session,channel_id,actor_id,payload,revision,query,limit):
    from . import semantic_index
    from .request_guard import recheck_actor
    lock_channel_writes(session,channel_id);session.expire_all();search_sources.authorize(session,channel_id,actor_id)
    row=session.get(ChannelSemanticIndex,channel_id)
    if not row:
        raise HTTPException(409,'The semantic index changed; retry search')
    if row.revision!=revision:
        row,payload=semantic_index._load(session,channel_id)
    # Hold the same native writer fence through vector publication/query. A
    # secure transition cannot purge and then be followed by a late native upsert.
    items=[item for item in payload['items'] if search_sources.still_current(session,item) and search_sources.allowed(session,channel_id,item)]
    model=payload['model'] if len(items)==len(payload['items']) else semantic_index.latent_model(items)
    generation=sha256(json.dumps([row.revision,[(item['kind'],item['id'],item['version']) for item in items]],separators=(',',':')).encode()).hexdigest()
    backend=Backend()
    try:
        backend.ensure_index()
        if row.mirror_backend!=backend.identity or row.mirror_generation!=generation:
            backend.publish(channel_id,items,model,generation)
            row.mirror_backend=backend.identity;row.mirror_generation=generation;session.add(row)
        hits=backend.search(channel_id,generation,query_vector(model,query),min(100,limit*4))
        by_id={(item['kind'],item['id']):item for item in items};selected=[]
        seen=set()
        for hit in hits:
            source=hit.get('_source',{});item=by_id.get((source.get('kind'),source.get('source_id')))
            if (source.get('kind'),source.get('source_id')) in seen:
                continue
            if not item or source.get('channel_id')!=channel_id or source.get('generation')!=generation or source.get('version')!=item['version']:
                continue
            # Returned fields are trusted only after current source validation.
            seen.add((item['kind'],item['id']))
            actual=search_sources.resolve_canonical(session,actor_id,item['kind'],item['id'],channel_id,item['version'],commit=False)
            score=hit.get('_score');score=float(score) if isinstance(score,(int,float)) and math.isfinite(score) else 0
            selected.append({**actual,'score':min(1.0,max(0.0,score))})
            if len(selected)>=limit:
                break
        recheck_actor(session,actor_id);search_sources.authorize(session,channel_id,actor_id)
        index_revision = row.revision
        session.commit()
        return {'items':selected,'complete':payload['complete'] and len(items)==len(payload['items']),
            'engine':'opensearch-lsa-knn-v1','index_revision':index_revision}
    finally:
        backend.close()


def purge_channel(session,channel_id):
    """Caller already holds channel writer fence; never commits its transaction.

    Source erasure, retention and secure enable call this before deleting the
    native cache. A configured unavailable backend rejects/retries the operation
    rather than claiming remote vectors were removed.
    """
    if not configured():
        return
    backend=Backend()
    try:
        if not backend.ensure_index(create=False):
            return
        removed=backend.request('POST','/'+backend.index+'/_delete_by_query?refresh=true&conflicts=proceed',
            {'query':{'term':{'channel_id':channel_id}}},missing=True)
        check_deleted(removed)
    finally:
        backend.close()
