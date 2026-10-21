#!/usr/bin/env python3
"""Download the pinned local ASR model for an operator-owned same-origin mirror.

Uses ordinary verified HTTPS and inherited proxy/CA configuration. Every file is
checked against its immutable Git blob ID or Git LFS SHA-256 from the pinned
official revision. ONNX weight digests are additionally fixed in this release.
No audio, customer credentials or model execution is involved in this utility.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request

MODEL = 'onnx-community/whisper-tiny'
REVISION = 'ff4177021cc41f7db950912b73ea4fdf7d01d8e7'
WEIGHTS = {
    'onnx/encoder_model_quantized.onnx': '2af4a414ca47aa30f61246017e5fe82b0a8d229281d1255ba666a2a7f6b84d19',
    'onnx/decoder_model_merged_quantized.onnx': '25e807a962b6349356d0ea5d0dfe530b7e5bf0e2a484aeca0359d03143faddd3',
}


def prepare(directory):
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{MODEL}/revision/{REVISION}?blobs=true', timeout=30) as response:
        metadata = json.load(response)
    if metadata.get('sha') != REVISION: raise ValueError('Unexpected model revision')
    selected = [row for row in metadata['siblings'] if '/' not in row['rfilename'] and (row['rfilename'].endswith('.json') or row['rfilename'] == 'README.md') or row['rfilename'] in WEIGHTS]
    if set(WEIGHTS) - {row['rfilename'] for row in selected}: raise ValueError('Pinned model weights are unavailable')
    root = Path(directory).resolve() / MODEL / 'resolve' / REVISION
    root.mkdir(parents=True, exist_ok=True)
    def fetch(row):
        name = row['rfilename']; path = root/name
        if '..' in Path(name).parts or Path(name).is_absolute(): raise ValueError('Invalid model artifact name')
        size = row['size']; expected = row.get('lfs', {}).get('sha256')
        if name in WEIGHTS and expected != WEIGHTS[name]: raise ValueError('Unexpected pinned model weight digest')
        body = path.read_bytes() if path.is_file() else None
        def valid(data):
            if data is None or len(data) != size: return False
            return hashlib.sha256(data).hexdigest() == expected if expected else hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == row['blobId']
        if not valid(body):
            with urllib.request.urlopen(f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}', timeout=90) as response:
                body = response.read(size + 1)
            if not valid(body): raise ValueError('Model artifact integrity check failed: ' + name)
            path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(path.name + '.part'); temporary.write_bytes(body); temporary.replace(path)
        return {'path': name, 'size': size, 'sha256': hashlib.sha256(body).hexdigest()}
    with ThreadPoolExecutor(max_workers=4) as executor: manifest = list(executor.map(fetch, selected))
    (root/'freecollab-model-manifest.json').write_text(json.dumps({'model': MODEL, 'revision': REVISION, 'files': manifest}, indent=2) + '\n')
    print(f'Prepared {len(manifest)} verified model artifacts for {MODEL} at {REVISION}')
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--directory', required=True, help='Mirror root, for example frontend/public/whisper-models')
    try: prepare(parser.parse_args().directory)
    except Exception as error: raise SystemExit('Model preparation failed (' + type(error).__name__ + '); keep TLS/proxy verification enabled and retry') from None
