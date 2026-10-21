"""The operator mirror verifies pinned model bytes before exposing them to browsers."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import pytest


def module():
    spec = importlib.util.spec_from_file_location('model_preparer', Path(__file__).parents[2] / 'scripts/prepare-whisper-model.py')
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result


def test_mirror_download_integrity_cache_and_tampered_cache_repair(tmp_path, monkeypatch):
    service = module(); bodies = {'config.json': b'{"model_type":"whisper"}', 'onnx/encoder_model_quantized.onnx': b'actual-fixture-model-bytes'}
    name = 'onnx/encoder_model_quantized.onnx'; checksum = hashlib.sha256(bodies[name]).hexdigest(); service.WEIGHTS = {name: checksum}
    siblings = []
    for filename, body in bodies.items():
        record = {'rfilename': filename, 'size': len(body), 'blobId': hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()}
        if filename == name: record['lfs'] = {'sha256': checksum}
        siblings.append(record)
    metadata = json.dumps({'sha': service.REVISION, 'siblings': siblings}).encode(); fetched = []
    def open_url(url, **_):
        fetched.append(url)
        if '/api/models/' in url: return io.BytesIO(metadata)
        return io.BytesIO(bodies[url.split(service.REVISION + '/')[1]])
    monkeypatch.setattr(service.urllib.request, 'urlopen', open_url)
    root = service.prepare(tmp_path); assert (root/name).read_bytes() == bodies[name]
    manifest = json.loads((root/'freecollab-model-manifest.json').read_text()); assert manifest['revision'] == service.REVISION and len(manifest['files']) == 2
    fetched.clear(); service.prepare(tmp_path); assert len(fetched) == 1
    (root/name).write_bytes(b'tampered'); fetched.clear(); service.prepare(tmp_path); assert (root/name).read_bytes() == bodies[name] and len(fetched) == 2
    (root/name).unlink(); bodies[name] = b'wrong download contents'
    with pytest.raises(ValueError, match='integrity'): service.prepare(tmp_path)
