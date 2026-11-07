#!/usr/bin/env python3
"""Local static server for Bot Desk."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import json
import os
import sys

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "43147"))
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/nellis", "/nellis/"):
            self._send_nellis_page(parsed)
            return
        if parsed.path == "/nellis.json":
            self._send_nellis_json(parsed)
            return
        if parsed.path == "/nellis/watchlist":
            self._send_watchlist_page()
            return
        if parsed.path == "/nellis/watchlist.json":
            self._send_watchlist_json()
            return
        if parsed.path == "/nellis/google":
            self._send_google_prompt()
            return
        super().do_GET()

    def _nellis_payload(self, parsed):
        from nellis.connector import NellisError, fetch_public_lots

        raw_limit = parse_qs(parsed.query).get("limit", [None])[0]
        try:
            if raw_limit is None:
                return fetch_public_lots()
            return fetch_public_lots(raw_limit)
        except NellisError as exc:
            return {"ok": False, "error": str(exc)}

    def _send_nellis_page(self, parsed):
        from nellis.connector import render_lots_page

        payload = self._nellis_payload(parsed)
        body = render_lots_page(payload).encode("utf-8")
        status = 200 if payload.get("ok") else 502
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_nellis_json(self, parsed):
        payload = self._nellis_payload(parsed)
        body = json.dumps(payload, indent=2).encode("utf-8")
        status = 200 if payload.get("ok") else 502
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_watchlist_page(self):
        from nellis.google_sign_in import render_watchlist_page

        body = render_watchlist_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_watchlist_json(self):
        from nellis.google_sign_in import watchlist_status_json

        body = watchlist_status_json().encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_google_prompt(self):
        from nellis.connector import NellisError
        from nellis.google_sign_in import fresh_google_prompt_url

        try:
            target = fresh_google_prompt_url()
        except NellisError as exc:
            body = str(exc).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args):
        print("[%s] %s" % (self.log_date_time_string(), format % args))


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Bot Desk serving http://127.0.0.1:{PORT}")
    httpd.serve_forever()
