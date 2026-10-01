"""Unregistered disposable browser owner. No CDP adoption or final-send method.

Only audited public GETs can be fetched by a separate API client and fulfilled.
The native browser's proxy can never forward traffic, even if routing disappears.
Live UI admission remains disabled until resource/lifecycle gates are complete.
"""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

from .network_fence import DenyOnlyProxy
from .transport import PreparationTransport, PUBLIC_RESOURCES


PRIVATE_TRANSPORT_FORBIDDEN_ENV = frozenset({
    'DEBUG','DEBUG_FILE','PWDEBUG','SSLKEYLOGFILE','CHROME_LOG_FILE','NODE_OPTIONS',
    'PW_TRACE_DIR','PLAYWRIGHT_TRACE_DIR','PLAYWRIGHT_HAR_PATH',
})


def require_private_transport_environment(environ=None):
    """Refuse inherited recording/debug modes before driver/browser creation.

    Do not silently modify corporate proxy/trust settings or mutate global
    process environment. The eventual dedicated child launcher must sanitize
    its own debug environment; an ambiguous caller remains unsupported.
    """
    source=os.environ if environ is None else environ
    if any(source.get(key) for key in PRIVATE_TRANSPORT_FORBIDDEN_ENV):
        raise RuntimeError('preparation_recording_environment_unsupported')


class DisposablePreparationSession:
    def __init__(self, *, headless=False, channel='chrome', backend='disposable_deny_proxy_v1'):
        if (backend != 'disposable_deny_proxy_v1' or type(headless) is not bool
                or channel not in {'chrome', None}):
            raise ValueError('unsupported_preparation_backend')
        self.headless, self.channel = headless, channel
        self.proxy = self.pw = self.browser = self.context = self.client = None
        self.transport = None

    def __enter__(self):
        if self.proxy is not None:raise RuntimeError('preparation_session_already_started')
        try:
            require_private_transport_environment()
            self.proxy = DenyOnlyProxy().__enter__()
            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch(headless=self.headless,channel=self.channel,
                                                  **self.proxy.browser_options())
            self.client = self.pw.request.new_context(ignore_https_errors=False)
            self.context = self.browser.new_context(service_workers='block',accept_downloads=False,
                                                    ignore_https_errors=False)
            self.transport = PreparationTransport(public_fetch=self._public_get)
            self.transport.install(self.context)
            return self
        except BaseException:
            self.close()
            raise RuntimeError('preparation_session_unavailable') from None

    def _public_get(self, url):
        # Transport already checked exact URL + method + no request body. Do
        # not forward arbitrary request headers, credentials, URLs or payloads.
        if url not in PUBLIC_RESOURCES:raise ValueError("public_resource_not_admitted")
        return self.client.get(url,max_redirects=0,max_retries=0,timeout=15000)

    def close(self):
        # Never remove the proxy while a browser might survive a failed close.
        # An uncertain browser stays fenced; caller must retain durable UNKNOWN.
        if self.browser is not None:
            try:self.browser.close()
            except Exception:return False
            self.browser=self.context=None
        if self.client is not None:
            try:self.client.dispose()
            except Exception:pass
            self.client=None
        if self.pw is not None:
            try:self.pw.stop()
            except Exception:return False
            self.pw=None
        if self.proxy is not None:
            self.proxy.__exit__(None,None,None);self.proxy=None
        return True

    def __exit__(self,*_):
        if not self.close():raise RuntimeError('preparation_context_closure_unknown')
