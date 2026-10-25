"""Purpose-encrypted, incremental native latent-semantic search.

LSA projects TF/IDF documents and queries into a deterministic low-rank corpus
space. It is not a substring matcher or an external model request. Encrypted
channels never enter this server index; their device owns any local index.
"""
from collections import Counter
from datetime import datetime
import json
import math
import re
import time

from fastapi import HTTPException
from sqlmodel import select

from ..models.content_completion import ChannelSemanticIndex
from ..models.workspace import WorkspaceChannel
from . import search_sources, shared_documents
from .workspaces import require_workspace_member

MAX_ITEMS = 1000
MAX_CHARACTERS = 2000000
MAX_TERMS = 4096
MAX_RANK = 12
STOP = set('the and for with from this that your you are was were have has will can not our och att det den som med för från till ett en är har på av vi ni de'.split())


def words(value):
    return [word for word in re.findall(r'[^\W_]{2,}', value.casefold(), re.UNICODE) if word not in STOP][:20000]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _unit(values):
    length = math.sqrt(_dot(values, values))
    return [v / length for v in values] if length > 1e-12 else [0.0] * len(values)


def latent_model(items):
    counts = [Counter(words(item['title'] + '\n' + item['text'])) for item in items]
    df = Counter(term for row in counts for term in row)
    vocabulary = sorted(df, key=lambda term: (-df[term], term))[:MAX_TERMS]
    idf = {term: math.log((len(items) + 1) / (df[term] + 1)) + 1 for term in vocabulary}
    vectors = []
    for row in counts:
        vector = {term: (1 + math.log(count)) * idf[term] for term, count in row.items() if term in idf}
        length = math.sqrt(sum(value * value for value in vector.values())) or 1
        vectors.append({term: value / length for term, value in vector.items()})
    n = len(items)
    def multiply(values):
        # X'X multiplication stays sparse; no quadratic dense document matrix.
        terms = Counter()
        for row, weight in zip(vectors, values):
            for term, value in row.items():
                terms[term] += value * weight
        return [sum(value * terms[term] for term, value in row.items()) for row in vectors]
    axes, eigenvalues = [], []
    # Truncation is essential: a full-rank projection would reduce to lexical
    # cosine. This corpus-derived space admits indirect co-occurrence matches.
    rank = min(MAX_RANK, max(1, n // 2))
    for k in range(rank):
        vector = _unit([math.sin((i + 1) * (k + 1) * 1.618) + .1 for i in range(n)])
        for _ in range(24):
            projected = multiply(vector)
            for axis in axes:
                amount = _dot(projected, axis)
                projected = [value - amount * coordinate for value, coordinate in zip(projected, axis)]
            vector = _unit(projected)
        eigenvalue = _dot(vector, multiply(vector))
        if eigenvalue < 1e-8:
            break
        axes.append(vector); eigenvalues.append(eigenvalue)
    return {'idf': idf, 'vectors': vectors, 'axes': axes, 'eigenvalues': eigenvalues}


def rank_items(items, model, query):
    counts = Counter(words(query))
    vector = {term: (1 + math.log(count)) * model['idf'][term] for term, count in counts.items() if term in model['idf']}
    length = math.sqrt(sum(value * value for value in vector.values())) or 1
    vector = {term: value / length for term, value in vector.items()}
    affinities = [sum(value * row.get(term, 0) for term, value in vector.items()) for row in model['vectors']]
    projected = [_dot(axis, affinities) / math.sqrt(value) for axis, value in zip(model['axes'], model['eigenvalues'])]
    query_space = _unit(projected)
    result = []
    for i, item in enumerate(items):
        document_space = _unit([axis[i] * math.sqrt(value) for axis, value in zip(model['axes'], model['eigenvalues'])])
        semantic = max(0, _dot(query_space, document_space))
        score = .8 * semantic + .2 * affinities[i]
        if score > .05:
            result.append({**item, 'score': round(score, 6)})
    return sorted(result, key=lambda row: (-row['score'], row['kind'], row['id']))


def _load(session, channel_id):
    row = session.get(ChannelSemanticIndex, channel_id, populate_existing=True)
    if not row:
        return None, {'items': [], 'model': latent_model([]), 'complete': True}
    payload = shared_documents.open_payload(session, channel_id, 'semantic-index', channel_id, 'current', row.encrypted_payload)
    payload['index_revision'] = row.revision
    if payload.get('engine') != 'local-lsa-v1':
        raise HTTPException(409, 'The semantic index needs rebuilding')
    return row, payload


def synchronize(session, channel_id, actor_id):
    search_sources.authorize(session, channel_id, actor_id)
    row, previous = _load(session, channel_id)
    cached = {(item['kind'], item['id']): item for item in previous['items']}
    items, characters, complete, changed = [], 0, True, False
    deadline = time.monotonic() + 12
    for kind, model in search_sources.KINDS.items():
        records = session.exec(select(model).where(model.channel_id == channel_id)
            .order_by(model.created_at.desc(), model.id.desc()).limit(MAX_ITEMS + 1)).all()
        if len(records) > MAX_ITEMS:
            complete = False
        for record in records[:MAX_ITEMS]:
            old = cached.get((kind, record.id))
            try:
                # Avoid parsing unchanged files/Yjs histories on every query.
                if old and search_sources.row_revision(record, session) == old.get('source_revision') and search_sources.still_current(session, old):
                    item = old
                else:
                    if time.monotonic() > deadline:
                        complete = False
                        continue
                    item = search_sources.source(session, channel_id, kind, record)
                    changed = True
                if not search_sources.allowed(session, channel_id, item):
                    continue
            except HTTPException as error:
                if error.status_code not in (404, 409, 413, 422):
                    raise
                complete &= error.status_code == 404
                continue
            characters += len(item['title']) + len(item['text'])
            if len(items) >= MAX_ITEMS or characters > MAX_CHARACTERS:
                complete = False; break
            items.append({key: value for key, value in item.items() if key not in ('reference', 'score')})
        if len(items) >= MAX_ITEMS or characters > MAX_CHARACTERS:
            break
    changed |= [(i['kind'], i['id'], i['version']) for i in items] != [(i['kind'], i['id'], i['version']) for i in previous['items']]
    if changed or row is None or complete != previous.get('complete'):
        model = latent_model(items)
        # Serialized with native writes and encrypted-channel conversion. There
        # is no plaintext cache write after revocation or a conversion fence.
        from .access import lock_channel_writes
        lock_channel_writes(session, channel_id); session.expire_all()
        search_sources.authorize(session, channel_id, actor_id)
        fresh = [item for item in items if search_sources.still_current(session, item) and search_sources.allowed(session, channel_id, item)]
        if len(fresh) != len(items):
            items, model, complete = fresh, latent_model(fresh), False
        current = session.get(ChannelSemanticIndex, channel_id)
        payload = {'engine': 'local-lsa-v1', 'items': items, 'model': model, 'complete': complete}
        row = current or ChannelSemanticIndex(channel_id=channel_id, encrypted_payload='')
        row.revision = (current.revision + 1) if current else 1
        row.updated_at = datetime.utcnow()
        payload['index_revision'] = row.revision
        row.encrypted_payload = shared_documents.seal(channel_id, 'semantic-index', channel_id, 'current', payload)
        session.add(row); session.commit(); session.expire_all()
        search_sources.authorize(session, channel_id, actor_id)
        return row, payload
    return row, previous


def retrieve(session, workspace_id, channel_id, actor_id, query, limit=10):
    require_workspace_member(session, workspace_id, actor_id)
    binding = session.get(WorkspaceChannel, channel_id)
    if not binding or binding.workspace_id != workspace_id:
        raise HTTPException(404, 'Search channel is outside this workspace')
    if not isinstance(query, str) or not query.strip() or len(query) > 1000 or not 1 <= limit <= 25:
        raise HTTPException(422, 'Use a query up to 1000 characters and at most 25 results')
    row, payload = synchronize(session, channel_id, actor_id)
    from . import semantic_opensearch
    if semantic_opensearch.configured():
        return semantic_opensearch.retrieve(session, channel_id, actor_id, payload, payload['index_revision'], query, limit)
    results = rank_items(payload['items'], payload['model'], query)
    session.commit(); session.expire_all()
    search_sources.authorize(session, channel_id, actor_id)
    fresh = []
    for item in results:
        if search_sources.still_current(session, item) and search_sources.allowed(session, channel_id, item):
            fresh.append({**item, 'reference': search_sources.reference(actor_id, item)})
        if len(fresh) >= limit:
            break
    return {'items': fresh, 'complete': payload['complete'], 'engine': 'local-lsa-v1', 'index_revision': payload['index_revision']}
