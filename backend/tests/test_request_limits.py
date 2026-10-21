from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware import RequestBodyLimitMiddleware


def small_app():
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, json_limit=16, upload_limit=32)

    @app.post("/{path:path}")
    async def echo(request: Request):
        body = await request.body()
        return {"length": len(body)}

    return app


def test_declared_oversize_is_rejected_before_parser():
    with TestClient(small_app()) as client:
        assert client.post("/messages", content=b"x" * 17).status_code == 413
        assert client.post("/files/channels/example", content=b"x" * 33).status_code == 413


def test_chunked_body_without_length_is_bounded():
    def chunks():
        yield b"x" * 10
        yield b"x" * 10
    with TestClient(small_app()) as client:
        response = client.post("/messages", content=chunks())
        assert response.status_code == 413


def test_file_body_has_separate_bounded_allowance():
    with TestClient(small_app()) as client:
        response = client.post("/files/channels/example", content=b"x" * 20)
        assert response.json() == {"length": 20}


def test_invalid_content_length_is_rejected():
    with TestClient(small_app()) as client:
        assert client.post("/messages", content=b"test", headers={"Content-Length": "invalid"}).status_code == 400


def test_private_stream_download_cannot_override_the_api_cache_boundary():
    from starlette.responses import StreamingResponse
    from app.middleware import PrivateResponseCacheMiddleware
    app = FastAPI()
    app.add_middleware(PrivateResponseCacheMiddleware)

    @app.get("/download")
    def download():
        return StreamingResponse(iter([b"private", b" document"]), headers={"Cache-Control": "public, max-age=3600"})

    with TestClient(app) as client:
        response = client.get("/download")
        assert response.content == b"private document"
        assert response.headers["Cache-Control"] == "private, no-store"
