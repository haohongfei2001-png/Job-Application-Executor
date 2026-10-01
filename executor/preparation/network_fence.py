"""Deny-only proxy for a newly launched disposable preparation browser.

Never use with an existing/user browser. This listener has no forwarding method,
no destination parsing and no request/body logging. It is defense in depth for
ordinary browser HTTP(S)/WebSocket traffic, not an OS-wide network sandbox.
"""
from __future__ import annotations

import socketserver
import threading


class _Deny(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.denied_connections += 1
        # Do not read a URL, headers or a potentially protected request body.
        try:
            self.request.settimeout(1)
            self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
        except OSError:
            pass


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False


class DenyOnlyProxy:
    """Own one ephemeral loopback listener; no release/allow/forward capability."""
    def __init__(self):
        self._server = None
        self._thread = None

    def __enter__(self):
        if self._server is not None:
            raise RuntimeError('preparation_proxy_already_started')
        self._server = _Server(('127.0.0.1', 0), _Deny)
        self._server.denied_connections = 0
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def denied_connections(self):
        return self._server.denied_connections if self._server else None

    def browser_options(self):
        if self._server is None or not self._thread.is_alive():
            raise RuntimeError('preparation_proxy_unavailable')
        return {'proxy': {'server': f'http://127.0.0.1:{self._server.server_address[1]}'},
                'args': ['--proxy-bypass-list=<-loopback>', '--disable-quic',
                         '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                         '--webrtc-ip-handling-policy=disable_non_proxied_udp',
                         '--force-webrtc-ip-handling-policy=disable_non_proxied_udp']}

    def __exit__(self, *_):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=2)
            self._server = self._thread = None
