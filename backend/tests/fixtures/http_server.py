"""A local HTTP server for adapter tests: static files (with HEAD and Range support) plus callable routes."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

# A route returns (status, content_type, body).
Route = Callable[[str, dict, bytes], tuple[int, str, bytes]]


class FixtureServer:
    def __init__(self, root: Path):
        self.root = root
        self.routes: dict[str, Route] = {}
        self.requests: list[tuple[str, str]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _respond(self, head_only: bool):
                parts = urlsplit(self.path)
                server.requests.append((self.command, self.path))
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                route = server.routes.get(parts.path)
                if route:
                    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                    if self.command == "POST" and body:
                        query.update({k: v[0] for k, v in parse_qs(body.decode()).items()})
                    status, content_type, payload = route(self.command, query, body)
                    return self._send(status, content_type, payload, head_only)
                path = (server.root / parts.path.lstrip("/")).resolve()
                if server.root not in path.parents or not path.is_file():
                    return self._send(404, "text/plain", b"not found", head_only)
                data = path.read_bytes()
                ranged = self.headers.get("Range")
                if ranged and ranged.startswith("bytes="):
                    start, _, end = ranged[6:].partition("-")
                    start = int(start)
                    end = min(int(end) if end else len(data) - 1, len(data) - 1)
                    chunk = data[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(chunk)
                    return None
                return self._send(200, "application/octet-stream", data, head_only,
                                  extra={"ETag": f'"{len(data)}-{hash(data) & 0xffff:x}"',
                                         "Last-Modified": "Fri, 09 Oct 2026 02:41:56 GMT"})

            def _send(self, status, content_type, payload, head_only, extra=None):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if not head_only:
                    self.wfile.write(payload)

            def do_GET(self):
                self._respond(False)

            def do_POST(self):
                self._respond(False)

            def do_HEAD(self):
                self._respond(True)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
