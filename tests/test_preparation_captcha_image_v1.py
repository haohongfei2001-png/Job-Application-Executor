"""Exact approved image scope; no challenge solving, private field or live I/O."""
from types import SimpleNamespace
import pytest
from executor.preparation import captcha_image as c
from executor.preparation.authority import PreparationConflict

IMAGE=b'GIF89aSYNTHETIC_BITMAP'

class Request:
    method='GET';resource_type='image';redirected_from=None
    def is_navigation_request(self):return False
    @property
    def post_data(self):pytest.fail('image gate inspected request body')
    @property
    def headers(self):pytest.fail('image gate inspected cookie/header values')
    def __repr__(self):pytest.fail('request logged')

@pytest.fixture
def fixture():
    calls=[];routes=[];clock=[1.0];guard_ok=[True]
    frame=object();page=SimpleNamespace(main_frame=frame,url=c.CONTRACT_URL)
    context=SimpleNamespace(pages=[page],route=lambda pattern,fn:routes.append((pattern,fn)))
    page.context=context
    response=SimpleNamespace(status=200,headers={'content-type':'image/gif'},body=lambda:IMAGE,
                             dispose=lambda:calls.append('dispose'))
    request=Request();request.frame=frame;request.url=c.CAPTCHA_ORIGIN+'/validateCode.jsp?7&vCodeId=15676'
    route=SimpleNamespace(request=request,abort=lambda *_:calls.append('abort'),fulfill=lambda **kw:calls.append('fulfill'))
    def fetch(actual,**kwargs):
        assert actual is request and kwargs=={'max_redirects':0,'max_retries':0,'timeout':15000}
        calls.append('fetch');return response
    def guard():
        calls.append('guard')
        if not guard_ok[0]:raise PreparationConflict()
    owner=SimpleNamespace(context=context,transport=SimpleNamespace(require_sealed=lambda:calls.append('sealed')),
                          client=SimpleNamespace(fetch=fetch))
    broker=c.CaptchaImageBroker(owner,page,guard,clock=lambda:clock[0])
    broker._deadline=20;broker._target=request.url;broker._state='REQUESTED'
    return broker,request,route,response,calls,clock,guard_ok,page,routes


def test_exact_one_image_uses_opaque_request_and_retains_terminal_deny_route(fixture):
    broker,request,route,response,calls,_,_,_,routes=fixture
    broker.handle(route)
    assert broker._state=='RETURNED' and calls.count('fetch')==1 and calls.count('fulfill')==1
    assert calls[-1]=='dispose' and len(routes)==1
    broker.handle(route);assert calls.count('fetch')==1 and calls[-1]=='abort'
    broker.close();broker.handle(route);assert calls.count('fetch')==1


@pytest.mark.parametrize('url',[
    'http://www.qiyunfang.com/validateCode.jsp?7&vCodeId=15676',
    'https://evil.test/validateCode.jsp?7&vCodeId=15676',
    'https://www.qiyunfang.com.evil.test/validateCode.jsp?7&vCodeId=15676',
    'https://x@www.qiyunfang.com/validateCode.jsp?7&vCodeId=15676',
    'https://www.qiyunfang.com/validateCode.jsp?7&vCodeId=15677',
    'https://www.qiyunfang.com/validateCode.jsp?1000&vCodeId=15676',
    'https://www.qiyunfang.com/validateCode.jsp?7&vCodeId=15676&private=CANARY',
    'https://www.qiyunfang.com/validateCode.jsp?7&vCodeId=15676#x',
    'https://www.qiyunfang.com/ajax/siteForm_h.jsp?7&vCodeId=15676',None])
def test_wrong_image_scope_is_never_fetched(fixture,url):
    broker,request,route,_,calls,*_=fixture;request.url=url
    assert c.scoped_image_url(url) is False
    broker.handle(route);assert 'fetch' not in calls and calls[-1]=='abort'


@pytest.mark.parametrize('change',[{'method':'POST'},{'resource_type':'xhr'},{'redirected_from':object()},
    {'frame':object()},{'url':c.CAPTCHA_ORIGIN+'/validateCode.jsp?8&vCodeId=15676'}])
def test_changed_native_request_or_racing_image_cannot_use_image_permit(fixture,change):
    broker,request,route,_,calls,*_=fixture
    for key,value in change.items():setattr(request,key,value)
    broker.handle(route);assert 'fetch' not in calls


@pytest.mark.parametrize('fault',['redirect','text','svg','oversize','signature','empty'])
def test_response_refusal_never_fulfills_or_retries(fixture,fault):
    broker,_,route,response,calls,*_=fixture
    if fault=='redirect':response.status=302
    elif fault=='text':response.headers={'content-type':'text/html'}
    elif fault=='svg':response.headers={'content-type':'image/svg+xml'}
    elif fault=='oversize':response.body=lambda:b'GIF89a'+b'x'*c.MAX_IMAGE_BYTES
    elif fault=='signature':response.body=lambda:b'<svg>SYNTHETIC</svg>'
    else:response.body=lambda:b''
    broker.handle(route);broker.handle(route)
    assert calls.count('fetch')==1 and 'fulfill' not in calls and broker._state=='UNAVAILABLE'


@pytest.mark.parametrize('during',['before','fetch','fulfill','expire'])
def test_reentrant_loss_never_promotes_image_to_ready(fixture,during):
    broker,_,route,_,calls,clock,guard_ok,*_=fixture
    if during=='before':guard_ok[0]=False
    elif during=='expire':clock[0]=21
    elif during=='fetch':
        original=broker.owner.client.fetch
        def fetch(*a,**kw):
            result=original(*a,**kw);broker.close();return result
        broker.owner.client.fetch=fetch
    else:route.fulfill=lambda **_:broker.close()
    broker.handle(route);assert broker._state=='UNAVAILABLE'
    before=calls.count('fetch');broker.handle(route);assert calls.count('fetch')==before


def test_load_refreshes_only_verified_owned_image_and_never_reads_answer(fixture):
    broker,request,route,_,calls,_,_,page,_=fixture;broker._state='IDLE'
    class Image:
        def count(self):return 1
        def get_attribute(self,name):assert name=='src';return request.url
        def evaluate(self,script,*args):
            assert 'value' not in script
            if args:
                request.url=args[0];broker.handle(route)
            else:return True
    page.locator=lambda selector:Image()
    page.wait_for_timeout=lambda _:None
    assert broker.load()=={'status':'CAPTCHA_DISPLAYED','automatic_retry':False,'submit_capability':False}
    assert calls.count('fetch')==1
    with pytest.raises(PreparationConflict):broker.load()


@pytest.mark.parametrize('finish',['returned','cancelled','expired','unavailable'])
def test_load_waits_for_suspended_route_callback_without_renewing_deadline(fixture,finish):
    broker,request,_,_,calls,clock,_,page,_=fixture
    broker._state='IDLE';pumps=[]
    class Image:
        def count(self):return 1
        def get_attribute(self,name):assert name=='src';return request.url
        def evaluate(self,script,*args):
            if args:
                # Real Playwright can yield the caller after starting a route
                # callback, before its APIRequestContext fetch has returned.
                broker._state='ATTEMPTED';broker._attempts=1
                return None
            assert broker._state=='RETURNED'
            return True
    def pump(milliseconds):
        assert milliseconds==25 and broker._state=='ATTEMPTED'
        pumps.append(broker._deadline)
        if finish=='returned':broker._state='RETURNED'
        elif finish=='cancelled':broker.close()
        elif finish=='unavailable':broker._state='UNAVAILABLE'
        else:clock[0]=broker._deadline
    page.locator=lambda _:Image();page.wait_for_timeout=pump
    if finish=='returned':assert broker.load()['status']=='CAPTCHA_DISPLAYED'
    else:
        with pytest.raises(PreparationConflict):broker.load()
    assert pumps==[21.0] and broker._deadline==21.0
    assert broker._attempts==1 and 'fetch' not in calls


@pytest.mark.parametrize('url',['https://evil.test/','https://www.qiyunfang.com/h-col-124.html#x'])
def test_fixed_production_owner_gate_has_no_other_site_scope(url):
    from executor.preparation.private_child import approved_qiyunfang_owner
    from executor.preparation.session import DisposablePreparationSession
    owner=DisposablePreparationSession(headless=False,channel='chrome')
    owner.context=SimpleNamespace(pages=[SimpleNamespace(url=url)]);owner.identity=object()
    assert approved_qiyunfang_owner(owner) is False
    owner.context.pages[0].url=c.CONTRACT_URL
    assert approved_qiyunfang_owner(owner) is True
    owner._close_attempted=True;assert approved_qiyunfang_owner(owner) is False


def test_sibling_image_request_cannot_reuse_consumed_target(fixture):
    broker,request,route,_,calls,*_=fixture;broker.handle(route)
    sibling=Request();sibling.frame=request.frame;sibling.url=request.url
    sibling_route=SimpleNamespace(request=sibling,abort=lambda *_:calls.append('sibling_abort'),
                                  fulfill=lambda **_:pytest.fail('sibling fulfilled'))
    broker.handle(sibling_route)
    assert calls.count('fetch')==1 and calls[-1]=='sibling_abort'


def test_terminal_deny_observation_never_resets_strict_write_history():
    from executor.preparation.transport import PreparationTransport
    gate=PreparationTransport();gate.installed=True;gate.phase='SEALED'
    gate._sealed_certificate_valid=True;gate._sealed_denial_epoch=2;gate.blocked=3
    gate.require_terminal_sealed()
    assert gate.blocked==3 and gate._sealed_denial_epoch==2
    with pytest.raises(RuntimeError):gate.require_sealed()
    gate._accepted_attempts+=1
    with pytest.raises(RuntimeError):gate.require_terminal_sealed()
    assert gate._sealed_accepted_attempts==0
    gate._accepted_attempts=0
    for phase,installed,valid in [('READ_ONLY',True,True),('SEALED',False,True),('SEALED',True,False)]:
        gate.phase=phase;gate.installed=installed;gate._sealed_certificate_valid=valid
        with pytest.raises(RuntimeError):gate.require_terminal_sealed()


def test_terminal_guard_distinguishes_actual_denial_from_extra_admitted_callback():
    from executor.preparation.transport import PreparationTransport
    from executor.preparation.qiyunfang import CONTRACT_URL
    calls=[];response=SimpleNamespace(status=200,body=lambda:b'SYNTHETIC_PUBLIC_HTML')
    gate=PreparationTransport(public_fetch=lambda _:calls.append('fetch') or response)
    gate.installed=True;gate.context=SimpleNamespace(pages=[]);gate.seal()
    request=SimpleNamespace(method='GET',url=CONTRACT_URL,post_data=None,is_navigation_request=lambda:False)
    route=SimpleNamespace(request=request,abort=lambda *_:calls.append('abort'),fulfill=lambda **_:calls.append('fulfill'))
    gate._route(route)
    assert calls==['abort'] and gate._accepted_attempts==0
    gate.require_terminal_sealed()
    # A compromised/buggy callback that temporarily admits a request cannot
    # hide the attempt merely by restoring the phase flag afterward.
    gate.phase='READ_ONLY';gate._route(route);gate.phase='SEALED'
    assert calls==['abort','fetch','fulfill'] and gate._accepted_attempts==1
    assert gate._sealed_accepted_attempts==0
    with pytest.raises(RuntimeError):gate.require_terminal_sealed()
    with pytest.raises(RuntimeError):gate.require_sealed()


def test_terminal_transport_observation_has_only_the_spent_review_callsite():
    import ast
    from pathlib import Path
    root=Path(c.__file__).resolve().parents[1]
    locations=[]
    for path in root.rglob('*.py'):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.FunctionDef):
                for child in ast.walk(node):
                    if (isinstance(child,ast.Call) and isinstance(child.func,ast.Attribute)
                            and child.func.attr=='require_terminal_sealed'):
                        locations.append((path.relative_to(root).as_posix(),node.name))
    assert locations==[('preparation/human_review.py','terminal_tick')]
    for name in ('autonomy/dashboard.py','autonomy/supervisor.py','preparation/private_child.py'):
        assert '_accepted_attempts' not in (root/name).read_text()
