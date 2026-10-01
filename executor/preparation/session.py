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
from .process_identity import OwnedProcessIdentity
from .observation import observe_owned_document
from .public_reads import FORM_LOOKUP_URL, FORM_LOOKUP_BODY, POPUP_URL, POPUP_BODY, public_style_url


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
        self.identity = None

    def __enter__(self):
        if self.proxy is not None:raise RuntimeError('preparation_session_already_started')
        phase='PRIVATE_ENVIRONMENT'
        try:
            require_private_transport_environment()
            phase='DENY_LISTENER'
            self.proxy = DenyOnlyProxy().__enter__()
            phase='DRIVER_START'
            self.pw = sync_playwright().start()
            phase='SANDBOXED_BROWSER_LAUNCH'
            self.browser = self.pw.chromium.launch(headless=self.headless,channel=self.channel,
                                                  **self.proxy.browser_options())
            phase='NATIVE_LAUNCH_RECIPE'
            self.proxy.verify_launch(self.browser)
            phase='OWNED_PROCESS_IDENTITY'
            self.identity = OwnedProcessIdentity.capture(self.pw,self.browser)
            phase='API_CLIENT'
            self.client = self.pw.request.new_context(ignore_https_errors=False)
            phase='EMPTY_CONTEXT'
            self.context = self.browser.new_context(service_workers='block',accept_downloads=False,
                                                    ignore_https_errors=False)
            phase='REQUEST_GUARD'
            self.transport = PreparationTransport(public_fetch=self._public_get,public_lookup=self._public_form_lookup,
                                                  public_popup=self._public_popup)
            self.transport.install(self.context)
            return self
        except BaseException:
            self.close()
            error=RuntimeError('preparation_session_unavailable')
            error.phase=phase
            raise error from None

    def _public_get(self, url):
        # Transport already checked exact URL + method + no request body. Do
        # not forward arbitrary request headers, credentials, URLs or payloads.
        if url not in PUBLIC_RESOURCES and not public_style_url(url):raise ValueError("public_resource_not_admitted")
        return self.client.get(url,max_redirects=0,max_retries=0,timeout=15000)

    def _public_form_lookup(self):
        return self.client.post(FORM_LOOKUP_URL,data=FORM_LOOKUP_BODY,
            headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},
            max_redirects=0,max_retries=0,timeout=15000)

    def _public_popup(self):
        return self.client.post(POPUP_URL,data=POPUP_BODY,
            headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},
            max_redirects=0,max_retries=0,timeout=15000)

    def observe(self,page,*,resources_sha):
        if self.identity is None or page.context is not self.context:
            raise RuntimeError('preparation_context_identity_unknown')
        before=self.identity.verify(self.browser)
        document=observe_owned_document(page,process_sha=before,resources_sha=resources_sha)
        if self.identity.verify(self.browser)!=before:
            raise RuntimeError('preparation_context_identity_changed')
        return document

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
        if self.identity is not None and self.identity.absence().get('status')!='ABSENT':
            return False
        if self.proxy is not None:
            self.proxy.__exit__(None,None,None);self.proxy=None
        return True

    def __exit__(self,*_):
        if not self.close():raise RuntimeError('preparation_context_closure_unknown')
