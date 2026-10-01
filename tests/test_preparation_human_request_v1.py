"""Synthetic native-dialog state machine; not evidence of physical-human input."""
from types import SimpleNamespace
import pytest

from executor.preparation import human_request as h

BINDING={'document':'synthetic-document','root':'synthetic-root','change_epoch':7,'frame_id':'native-frame'}
META={'contract':h.CONTRACT_VERSION,'command':'addWafCk_addSubmit','form_id':6,
      'role':h.CONTRACT_ROLE,'category':'final_application','change_epoch':7}


class Request:
    method='POST';url=h.FINAL_URL;frame='frame'
    def is_navigation_request(self):return False
    @property
    def post_data(self):pytest.fail('protected payload inspected')
    @property
    def post_data_buffer(self):pytest.fail('protected payload extracted')
    @property
    def headers(self):
        class OnlyContentType:
            def get(_,name,default=None):
                assert name=='content-type'
                return 'application/x-www-form-urlencoded; charset=UTF-8'
        return OnlyContentType()
    def __repr__(self):pytest.fail('request logged')


@pytest.fixture
def setup():
    events=[];clock=[10.0];request=Request()
    route=SimpleNamespace(request=request,abort=lambda *_:events.append('abort'),
                          fulfill=lambda **kw:events.append('fulfill'))
    page=SimpleNamespace(main_frame='frame',evaluate=lambda *args:events.append('present'))
    response=SimpleNamespace(status=200,dispose=lambda:events.append('dispose'))
    def fetch(actual,**kwargs):
        assert actual is request and kwargs=={'max_redirects':0,'max_retries':0,'timeout':15000}
        events.append('fetch');return response
    bridge=h.OpaqueHumanRequest(page=page,client=SimpleNamespace(fetch=fetch),binding=BINDING,
        guard=lambda:dict(BINDING),consume=lambda scope:events.append('consume') or 'a'*64,
        record=lambda intent,outcome:events.append(outcome) or outcome,clock=lambda:clock[0])
    return bridge,route,events,clock,response


def confirm(bridge):
    bridge.present()
    bridge.dialog_opened({'type':'confirm','url':h.CONTRACT_URL,'message':bridge._message,'frameId':'native-frame','hasBrowserHandler':True})
    bridge.dialog_closed({'result':True,'frameId':'native-frame'})


def test_only_matched_native_dialog_can_forward_exact_request_once_without_body_access(setup):
    bridge,route,events,_,_=setup
    view=bridge.hold(route,META);assert view['submit_capability'] is False
    assert bridge.drain()=={'status':'HELD'} and events==[]
    confirm(bridge);result=bridge.drain()
    assert result=={'status':'RETURNED_UNVERIFIED','automatic_retry':False,'server_application_verified':False}
    assert events==['present','consume','fetch','fulfill','dispose','RETURNED_UNVERIFIED']
    bridge.dialog_closed({'result':True,'frameId':'native-frame'});bridge.drain()
    assert events.count('fetch')==1


@pytest.mark.parametrize('fault',['close_without_open','wrong_url','wrong_message','wrong_type','false','expired','changed','duplicate'])
def test_unmatched_stale_replaced_or_duplicate_authority_never_sends(setup,fault):
    bridge,route,events,clock,_=setup;bridge.hold(route,META)
    if fault=='close_without_open':bridge.dialog_closed({'result':True,'frameId':'native-frame'})
    elif fault=='duplicate':
        with pytest.raises(h.HumanRequestConflict):bridge.hold(route,META)
    else:
        bridge.present();event={'type':'confirm','url':h.CONTRACT_URL,'message':bridge._message,'frameId':'native-frame','hasBrowserHandler':True}
        if fault=='wrong_url':event['url']='https://other.test/'
        if fault=='wrong_message':event['message']='other'
        if fault=='wrong_type':event['type']='alert'
        bridge.dialog_opened(event)
        if fault=='expired':clock[0]+=61
        bridge.dialog_closed({'result':fault!='false','frameId':'native-frame'})
        if fault=='changed':bridge.invalidate()
    bridge.drain();assert 'fetch' not in events and 'consume' not in events


@pytest.mark.parametrize('status',[301,302,307,308])
def test_redirect_is_not_followed_or_retried_and_response_is_disposed(setup,status):
    bridge,route,events,_,response=setup;response.status=status
    bridge.hold(route,META);confirm(bridge)
    assert bridge.drain()['status']=='UNKNOWN_OUTCOME'
    assert events[:5]==['present','consume','fetch','abort','dispose']
    assert set(events[5:])=={'UNKNOWN_OUTCOME'}
    bridge.drain();assert events.count('fetch')==1


def test_failed_durable_consumption_has_zero_network_effect(setup):
    bridge,route,events,_,_=setup
    def fail(_):raise OSError('synthetic commit uncertainty')
    bridge._consume=fail;bridge.hold(route,META);confirm(bridge)
    assert bridge.drain()['status']=='UNKNOWN_OUTCOME'
    assert 'fetch' not in events


def test_product_has_no_dialog_accept_or_protected_payload_reader():
    from pathlib import Path
    import ast
    source=Path(h.__file__).read_text();tree=ast.parse(source)
    forbidden={'accept','post_data','post_data_buffer','post_data_json','body','json','text','storage_state'}
    assert not [node.attr for node in ast.walk(tree) if isinstance(node,ast.Attribute) and node.attr in forbidden]


@pytest.mark.parametrize('event_patch',[{'hasBrowserHandler':False},{'frameId':'other'},{'hasBrowserHandler':None}])
def test_no_native_handler_or_wrong_frame_cannot_authorize(setup,event_patch):
    bridge,route,events,_,_=setup;bridge.hold(route,META);bridge.present()
    event={'type':'confirm','url':h.CONTRACT_URL,'message':bridge._message,'frameId':'native-frame','hasBrowserHandler':True}
    bridge.dialog_opened(dict(event,**event_patch));bridge.dialog_closed({'result':True,'frameId':'native-frame'})
    bridge.drain();assert 'consume' not in events and 'fetch' not in events


@pytest.mark.parametrize('during',['guard_before_consume','consume','guard_after_consume'])
@pytest.mark.parametrize('revoke',['cancel','invalidate'])
def test_reentrant_revocation_never_forwards_or_gets_overwritten(setup,during,revoke):
    bridge,route,events,_,_=setup;bridge.hold(route,META);confirm(bridge)
    original_guard=bridge._guard;original_consume=bridge._consume
    def guard():
        if ((during=='guard_before_consume' and bridge._state=='HUMAN_CONFIRMED')
                or (during=='guard_after_consume' and bridge._state=='FORWARD_ATTEMPTED')):
            getattr(bridge,revoke)()
        return original_guard()
    def consume(scope):
        result=original_consume(scope)
        if during=='consume':getattr(bridge,revoke)()
        return result
    bridge._guard=guard;bridge._consume=consume
    result=bridge.drain()
    assert 'fetch' not in events
    assert result['status'] in {'CANCELLED','UNKNOWN_OUTCOME'}
    if during!='guard_before_consume':assert 'UNKNOWN_OUTCOME' in events


@pytest.mark.parametrize('during',['fulfill','dispose','record'])
@pytest.mark.parametrize('revoke',['cancel','invalidate'])
def test_reentrant_post_send_revocation_cannot_be_promoted_to_clean_receipt(setup,during,revoke):
    bridge,route,events,_,response=setup;bridge.hold(route,META);confirm(bridge)
    def interrupt(*_,**__):getattr(bridge,revoke)()
    if during=='fulfill':route.fulfill=interrupt
    if during=='dispose':response.dispose=interrupt
    if during=='record':
        original=bridge._record
        def record(intent,outcome):original(intent,outcome);interrupt();return outcome
        bridge._record=record
    assert bridge.drain()['status']=='UNKNOWN_OUTCOME'
    assert bridge._state=='UNKNOWN'
    assert events.count('fetch')==1 and events[-1]=='UNKNOWN_OUTCOME'
    bridge.drain();assert events.count('fetch')==1


@pytest.mark.parametrize('content_type',['multipart/form-data; boundary=x','application/json','text/plain',''])
def test_final_bridge_does_not_imply_multipart_upload_permission(setup,content_type):
    bridge,route,events,_,_=setup
    class WithType(Request):
        @property
        def headers(self):return {'content-type':content_type}
    route.request=WithType()
    with pytest.raises(h.HumanRequestConflict):bridge.hold(route,META)
    assert events==['abort']


@pytest.mark.parametrize('reported',['UNKNOWN_OUTCOME',None,'invalid'])
def test_record_callback_downgrade_or_invalid_receipt_is_never_clean(setup,reported):
    bridge,route,events,_,_=setup;bridge.hold(route,META);confirm(bridge)
    bridge._record=lambda intent,outcome:events.append(outcome) or reported
    assert bridge.drain()['status']=='UNKNOWN_OUTCOME' and bridge._state=='UNKNOWN'
    assert events[-1]=='UNKNOWN_OUTCOME'
