"""WSGI adapter. Vercel's Python runtime (`vercel_runtime` 0.16.0) requires an
ASGI or WSGI callable — it does NOT support the `BaseHTTPRequestHandler`
pattern the official docs showed as of 2026-07-06 (confirmed via a live
`vercel dev` failure: "Could not determine the application interface...
Expected either an ASGI app... or a WSGI app". See DECISIONS.md).

This converts a WSGI `environ` into the plain dict/bytes every app/web/*.py
handler already expects, and a WebResponse back into a WSGI response — so
none of that handler logic (or its tests) needed to change, only this
one adapter file.
"""
from __future__ import annotations

from urllib.parse import parse_qsl

from app.web.types import WebResponse

_STATUS_TEXT = {
    200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error",
}


def read_body(environ: dict) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    return environ["wsgi.input"].read(length) if length else b""


def read_headers(environ: dict) -> dict:
    headers = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            headers[key[5:].replace("_", "-").title()] = value
    if "CONTENT_TYPE" in environ:
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    if "CONTENT_LENGTH" in environ:
        headers["Content-Length"] = environ["CONTENT_LENGTH"]
    return headers


def read_query(environ: dict) -> dict:
    return dict(parse_qsl(environ.get("QUERY_STRING", "")))


def wsgi_response(response: WebResponse, start_response) -> list[bytes]:
    status_line = f"{response.status} {_STATUS_TEXT.get(response.status, '')}".strip()
    headers = [("Content-Type", response.content_type), *response.headers.items()]
    start_response(status_line, headers)
    return [response.body]
