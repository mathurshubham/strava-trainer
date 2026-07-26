"""Transport-agnostic request/response shapes. The api/*.py Vercel entrypoints
translate BaseHTTPRequestHandler <-> these — every handler in app/web/ is a
plain function you can unit test without an HTTP server."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WebResponse:
    status: int
    body: bytes
    content_type: str = "application/json"
    headers: dict = field(default_factory=dict)

    @classmethod
    def json(cls, status: int, data: dict) -> "WebResponse":
        import json

        return cls(status=status, body=json.dumps(data).encode(), content_type="application/json")
