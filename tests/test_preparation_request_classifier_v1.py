"""Native identity/state proof with body getters that must never be touched."""
from types import SimpleNamespace
import pytest
from executor.preparation import request_classifier as c
from test_preparation_change_epoch_v1 import Context


class Page:
    def __init__(self,context,owner):
        self.events={};self.frames=[object()];self.main_frame=self.frames[0]
        self.owner=owner;self.sealed=False;self.epoch=0;self.capture=False
        context.pages.append(self);context.events['page'](self)
    def on(self,name,callback):self.events[name]=callback
    def is_closed(self):return False
    def evaluate(self,source,args):
        if type(args) is dict:
            return {'format':'preparation-change-epoch-v1','epoch':self.epoch,'invalid':False,'sealed':self.sealed}
        if '.arm()' in source:return True
        return {'format':'retained-xhr-classifier-v1','state':'CAPTURED' if self.capture else 'ARMED',
                'nonce':self.owner.capture_nonce if self.capture else None,'change_epoch':self.epoch if self.capture else None}


class Request:
    method='POST';url=c.FINAL_URL;resource_type='xhr';redirected_from=None
    def __init__(self,page):self.frame=page.main_frame;self.headers_read=[]
    def is_navigation_request(self):return False
    def header_value(self,name):self.headers_read.append(name);return 'application/x-www-form-urlencoded; charset=UTF-8'
    @property
    def post_data(self):pytest.fail('protected request body read')
    @property
    def post_data_buffer(self):pytest.fail('protected request bytes read')
    @property
    def headers(self):pytest.fail('full header collection read')


def ready():
    context=Context();owner=c.RetainedXHRClassifier(context);page=Page(context,owner)
    owner.arm();request=Request(page);owner._request_started(request)
    page.capture=page.sealed=True
    return context,owner,page,request


def test_one_ordered_bootstrap_and_metadata_from_the_exact_retained_request():
    context,owner,page,request=ready()
    assert len(context.scripts)==1
    source=context.scripts[0]
    assert source.index('retained-xhr-classifier-v1')<source.index('preparation-change-epoch-v1')
    meta=owner.classify(request)
    assert meta=={'contract':c.CONTRACT_VERSION,'command':'addWafCk_addSubmit','form_id':6,
                  'role':c.CONTRACT_ROLE,'category':'final_application','change_epoch':0}
    assert request.headers_read==['content-type'] and owner.require_exact(request)==0
    assert owner.require_exact(request)==0


@pytest.mark.parametrize('fault',['different_object','fetch','redirect','wrong_frame','wrong_url','get','bad_type','missing_capture','nonce','epoch','second_request'])
def test_ambiguous_capture_is_permanently_refused_without_body_access(fault):
    context,owner,page,request=ready()
    actual=request
    if fault=='different_object':actual=Request(page)
    elif fault=='fetch':request.resource_type='fetch'
    elif fault=='redirect':request.redirected_from=object()
    elif fault=='wrong_frame':request.frame=object()
    elif fault=='wrong_url':request.url=c.FINAL_URL+'?unexpected=1'
    elif fault=='get':request.method='GET'
    elif fault=='bad_type':request.header_value=lambda _: 'multipart/form-data'
    elif fault=='missing_capture':page.capture=False
    elif fault in {'nonce','epoch'}:
        original=page.evaluate
        def changed(source,args):
            value=original(source,args)
            if type(args) is str:value['nonce' if fault=='nonce' else 'change_epoch']='wrong' if fault=='nonce' else 7
            return value
        page.evaluate=changed
    elif fault=='second_request':owner._request_started(Request(page))
    with pytest.raises(c.RequestClassifierConflict):owner.classify(actual)
    with pytest.raises(c.RequestClassifierConflict):owner.classify(request)
    with pytest.raises(c.RequestClassifierConflict):owner.arm()


def test_python_equality_cannot_substitute_another_request_object():
    _,owner,page,request=ready()
    class EqualRequest(Request):
        def __eq__(self,_):return True
    with pytest.raises(c.RequestClassifierConflict):owner.classify(EqualRequest(page))


@pytest.mark.parametrize('event',['framenavigated','frameattached','close','crash','worker'])
def test_native_owner_loss_after_arm_is_terminal_even_before_capture(event):
    context=Context();owner=c.RetainedXHRClassifier(context);page=Page(context,owner);owner.arm()
    page.events[event](object())
    request=Request(page);owner._request_started(request);page.capture=page.sealed=True
    with pytest.raises(c.RequestClassifierConflict):owner.classify(request)


def test_existing_inflight_requests_cannot_be_armed_away():
    context=Context();owner=c.RetainedXHRClassifier(context);page=Page(context,owner)
    request=Request(page);owner._request_started(request)
    with pytest.raises(c.RequestClassifierConflict):owner.arm()
    owner._request_finished(request)
    with pytest.raises(c.RequestClassifierConflict):owner.arm()


def test_request_failure_and_epoch_change_cannot_refund_capture():
    _,owner,page,request=ready();owner.classify(request)
    owner._request_failed(request)
    with pytest.raises(c.RequestClassifierConflict):owner.require_exact(request)
    _,owner,page,request=ready();owner.classify(request);page.epoch=1
    with pytest.raises(c.RequestClassifierConflict):owner.require_exact(request)
    page.epoch=0
    with pytest.raises(c.RequestClassifierConflict):owner.require_exact(request)


def test_evaluate_failure_emits_only_fixed_error():
    _,owner,page,request=ready()
    def fail(*_):raise RuntimeError('PRIVATE_REQUEST_CANARY')
    page.evaluate=fail
    with pytest.raises(c.RequestClassifierConflict) as error:owner.classify(request)
    assert str(error.value)=='retained_xhr_classifier_conflict'
    assert error.value.__suppress_context__ is True


def test_public_fixture_builder_uses_the_real_contract_field_schema():
    from urllib.parse import parse_qs
    import json
    from test_preparation_request_classifier_browser import body
    params=parse_qs(body())
    rows=json.loads(params['submitContentList'][0])
    assert len(rows)==16
    assert next(row for row in rows if row['id']==8)['val']==c.CONTRACT_ROLE


@pytest.mark.parametrize('variable',['DEBUG','PWDEBUG','SSLKEYLOGFILE','PLAYWRIGHT_HAR_PATH'])
def test_recording_environment_refused_before_any_classifier_install(monkeypatch,variable):
    context=Context();monkeypatch.setenv(variable,'SYNTHETIC_PRIVATE_CANARY')
    with pytest.raises(RuntimeError,match='preparation_recording_environment_unsupported'):
        c.RetainedXHRClassifier(context)
    assert context.scripts==[] and context.events=={}


def test_request_storm_retention_stays_bounded_after_terminal_refusal():
    _,owner,page,request=ready()
    for _ in range(1000):owner._request_started(Request(page))
    assert owner.invalid and len(owner._attempts)==2
    with pytest.raises(c.RequestClassifierConflict):owner.classify(request)
    context=Context();owner=c.RetainedXHRClassifier(context);page=Page(context,owner)
    for _ in range(1000):owner._request_started(Request(page))
    assert owner.invalid and len(owner._inflight)==256 and owner._attempts==[]
    with pytest.raises(c.RequestClassifierConflict):owner.arm()


def test_research_workflow_is_branch_scoped_readonly_and_never_arms_the_public_site():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'.github/workflows/retained-xhr-classifier.yml').read_text()
    for required in ('branches: [test/jae-retained-xhr-classifier]','contents: read','persist-credentials: false',
                     'timeout-minutes: 8','timeout-minutes: 2','test_preparation_request_classifier_browser.py',
                     '--xhr-classifier','retention-days: 3'):
        assert required in source
    for forbidden in ('pull_request','workflow_dispatch','secrets.','contents: write','write-all',
                      'continue-on-error','build_macos_app','profiles/','--ignore-certificate-errors'):
        assert forbidden not in source
