"""Bound the open upload request before multipart parsing can spool it."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from starlette.responses import JSONResponse

ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]

MULTIPART_OVERHEAD_BYTES = 64 * 1024


class UploadBodyLimit:
    """ASGI receive guard for the one public multipart route.

    The file store still enforces the exact file limit. This guard caps multipart
    metadata overhead and stops unknown-length streams before parser spooling.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], *, max_upload_bytes: int) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_upload_bytes + MULTIPART_OVERHEAD_BYTES

    async def __call__(self, scope: ASGIMessage, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/business/documents":
            await self.app(scope, receive, send)
            return

        lengths = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
        if len(lengths) > 1:
            await JSONResponse({"detail": "Ambiguous Content-Length"}, status_code=400)(scope, receive, send)
            return
        if lengths:
            try:
                declared = int(lengths[0])
            except ValueError:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
                return
            if declared < 0:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
                return
            if declared > self.max_body_bytes:
                await JSONResponse(
                    {"detail": "Upload request body exceeds configured limit"},
                    status_code=413,
                )(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> ASGIMessage:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise HTTPException(status_code=413, detail="Upload request body exceeds configured limit")
            return message

        await self.app(scope, limited_receive, send)


__all__ = ["UploadBodyLimit"]
