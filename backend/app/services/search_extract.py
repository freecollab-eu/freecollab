"""Passive bounded text extraction in a disposable, resource-limited process.

No Office macros, PDF actions, embedded files, external relationships or URLs
execute. The parent enforces input, wall-time and output bounds as well.
"""
import base64
import io
import json
import sys


def extract(data):
    kind = data["kind"]
    if kind == "yjs":
        import y_py
        doc = y_py.YDoc()
        for encoded in data["updates"]:
            y_py.apply_update(doc, base64.b64decode(encoded, validate=True))
        title, body = str(doc.get_text("title")), str(doc.get_text("body"))
        if len(title) > 200 or len(body) > 50000:
            raise ValueError("Unsupported document size")
        return {"title": title, "text": body, "truncated": False}
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(base64.b64decode(data["content"], validate=True)), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF")
    texts, size = [], 0
    for page in reader.pages[:40]:
        text = page.extract_text() or ""
        texts.append(text[:100000 - size]); size += len(texts[-1])
        if size >= 100000:
            break
    return {"text": "\n".join(texts)[:100000], "truncated": len(reader.pages) > len(texts) or size >= 100000}


if __name__ == "__main__":
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    try:
        raw = sys.stdin.buffer.read(6 * 1024 * 1024 + 1)
        if len(raw) > 6 * 1024 * 1024:
            raise ValueError("Input limit")
        print(json.dumps(extract(json.loads(raw)), ensure_ascii=False))
    except BaseException:
        sys.exit(2)
