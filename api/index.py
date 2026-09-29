"""Vercel ASGI adapter for the DEEP33 FastAPI backend.

Vercel routes /api/* to this function. DEEP33 keeps its canonical API paths
at /health, /v1/*, and /metrics, so this adapter strips the /api prefix
before handing the request to the existing FastAPI application.
"""
from __future__ import annotations

from backend.main import app as backend_app


class StripApiPrefix:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if path == "/api":
            new_path = "/"
        elif path.startswith("/api/"):
            new_path = path[4:] or "/"
        else:
            new_path = path

        forwarded = dict(scope)
        forwarded["path"] = new_path
        forwarded["raw_path"] = new_path.encode("utf-8")
        await self.app(forwarded, receive, send)


app = StripApiPrefix(backend_app)
