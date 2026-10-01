"""Fail-closed request gate for a dedicated preparation-only browser context.

This is an HTTP/WebSocket guard, not an OS network sandbox. It never authorizes
submission or reconnects a filled document. A production session additionally
needs independently verified process-lifetime and human-transition boundaries.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from .qiyunfang import CONTRACT_URL
from .resources import OBSERVED_STATIC_RESOURCES

PUBLIC_RESOURCES = OBSERVED_STATIC_RESOURCES | frozenset({
    CONTRACT_URL,
    "https://1-ss-sys.huaweicloudsite.cn/js/dist/site.min.js?v=202506121459",
    "https://1-ss-sys.huaweicloudsite.cn/js/dist/module.min.js?v=202506121459",
    "https://1-ss-sys.huaweicloudsite.cn/js/dist/frontend.min.js?v=202506121459",
    "https://1-ss-sys.huaweicloudsite.cn/js/dist/partitionSite.min.js?v=202511071120",
    "https://jzfe-sys.huaweicloudsite.cn/dist/jz/request/jzRequest.min.js?v=202506121719",
    "https://jzfe-sys.huaweicloudsite.cn/dist/jz/biz-shared/bizShared.min.js?v=202608251625",
    "https://jzfe-sys.huaweicloudsite.cn/dist/jz/utils/jzUtils.min.js?v=202506121754",
    "https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/comMethods.min.js?v=202407171154",
    "https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/jzUtils.min.js?v=202506121459",
    "https://1-ss-sys.huaweicloudsite.cn/js/comm/vue/vue-2.7.14.min.js?v=202310161432",
})
# The manifest is deliberately incomplete for live admission. An unknown asset
# is a refusal, never a dynamically learned permission from untrusted page code.
SCRIPT_DIGESTS = {
    "https://1-ss-sys.huaweicloudsite.cn/js/dist/site.min.js?v=202506121459":
        "d9e0a901236460425c9cef8a16bc06614b9336347f0384c420d840162b630da3",
}
PUBLIC_HOSTS = frozenset({"www.qiyunfang.com", "1-ss-sys.huaweicloudsite.cn", "jzfe-sys.huaweicloudsite.cn", "2-ss-sys.huaweicloudsite.cn", "jzs-sys.huaweicloudsite.cn"})


class PreparationTransport:
    """No request bodies, URLs or values retained in diagnostics.

    Install on a newly-created dedicated context before creating its first page;
    it is not safe to retrofit this onto a persistent or shared user context.
    READ_ONLY permits HTTPS GET public assets only, aborts redirects and every
    form endpoint. SEALED refuses routed HTTP and page WebSocket requests. This page-level
    WebSocket mock is not an adversarial-script/worker network sandbox.
    No method reopens the gate or grants final-submit authority.
    """
    def __init__(self, *, public_fetch=None):
        self.public_fetch = public_fetch
        self.phase = "READ_ONLY"
        self.blocked = 0
        self.installed = False
        self.context = None

    @staticmethod
    def _public_get(request):
        try:
            parsed = urlsplit(request.url)
            path = parsed.path.lower()
            return (request.method == "GET" and parsed.scheme == "https"
                    and request.url in PUBLIC_RESOURCES
                    and parsed.hostname in PUBLIC_HOSTS and parsed.netloc == parsed.hostname
                    and not parsed.username and not parsed.password and not parsed.fragment
                    and request.post_data is None
                    and not any(term in path for term in ("siteform_h.jsp", "membermodifysubmit"))
                    and "cmd=" not in parsed.query.lower())
        except (ValueError, AttributeError, TypeError):
            return False

    def install(self, context):
        if self.installed or context.pages or context.service_workers:
            raise RuntimeError("preparation transport requires an unused context")
        self.context, self.installed = context, True
        context.route("**/*", self._route)
        context.route_web_socket("**/*", self._websocket)
        context.on("page", self._page_created)

    def _page_created(self, page):
        # _route refuses popup navigations before fetch. Do not close a page
        # synchronously inside an event callback: that can nest an unbounded
        # browser RPC during the current primitive. The owner closes the whole
        # dedicated context after the next fence refuses its extra page.
        if len(self.context.pages) != 1:
            self.blocked += 1

    def _websocket(self, route):
        self.blocked += 1
        # Playwright-routed sockets are nonconnecting mocks until explicitly
        # connect_to_server() is called. Drop messages locally and return;
        # close() here nests closePage/frame.evaluate with no timeout.
        route.on_message(lambda _message: None)

    @staticmethod
    def _abort(route):
        try:
            route.abort("blockedbyclient")
        except Exception:
            # A closing/disconnected route is uncertain, never permission to
            # continue or a reason to log a potentially value-bearing URL.
            pass

    def _route(self, route):
        request = route.request
        try:
            navigation_ok = (not request.is_navigation_request() or
                (request.url == CONTRACT_URL and len(self.context.pages) == 1
                 and request.frame == self.context.pages[0].main_frame))
        except Exception:
            navigation_ok = False
        if self.phase != "READ_ONLY" or not navigation_ok or not self._public_get(request):
            self.blocked += 1
            self._abort(route)
            return
        try:
            # Never let the HTTP client or browser carry permission across a
            # redirect chain. Public content must be returned directly.
            response = (self.public_fetch(request.url) if self.public_fetch is not None
                        else route.fetch(max_redirects=0, max_retries=0, timeout=15000))
            if 300 <= response.status < 400 or self.phase != "READ_ONLY":
                self.blocked += 1
                self._abort(route)
            else:
                expected = SCRIPT_DIGESTS.get(request.url)
                if expected and hashlib.sha256(response.body()).hexdigest() != expected:
                    self.blocked += 1
                    self._abort(route)
                    return
                route.fulfill(response=response)
        except Exception:
            self.blocked += 1
            self._abort(route)

    def seal(self):
        if not self.installed or self.phase != "READ_ONLY":
            raise RuntimeError("preparation transport state conflict")
        self.phase = "SEALED"
        # Preserve unexpected activity from discovery; sealing is not a reset.

    def require_sealed(self):
        if not self.installed or self.phase != "SEALED" or self.blocked:
            raise RuntimeError("preparation transport changed")
