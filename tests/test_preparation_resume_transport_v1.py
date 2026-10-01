"""Trusted outbound reconstruction. Incoming upload bodies are never accessed."""
import json
from types import SimpleNamespace
import pytest

from executor.preparation.authority import PreparationConflict
from executor.preparation.resume_transport import ResumeRequestBroker,LOOKUP_URL,UPLOAD_URL,SPLIT_BYTES
from test_preparation_resume_journal_v1 import ready,resume,fixture,approve,rows


@pytest.fixture
def broker(ready):
    source,material,journal,valid=ready;approve(journal,select=False);events=[];responses=[]
    class Response:
        status=200
        def body(self):return b'{"success":true,"size":0,"id":"SYNTHETIC_ATTACHMENT"}'
        def dispose(self):events.append(('dispose',))
    def post(url,**kwargs):
        events.append(('post',url,kwargs));response=Response();responses.append(response);return response
    value=ResumeRequestBroker(page=SimpleNamespace(main_frame='owned-frame'),client=SimpleNamespace(post=post),journal=journal,material=material)
    payload=value.begin_selection();assert payload['buffer']==source[3]
    return ready,value,events,responses


class Request:
    method='POST';frame='owned-frame'
    def __init__(self,upload=False):
        self.url=(UPLOAD_URL+'?cmd=_mobiupload&app=21&type=0&fileUploadLimit=1&pieceUpload=true&checkId=6&checkItemId=11&bizType=5') if upload else LOOKUP_URL
        self.headers={'content-type':'multipart/form-data; boundary=SyntheticBoundary' if upload else 'application/x-www-form-urlencoded; charset=UTF-8'}
    def is_navigation_request(self):return False
    @property
    def post_data(self):pytest.fail('untrusted upload body extracted')
    @property
    def post_data_buffer(self):pytest.fail('untrusted upload bytes extracted')
    @property
    def post_data_json(self):pytest.fail('untrusted upload body parsed')


class Route:
    def __init__(self,upload=False):self.request=Request(upload);self.events=[];self.on_fulfill=lambda:None
    def abort(self,*_):self.events.append('abort')
    def fulfill(self,**kwargs):self.events.append('fulfill');self.on_fulfill()


def test_only_reviewed_file_and_fixed_metadata_are_sent_without_incoming_body_or_header_copy(broker):
    (((q,*_),*_),material,_,_),value,events,_=broker
    routes=[Route(),Route(),Route(True)]
    for route in routes:assert value.handle(route)['status']=='ARMED'
    result=value.finish()
    assert result['status']=='RETURNED_UNVERIFIED' and not result['server_attachment_verified'] and not result['submit_capability']
    posts=[event for event in events if event[0]=='post'];assert len(posts)==3
    for _,url,options in posts[:2]:
        assert url==LOOKUP_URL and set(options)=={'form','headers','max_redirects','max_retries','timeout'}
        assert options['form']=={'fileMd5':value._nonce,'fileSplitSize':str(SPLIT_BYTES),
            'fileName':material.private_review()['destination_filename'],'totalSize':str(material.private_review()['byte_count'])}
    _,target,options=posts[2]
    assert target==routes[2].request.url and set(options)=={'multipart','headers','max_redirects','max_retries','timeout'}
    assert options['multipart']=={'filedata':material._file_payload(),'fileMd5':value._nonce,
        'totalSize':str(material.private_review()['byte_count']),'complete':'true','initSize':'0'}
    assert all(event[2]['max_redirects']==event[2]['max_retries']==0 for event in posts)
    assert all(event[2]['headers']=={'X-Requested-With':'XMLHttpRequest'} for event in posts)
    assert all(route.events==['fulfill'] for route in routes)
    assert rows(q)[0]['outcome']=='RETURNED_UNVERIFIED'


def test_synchronous_nested_next_request_can_follow_only_a_durable_returned_predecessor(broker):
    (((q,*_),*_),_,_,_),value,events,_=broker;routes=[Route(),Route(),Route(True)]
    def nested(index):
        assert rows(q,'preparation_resume_stages')[index]['outcome']=='RETURNED_UNVERIFIED'
        value.handle(routes[index+1])
    routes[0].on_fulfill=lambda:nested(0);routes[1].on_fulfill=lambda:nested(1)
    value.handle(routes[0])
    assert value._active==0 and value.ready_to_finish()
    assert value.finish()['status']=='RETURNED_UNVERIFIED'
    assert len([event for event in events if event[0]=='post'])==3


@pytest.mark.parametrize('change',['origin','extra_query','wrong_command','method','frame','authorization','content_type','upload_first'])
def test_unapproved_request_shape_aborts_before_any_client_send(broker,change):
    (((q,*_),*_),_,_,_),value,events,_=broker;route=Route(change=='upload_first')
    if change=='origin':route.request.url=route.request.url.replace('www.qiyunfang.com','unapproved.example')
    elif change=='extra_query':route.request.url+='&identity=SYNTHETIC_FORBIDDEN'
    elif change=='wrong_command':route.request.url=UPLOAD_URL+'?cmd=addWafCk_addSubmit'
    elif change=='method':route.request.method='GET'
    elif change=='frame':route.request.frame='other-frame'
    elif change=='authorization':route.request.headers['authorization']='NEVER_COPY_SECRET'
    elif change=='content_type':route.request.headers['content-type']='application/json'
    assert value.handle(route)['status']=='UNKNOWN'
    assert not any(event[0]=='post' for event in events) and route.events==['abort']
    assert rows(q)[0]['outcome']=='UNKNOWN_OUTCOME'


@pytest.mark.parametrize('where',['stage_commit','last_guard','send','fulfill','dispose'])
def test_cancellation_and_reentrancy_never_upgrade_or_replay(broker,where):
    (((q,*_),*_),_,journal,_),value,events,responses=broker;route=Route()
    if where=='stage_commit':
        original=journal.begin_stage
        def consume(name):
            result=original(name);value.cancel();return result
        journal.begin_stage=consume
    elif where=='last_guard':
        original=journal.guard
        def guard(stage=None):
            result=original(stage)
            if stage is not None:value.cancel()
            return result
        journal.guard=guard
    elif where=='send':
        original=value.client.post
        def post(*args,**kwargs):
            result=original(*args,**kwargs);value.cancel();return result
        value.client.post=post
    elif where=='fulfill':route.on_fulfill=value.cancel
    else:
        original=value.client.post
        def post(*args,**kwargs):
            result=original(*args,**kwargs);result.dispose=value.cancel;return result
        value.client.post=post
    assert value.handle(route)['status']=='UNKNOWN'
    sent=len([event for event in events if event[0]=='post'])
    assert sent==(0 if where in {'stage_commit','last_guard'} else 1)
    value.handle(Route());assert len([event for event in events if event[0]=='post'])==sent
    assert rows(q)[0]['outcome']=='UNKNOWN_OUTCOME'


@pytest.mark.parametrize('reply',[b'{"success":true,"size":1}',b'{"success":true,"size":false}',b'{"success":false}',b'{"success":true,"success":true,"size":0}',b'x'*16385],ids=['nonzero','boolean_size','rejected','duplicate_key','oversized'])
def test_nonzero_resume_or_malformed_lookup_cannot_start_upload(broker,reply):
    (((q,*_),*_),_,_,_),value,events,_=broker
    value.client.post=lambda *_,**__:SimpleNamespace(status=200,body=lambda:reply,dispose=lambda:None)
    value.handle(Route())
    assert value._state=='UNKNOWN' and rows(q)[0]['outcome']=='UNKNOWN_OUTCOME'
    assert len(rows(q,'preparation_resume_stages'))==1


def test_reconstructed_broker_cannot_repeat_native_file_selection(broker):
    (_,material,journal,_),value,_,_=broker
    replacement=ResumeRequestBroker(page=value.page,client=value.client,journal=journal,material=material)
    with pytest.raises(PreparationConflict):replacement.begin_selection()
