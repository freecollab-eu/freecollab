"""Request limits enforced while streaming, before multipart parsing can grow unbounded."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, json_limit=1024 * 1024, upload_limit=52 * 1024 * 1024):
        self.app = app
        self.json_limit = json_limit
        self.upload_limit = upload_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        is_upload = scope.get("method") in {"POST", "PUT"} and (
            path.startswith("/files/") or path.startswith("/office-edit/") or path.startswith("/_matrix/media/") or
            path.startswith("/_matrix/client/v1/media/") or path.startswith("/workspaces/") and path.endswith("/logo")
        )
        limit = self.upload_limit if is_upload else self.json_limit
        lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError
                length = int(lengths[0])
            except ValueError:
                return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
            if length > limit:
                return await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            event = await receive()
            if event["type"] == "http.request":
                consumed += len(event.get("body", b""))
                if consumed > limit:
                    raise HTTPException(status_code=413, detail="Request body too large")
            return event

        await self.app(scope, limited_receive, send)


class PrivateResponseCacheMiddleware:
    """Keep API data and credentials out of HTTP caches, including downloads."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                from starlette.datastructures import MutableHeaders
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private, no-store"
                headers["Pragma"] = "no-cache"
            await send(message)

        await self.app(scope, receive, private_send)
