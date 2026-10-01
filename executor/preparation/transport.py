"""Fail-closed request gate for a dedicated preparation-only browser context.

This is an HTTP/WebSocket guard, not an OS network sandbox. It never authorizes
submission or reconnects a filled document. A production session additionally
needs independently verified process-lifetime and human-transition boundaries.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from .qiyunfang import CONTRACT_URL, digest
from .discovery_denials import expected_discovery_denial
from .resources import OBSERVED_STATIC_RESOURCES, OBSERVED_SCRIPT_DIGESTS
from .public_reads import public_style_url, form_lookup_request, lookup_evidence, popup_request, popup_evidence

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
SCRIPT_DIGESTS = OBSERVED_SCRIPT_DIGESTS
PUBLIC_HOSTS = frozenset({"www.qiyunfang.com", "1-ss-sys.huaweicloudsite.cn", "jzfe-sys.huaweicloudsite.cn", "2-ss-sys.huaweicloudsite.cn", "jzs-sys.huaweicloudsite.cn"})


class PreparationTransport:
    """No request bodies, URLs or values retained in diagnostics.

    Install on a newly-created dedicated context before creating its first page;
    it is not safe to retrofit this onto a persistent or shared user context.
    READ_ONLY permits finite HTTPS GET assets and two exact one-shot public form
    bootstrap POSTs. All redirects and other commands are refused. SEALED
    refuses routed HTTP and page WebSocket requests. This page-level
    WebSocket mock is not an adversarial-script/worker network sandbox.
    No method reopens the gate or grants final-submit authority.
    """
    def __init__(self, *, public_fetch=None, public_lookup=None, public_popup=None):
        self.public_fetch = public_fetch
        self.public_lookup = public_lookup
        self.public_lookup_used = False
        self.public_popup = public_popup
        self.public_popup_used = False
        self.public_read_evidence = []
        self.resource_evidence = {}
        self.discovery_denials = {}
        self.discovery_sequence = 0
        self._certified_discovery = None
        self._sealed_denial_epoch = 0
        self._sealed_certificate_valid = True
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
                    and (request.url in PUBLIC_RESOURCES or public_style_url(request.url))
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
        self.discovery_sequence += 1
        # _route refuses popup navigations before fetch. Do not close a page
        # synchronously inside an event callback: that can nest an unbounded
        # browser RPC during the current primitive. The owner closes the whole
        # dedicated context after the next fence refuses its extra page.
        if len(self.context.pages) != 1:
            self.blocked += 1

    def _websocket(self, route):
        self.discovery_sequence += 1
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
        self.discovery_sequence += 1
        request = route.request
        try:
            navigation_ok = (not request.is_navigation_request() or
                (request.url == CONTRACT_URL and len(self.context.pages) == 1
                 and request.frame == self.context.pages[0].main_frame))
        except Exception:
            navigation_ok = False
        if self.phase != "READ_ONLY" or not navigation_ok:
            self.blocked += 1
            self._abort(route)
            return
        is_get = self._public_get(request)
        is_lookup = (not is_get and self.public_lookup is not None and not self.public_lookup_used
                     and form_lookup_request(request))
        is_popup = (not is_get and not is_lookup and self.public_popup is not None and not self.public_popup_used
                    and any(item.get('kind')=='formId6_popup_lookup' and item.get('popup_id')==1566 for item in self.public_read_evidence)
                    and popup_request(request))
        if not is_get and not is_lookup and not is_popup:
            self.blocked += 1
            category=expected_discovery_denial(request)
            if category:
                self.discovery_denials[category]=self.discovery_denials.get(category,0)+1
            self._abort(route)
            return
        try:
            if is_lookup or is_popup:
                # Retire this sole pre-data lookup before its bounded request.
                if is_lookup:
                    self.public_lookup_used = True
                    response = self.public_lookup()
                    evidence = lookup_evidence(response)
                else:
                    self.public_popup_used = True
                    response = self.public_popup()
                    evidence = popup_evidence(response)
                if self.phase != "READ_ONLY":raise RuntimeError("public lookup phase changed")
                self.public_read_evidence.append(evidence)
                route.fulfill(response=response)
                return
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
                self.resource_evidence[request.url]=hashlib.sha256(response.body()).hexdigest()
                route.fulfill(response=response)
        except Exception:
            self.blocked += 1
            self._abort(route)

    def certify_discovery(self):
        """Bind version-pinned external scripts and retained classified denials.

        Caller must independently validate the complete empty DOM. No denied
        request gets permission, and no historical counter is cleared here.
        Main HTML/popup bytes are observed and hashed, not semantically audited
        or pinned inline-script execution evidence.
        """
        if (self.phase!='READ_ONLY' or not self.installed
                or self.blocked!=sum(self.discovery_denials.values())
                or any(self.resource_evidence.get(url)!=sha for url,sha in SCRIPT_DIGESTS.items())
                or any(urlsplit(url).path.endswith('.js') and url not in SCRIPT_DIGESTS for url in self.resource_evidence)
                or [item.get('kind') for item in self.public_read_evidence]!=['formId6_popup_lookup','popup1566_public_dom']):
            raise RuntimeError('preparation_discovery_unverified')
        receipt=self._discovery_receipt()
        self._certified_discovery=(self.blocked,digest(receipt))
        return {'resources_sha':digest(receipt),'denied':dict(self.discovery_denials),'denial_epoch':self.blocked}

    def _discovery_receipt(self):
        return {'resources':dict(self.resource_evidence),'public_reads':list(self.public_read_evidence),
                'denied':dict(self.discovery_denials),'denial_epoch':self.blocked,
                'activity_sequence':self.discovery_sequence}

    def seal(self):
        if not self.installed or self.phase != "READ_ONLY":
            raise RuntimeError("preparation transport state conflict")
        self.phase = "SEALED"
        if self._certified_discovery is not None:
            self._sealed_certificate_valid=self._certified_discovery==(self.blocked,digest(self._discovery_receipt()))
            if self._sealed_certificate_valid:self._sealed_denial_epoch=self.blocked
        # Preserve unexpected activity from discovery; sealing is not a reset.

    def require_sealed(self):
        if (not self.installed or self.phase != "SEALED" or not self._sealed_certificate_valid
                or self.blocked!=self._sealed_denial_epoch):
            raise RuntimeError("preparation transport changed")
