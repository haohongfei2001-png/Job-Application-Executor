"""In-memory loopback presentation contract for browser and consumer app hosts.

UI credentials never appear in repr, public launch results or safe diagnostics.
This supplies the native host boundary, not a claim of a shipped native window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, quote, urlsplit
import webbrowser


def loopback_origin(port: int) -> str:
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError("invalid consumer presentation") from None
    return f"http://127.0.0.1:{port}"


@dataclass(frozen=True, repr=False)
class ConsumerSurface:
    surface: str
    _url: str = field(repr=False)
    _origin: str = field(repr=False)
    _service_origin: str = field(repr=False)

    @classmethod
    def dashboard(cls, port: int, ticket: str) -> "ConsumerSurface":
        origin = loopback_origin(port)
        if (not isinstance(ticket, str) or not ticket or len(ticket) > 512
                or any(ord(char) < 32 or ord(char) == 127 for char in ticket)):
            raise ValueError("invalid consumer presentation") from None
        return cls.from_url("dashboard", origin + "/ui-login?ticket=" + quote(ticket, safe=""))

    @classmethod
    def from_url(cls, surface: str, url: str, *,
                 service_port: int | None = None) -> "ConsumerSurface":
        try:
            if surface not in {"dashboard", "bootstrap"} or not isinstance(url, str) or len(url) > 2048:
                raise ValueError
            if any(ord(char) < 32 or ord(char) == 127 for char in url):
                raise ValueError
            parsed = urlsplit(url)
            origin = loopback_origin(parsed.port)
            if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                    or parsed.netloc != origin.removeprefix("http://")
                    or parsed.username is not None or parsed.password is not None
                    or parsed.fragment):
                raise ValueError
            key = "ticket" if surface == "dashboard" else "token"
            path = "/ui-login" if surface == "dashboard" else "/"
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=1)
            credential = query.get(key, [""])[0]
            if (parsed.path != path or set(query) != {key} or len(query[key]) != 1
                    or not credential or len(credential) > 512
                    or any(ord(char) < 32 or ord(char) == 127 for char in credential)):
                raise ValueError
            service_origin = loopback_origin(service_port) if service_port is not None else origin
            if surface == "dashboard" and service_origin != origin:
                raise ValueError
            return cls(surface, url, origin, service_origin)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("invalid consumer presentation") from None

    @property
    def url(self) -> str:
        """Private app-host input. Never copy into a public receipt or report."""
        return self._url

    def allows_navigation(self, url: str) -> bool:
        """Permit only the initial local surface and its known service handoff."""
        try:
            if not isinstance(url, str) or any(ord(c) < 32 or ord(c) == 127 for c in url):
                return False
            parsed = urlsplit(url)
            if parsed.fragment or parsed.username is not None or parsed.password is not None:
                return False
            origin = loopback_origin(parsed.port)
            if parsed.scheme != "http" or parsed.netloc != origin.removeprefix("http://"):
                return False
            if origin == self._service_origin and parsed.path in {"/ui", "/ui-login"}:
                return True
            return (self.surface == "bootstrap" and origin == self._origin
                    and parsed.path in {"/", "/retry"})
        except (ValueError, TypeError, AttributeError):
            return False

    def safe_summary(self) -> dict:
        return {"surface": self.surface, "host_scope": "loopback_routes",
                "evidence_scope": "route_scope_only",
                "credential_in_report": False, "submit_capability": False}


def present_surface(surface: ConsumerSurface, *,
                    presenter: Callable[[ConsumerSurface], bool] | None = None) -> bool:
    """A failed app presentation never falls back to another browser window."""
    try:
        if presenter is None:
            return bool(webbrowser.open(surface.url, new=2))
        return presenter(surface) is True
    except Exception:
        # A host exception may contain the one-use ticket. It is not diagnostic text.
        return False
