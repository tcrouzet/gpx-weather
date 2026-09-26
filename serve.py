#!/usr/bin/env python3
"""Serveur local du site généré, avec routage des URL de prévision."""

import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent / "_output"
FORECAST_PATH = re.compile(
    r"^/(?P<slug>[^/]+)/(?:forecast|forecast_details)"
    r"(?:/\d{8}-\d{1,2}-\d{1,2})?/?$"
)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        match = FORECAST_PATH.fullmatch(path)
        if match:
            page = ROOT / match.group("slug") / "index.html"
            if page.is_file():
                content = page.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
        super().do_GET()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if not ROOT.is_dir():
        raise SystemExit("_output/ absent : lance d'abord app.py")
    server = ThreadingHTTPServer(("", port), Handler)
    print(f"GPX Weather : http://localhost:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
