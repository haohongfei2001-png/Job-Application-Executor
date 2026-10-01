from __future__ import annotations

import socketserver
from http.server import ThreadingHTTPServer


class LoopbackHTTPServer(ThreadingHTTPServer):
    """Bind the local service without DNS or hostname resolution."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]
