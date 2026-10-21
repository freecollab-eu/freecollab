"""Verify production Nginx serves and executes the built crypto/PDF assets.

Run against a disposable frontend Docker container, never the Vite dev server:
STATIC_BASE_URL=http://127.0.0.1:18081 CHROMIUM_PATH=/usr/bin/chromium \\
  backend/.venv/bin/python frontend/tests/production-assets-browser.py

No user accounts, message keys or external Matrix server are used. The real built
Rust crypto module initializes an in-memory device; the PDF worker renders a page.
"""
import json
import os
import re
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

BASE = os.getenv('STATIC_BASE_URL', 'http://127.0.0.1:18081').rstrip('/')
with urlopen(BASE + '/', timeout=30) as response:
    html = response.read().decode()
entrypoints = re.findall(r'<script[^>]+src="([^"]+\.js)"', html)
assert entrypoints, 'No production module entrypoint found'
queue = [urljoin(BASE + '/', path) for path in entrypoints]
sources = {}
assets = set(queue)
while queue:
    url = queue.pop()
    if url in sources:
        continue
    assert urlparse(url).netloc == urlparse(BASE).netloc, 'Production asset references an external origin'
    with urlopen(url, timeout=30) as response:
        assert response.headers.get_content_type() in {'application/javascript', 'text/javascript'}, (url, response.headers.get('Content-Type'))
        source = response.read().decode()
    sources[url] = source
    for path in re.findall(r'''["']((?:\./|/assets/|assets/)[^"'\s]+-[A-Za-z0-9_-]{6,}\.(?:js|mjs|wasm))["']''', source):
        target = urljoin(BASE + '/' if path.startswith('assets/') else url, path)
        assets.add(target)
        if target.endswith('.js') and target not in sources:
            queue.append(target)
wasm = next((url for url in assets if 'matrix_sdk_crypto_wasm_bg-' in url and url.endswith('.wasm')), None)
worker = next((url for url in assets if 'pdf.worker.min-' in url and url.endswith('.mjs')), None)
rust = next((url for url, source in sources.items() if 'as initRustCrypto}' in source), None)
pdf = next((url for url in sources if '/pdf-' in url), None)
assert wasm and worker and rust and pdf, {'wasm': wasm, 'worker': worker, 'rust': rust, 'pdf': pdf}
for url, expected in [(wasm, {'application/wasm'}), (worker, {'application/javascript', 'text/javascript'})]:
    with urlopen(url, timeout=30) as response:
        assert response.headers.get_content_type() in expected, (url, response.headers.get('Content-Type'))
        assert response.headers.get('X-Content-Type-Options') == 'nosniff'
print('PASS: built WASM and local PDF module worker have strict, correct production MIME types', flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None, headless=True, args=['--no-sandbox'])
    try:
        page = browser.new_page()
        page.set_default_timeout(60000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until='domcontentloaded')
        result = page.evaluate('''async ({wasm,rust,pdf,worker}) => {
          const compiled = await WebAssembly.compileStreaming(fetch(wasm));
          const exports = WebAssembly.Module.exports(compiled).length;
          if (exports < 100) throw new Error('Unexpected crypto WASM module');
          const module = await import(rust);
          const logger = {trace(){},debug(){},info(){},warn(){},error(){},getChild(){return this}};
          const http = {opts:{baseUrl:location.origin}, authedRequest:async()=>{ throw Object.assign(new Error('No test backup configured'),{errcode:'M_NOT_FOUND',httpStatus:404}) }};
          const crypto = await module.initRustCrypto({logger,http,userId:'@asset-proof:local.test',deviceId:'ASSET-PROOF',secretStorage:{isStored:async()=>null},cryptoCallbacks:{}});
          let keys;
          try { keys = await crypto.getOwnDeviceKeys(); if (!keys.ed25519 || !keys.curve25519) throw new Error('Rust device keys were not generated'); }
          finally { crypto.stop(); }
          await new Promise((resolve,reject) => {
            const thread = new Worker(worker,{type:'module'});
            const timer = setTimeout(()=>{thread.terminate();reject(new Error('PDF worker did not initialize'))},15000);
            thread.onmessage = event => { if (event.data.action === 'ready') {clearTimeout(timer);thread.terminate();resolve()} };
            thread.onerror = error => {clearTimeout(timer);thread.terminate();reject(new Error(error.message || 'PDF worker failed to load'))};
          });
          const pdfjs = await import(pdf); pdfjs.GlobalWorkerOptions.workerSrc = worker;
          const objects = ['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>','<< /Length 23 >>\\nstream\\n0 0 1 rg 10 10 50 50 re f\\nendstream'];
          let body = '%PDF-1.4\\n'; const offsets=[0];
          for (let i=0;i<objects.length;i++){offsets.push(body.length);body+=`${i+1} 0 obj\\n${objects[i]}\\nendobj\\n`}
          const xref=body.length;body+=`xref\\n0 ${offsets.length}\\n0000000000 65535 f \\n`;
          body+=offsets.slice(1).map(offset=>String(offset).padStart(10,'0')+' 00000 n \\n').join('');
          body+=`trailer\\n<< /Size ${offsets.length} /Root 1 0 R >>\\nstartxref\\n${xref}\\n%%EOF`;
          const task=pdfjs.getDocument({data:new TextEncoder().encode(body),useSystemFonts:true,stopAtErrors:true});
          let pages;
          try { const document=await task.promise;pages=document.numPages;const page=await document.getPage(1);const canvas=window.document.createElement('canvas');const viewport=page.getViewport({scale:1});canvas.width=viewport.width;canvas.height=viewport.height;await page.render({canvas,viewport}).promise;const pixel=canvas.getContext('2d').getImageData(20,60,1,1).data;if(pixel[2]!==255 || pixel[0]!==0) throw new Error('PDF page did not render its blue rectangle'); }
          finally { await task.destroy(); }
          return {wasmExports:exports,deviceKeysGenerated:true,pdfWorkerReady:true,pdfPagesRendered:pages};
        }''', {'wasm': wasm, 'rust': rust, 'pdf': pdf, 'worker': worker})
        assert not errors, errors
        print('PASS: production Chromium streaming-compiles Matrix WASM, initializes Rust crypto device, boots the local PDF module worker and renders a PDF page', flush=True)
        print(json.dumps(result, sort_keys=True))
    finally:
        browser.close()
