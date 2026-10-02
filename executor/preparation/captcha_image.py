"""One exact official CAPTCHA image for a retained human-review session.

Displays pixels only in the owned browser. Never solves a challenge, reads a
protected input, exports image bytes, or reopens the browser's deny-only proxy.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from urllib.parse import urljoin

from .authority import PreparationConflict
from .qiyunfang import CONTRACT_URL,ROOT

CAPTCHA_ORIGIN='https://www.qiyunfang.com'
MAX_IMAGE_BYTES=256*1024
MIMES=frozenset({'image/png','image/gif','image/jpeg','image/webp'})


def scoped_image_url(url):
    return (isinstance(url,str) and len(url)<256
            and re.fullmatch(re.escape(CAPTCHA_ORIGIN)+r'/validateCode\.jsp\?[0-9]{1,3}&vCodeId=15676',url) is not None)


def _image_signature(mime,raw):
    if mime=='image/png':return raw.startswith(b'\x89PNG\r\n\x1a\n')
    if mime=='image/gif':return raw.startswith((b'GIF87a',b'GIF89a'))
    if mime=='image/jpeg':return raw.startswith(b'\xff\xd8\xff')
    return mime=='image/webp' and raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'


class CaptchaImageBroker:
    def __repr__(self):return '<CaptchaImageBroker>'

    def __init__(self,owner,page,guard,*,clock=time.monotonic):
        self._thread=threading.get_ident();self.owner=owner;self.page=page
        self._guard=guard;self._clock=clock;self._state='IDLE';self._deadline=0
        self._request=None;self._target=None;self._attempts=0
        if (page.context is not owner.context or owner.context.pages!=[page]
                or page.url!=CONTRACT_URL or not callable(guard)):raise PreparationConflict()
        guard();owner.transport.require_sealed()
        # Permanently retain this deny-on-terminal route. Never unroute a filled
        # page or fall through to broader permissions after an image attempt.
        owner.context.route('**/validateCode.jsp?*',self.handle)

    def _check(self,state):
        if (threading.get_ident()!=self._thread or self._state!=state
                or self._clock()>=self._deadline):raise PreparationConflict()
        self._guard();self.owner.transport.require_sealed()
        if self._state!=state or self._clock()>=self._deadline:raise PreparationConflict()

    @staticmethod
    def _abort(route):
        try:route.abort('blockedbyclient')
        except Exception:pass

    def handle(self,route):
        response=None
        try:
            self._check('REQUESTED')
            request=route.request
            if (request.method!='GET' or request.resource_type!='image'
                    or request.is_navigation_request() or request.redirected_from is not None
                    or request.frame is not self.page.main_frame or not scoped_image_url(request.url)
                    or request.url!=self._target):
                raise PreparationConflict()
            # Native image GET has no caller-supplied body. Do not inspect any
            # request body/header/cookie values. Preserve its opaque session.
            if self._attempts:raise PreparationConflict()
            self._attempts+=1
            self._state='ATTEMPTED';self._request=request
            self._check('ATTEMPTED')
            response=self.owner.client.fetch(request,max_redirects=0,max_retries=0,timeout=15000)
            self._check('ATTEMPTED')
            mime=response.headers.get('content-type','').split(';',1)[0].strip().lower()
            if response.status!=200 or mime not in MIMES:raise PreparationConflict()
            # APIRequestContext already buffers responses; this is a validation
            # limit, not a claim of streaming-memory or network-byte isolation.
            raw=response.body()
            if not 0<len(raw)<=MAX_IMAGE_BYTES or not _image_signature(mime,raw):raise PreparationConflict()
            self._check('ATTEMPTED');route.fulfill(response=response)
            self._check('ATTEMPTED');self._state='RETURNED'
        except Exception:
            self._state='UNAVAILABLE';self._abort(route)
        finally:
            if response is not None:
                try:response.dispose()
                except Exception:self._state='UNAVAILABLE'
            self._request=None

    def load(self):
        if self._state!='IDLE':raise PreparationConflict()
        self._deadline=self._clock()+20
        self._guard();self.owner.transport.require_sealed()
        image=self.page.locator(ROOT+' .validatecode_img')
        if image.count()!=1:raise PreparationConflict()
        original=urljoin(CONTRACT_URL,image.get_attribute('src') or '')
        if not scoped_image_url(original):raise PreparationConflict()
        # Source-pinned image scope, one fresh cache-busting value. No code is
        # read or inferred; only the person sees/solves the rendered challenge.
        previous=int(original.split('?',1)[1].split('&',1)[0])
        fresh=(previous+1+secrets.randbelow(999))%1000
        target=CAPTCHA_ORIGIN+'/validateCode.jsp?'+str(fresh)+'&vCodeId=15676'
        self._target=target;self._state='REQUESTED'
        self._check('REQUESTED')
        image.evaluate('(image,url)=>{if(image.tagName!=="IMG")throw new Error();image.src=url;}',target)
        # Playwright may resume this caller while its route callback is still
        # awaiting the opaque fetch/fulfill. Both states are in flight; neither
        # grants readiness or a second request. Keep the original deadline.
        while self._state in {'REQUESTED','ATTEMPTED'} and self._clock()<self._deadline:
            self._guard();self.page.wait_for_timeout(25)
        self._check('RETURNED')
        while not image.evaluate('image=>image.complete&&image.naturalWidth>0'):
            self._check('RETURNED');self.page.wait_for_timeout(25)
        self._check('RETURNED')
        return {'status':'CAPTCHA_DISPLAYED','automatic_retry':False,'submit_capability':False}

    def close(self):
        self._state='CLOSED';self._request=None
