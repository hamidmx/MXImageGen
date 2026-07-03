#!/usr/bin/env python3
"""
Tiny local CORS proxy for the how-to-draw web app.

Ideogram's API does not send the Access-Control-Allow-Origin header, so a
browser fetch() call from a static index.html is blocked by CORS and fails
with "Failed to fetch". This proxy runs on localhost, forwards any URL you
hand it via ?url=..., and adds the CORS headers the browser needs.

Usage:
    python3 cors-proxy.py            # listens on http://localhost:8787
    python3 cors-proxy.py --port 8080

Only the Ideogram model needs this — Gemini and OpenAI work directly.
Leave this script running in a terminal while you use Ideogram in the app.
Stop it with Ctrl+C when done. Uses only the Python standard library, so
no pip install is needed.
"""
import argparse
import gzip
import http.server
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib

# Headers we refuse to forward to the upstream. Everything else (including
# Api-Key, Authorization, Content-Type with its multipart boundary) is passed
# through so the upstream sees the request as if it came from a normal
# server-side client.
#
# `accept-encoding` is explicitly dropped so the upstream sends us plain
# uncompressed bytes. Otherwise Ideogram happily gzips its JSON responses
# and the browser (which only sees Content-Type: application/json from our
# response headers, not Content-Encoding) chokes on JSON.parse with an
# "Unexpected token" error at byte 0.
DROP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "origin",
    "referer",
    "connection",
    "cookie",
    "accept-encoding",
}

# Headers we forward from the upstream response to the browser. Content-Type
# tells the browser how to interpret the body; anything else is skipped so we
# don't leak upstream Set-Cookie / hop-by-hop headers.
KEEP_RESPONSE_HEADERS = {"content-type"}

# Max upstream response body size (100 MB is well above anything Ideogram
# will ever return; keeps the proxy from being weaponized as a giant relay).
MAX_BODY_BYTES = 100 * 1024 * 1024


class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        sys.stderr.write(
            "[%s] %s %s\n"
            % (self.log_date_time_string(), self.command, fmt % args)
        )

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, DELETE, PATCH, OPTIONS",
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Api-Key, Authorization, Content-Type, X-Api-Key",
        )
        self.send_header("Access-Control-Expose-Headers", "Retry-After")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_error(self, status, message):
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = ('{"proxy_error":' + repr(message) + "}").encode()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # CORS preflight — reply immediately with the allowed methods/headers.
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _extract_target_url(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/proxy":
            return None
        query = urllib.parse.parse_qs(parsed.query)
        if "url" not in query:
            return None
        target = query["url"][0]
        # Only allow http(s) upstreams — refuse file://, ftp://, etc.
        if not (target.startswith("http://") or target.startswith("https://")):
            return None
        return target

    def _forward(self):
        target = self._extract_target_url()
        if not target:
            self._send_error(
                400,
                "Path must be /proxy?url=<http(s)-url>",
            )
            return

        content_length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(content_length) if content_length else None

        req = urllib.request.Request(target, data=body, method=self.command)
        for header_name in self.headers:
            if header_name.lower() in DROP_REQUEST_HEADERS:
                continue
            req.add_header(header_name, self.headers[header_name])
        # Explicitly tell the upstream we want uncompressed bytes.
        req.add_header("Accept-Encoding", "identity")

        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status = resp.status
                upstream_headers = list(resp.getheaders())
                data = resp.read(MAX_BODY_BYTES)
        except urllib.error.HTTPError as e:
            # Upstream returned a non-2xx status — propagate the response.
            status = e.code
            upstream_headers = list(e.headers.items()) if e.headers else []
            try:
                data = e.read(MAX_BODY_BYTES) or b""
            except Exception:
                data = b""
        except Exception as e:
            self._send_error(502, "upstream fetch failed: %s" % e)
            return

        # Belt-and-suspenders decompression: if the upstream ignored our
        # Accept-Encoding: identity and sent compressed bytes anyway, decode
        # them here so we forward plain text/bytes to the browser (which
        # doesn't see the Content-Encoding header — we strip it below).
        content_encoding = ""
        for k, v in upstream_headers:
            if k.lower() == "content-encoding":
                content_encoding = v.lower().strip()
                break
        try:
            if content_encoding in ("gzip", "x-gzip") or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            elif content_encoding == "deflate":
                # Try raw deflate first, then zlib-wrapped as a fallback.
                try:
                    data = zlib.decompress(data, -zlib.MAX_WBITS)
                except zlib.error:
                    data = zlib.decompress(data)
        except Exception as e:
            sys.stderr.write("decompression failed (forwarding raw): %s\n" % e)

        self.send_response(status)
        self._send_cors_headers()
        content_type_sent = False
        for k, v in upstream_headers:
            if k.lower() in KEEP_RESPONSE_HEADERS:
                self.send_header(k, v)
                if k.lower() == "content-type":
                    content_type_sent = True
        if not content_type_sent:
            self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # Route every method (except OPTIONS handled above) through _forward.
    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787, help="listen port (default 8787)")
    args = parser.parse_args()

    with ThreadingServer(("localhost", args.port), ProxyHandler) as httpd:
        print("CORS proxy listening on http://localhost:%d" % args.port)
        print("Route:  http://localhost:%d/proxy?url=<url-encoded-target>" % args.port)
        print("Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nshutting down.")


if __name__ == "__main__":
    main()
