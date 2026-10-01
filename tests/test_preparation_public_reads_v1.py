"""Only canonical, pre-data read requests. Never generalize a JSP path."""
from types import SimpleNamespace
import json
import pytest
from executor.preparation.public_reads import *


@pytest.mark.parametrize('body',[FORM_LOOKUP_BODY,'formId=6','&formId=7','&formId=6&formId=7','&formId=6&cmd=addWafCk_addSubmit','&form%49d=6','&formId=6&id=SYNTHETIC_PROTECTED'])
def test_lookup_body_is_exact_audited_literal(body):
    request=SimpleNamespace(method='POST',url=FORM_LOOKUP_URL,post_data=body)
    assert form_lookup_request(request) is (body==FORM_LOOKUP_BODY)


@pytest.mark.parametrize('method,url',[('GET',FORM_LOOKUP_URL),('POST',FORM_LOOKUP_URL+'&cmd=addWafCk_addSubmit'),('POST',FORM_LOOKUP_URL.replace('getWafNotCk_getFormPopupId','addWafCk_addSubmit'))])
def test_no_method_path_or_command_widening(method,url):
    assert not form_lookup_request(SimpleNamespace(method=method,url=url,post_data=FORM_LOOKUP_BODY))


@pytest.mark.parametrize('value',[{'success':True,'popupId':1566},{'success':False,'popupId':1566},{'success':True,'popupId':'1566'},{'success':True,'popupId':True},{'success':True,'popupId':0}])
def test_only_typed_public_popup_identity_is_retained(value):
    response=SimpleNamespace(status=200,body=lambda:json.dumps(value).encode())
    if type(value['popupId']) is int and value['popupId']>0 and value['success'] is True:
        assert set(lookup_evidence(response))=={'kind','popup_id','response_sha'}
    else:
        with pytest.raises(ValueError):lookup_evidence(response)


@pytest.mark.parametrize('query',['id=124&colId=124&extId=0&_csw=0&clientSupportWebp=true',
    'clientSupportWebp=false&_csw=0&extId=0&colId=124&id=124'])
def test_css_only_normalizes_exact_public_selectors_and_boolean_feature(query):
    assert public_style_url(CSS_URL+'?'+query)
    assert not public_style_url(CSS_URL+'?'+query+'&cmd=delete')
    assert not public_style_url(CSS_URL+'?'+query+'&id=125')
    assert not public_style_url(CSS_URL+'?'+query.replace('id=124','id=125'))


@pytest.mark.parametrize('body',[POPUP_BODY,POPUP_BODY+'&cmd=delete',POPUP_BODY.replace('undefined','0'),
    POPUP_BODY.replace('1566','1567'),POPUP_BODY.replace('getPopupZoneModule','deleteModule'),
    POPUP_BODY.replace('#2b2b2b','%232b2b2b'),POPUP_BODY+'&applicant=SYNTHETIC_PRIVATE'])
def test_popup_read_is_exact_observed_literal_not_general_module_authority(body):
    assert popup_request(SimpleNamespace(method='POST',url=POPUP_URL,post_data=body)) is (body==POPUP_BODY)


def test_popup_response_only_retains_public_hash_and_count():
    value={'success':True,'rtInfo':json.dumps({'moduleDomList':[{'dom':'<section id="module1566">public synthetic</section>'}]})}
    result=popup_evidence(SimpleNamespace(status=200,body=lambda:json.dumps(value).encode()))
    assert set(result)=={'kind','response_sha','module_count'} and result['module_count']==1
    for bad in ({'success':False},dict(value,rtInfo='{}'),dict(value,rtInfo='null')):
        with pytest.raises(ValueError):popup_evidence(SimpleNamespace(status=200,body=lambda:json.dumps(bad).encode()))


def test_popup_guard_requires_successful_lookup_and_one_shot_predata_phase():
    from executor.preparation.transport import PreparationTransport
    calls=[]
    lookup=SimpleNamespace(status=200,body=lambda:b'{"success":true,"popupId":1566}')
    popup=SimpleNamespace(status=200,body=lambda:json.dumps({'success':True,'rtInfo':json.dumps({'moduleDomList':[{'dom':'public'}]})}).encode())
    gate=PreparationTransport(public_lookup=lambda:calls.append('lookup') or lookup,
                              public_popup=lambda:calls.append('popup') or popup)
    def request(url,body):
        req=SimpleNamespace(method='POST',url=url,post_data=body,is_navigation_request=lambda:False)
        return SimpleNamespace(request=req,abort=lambda *_:calls.append('abort'),fulfill=lambda **_:calls.append('fulfill'))
    gate._route(request(POPUP_URL,POPUP_BODY));assert calls==['abort']
    gate._route(request(FORM_LOOKUP_URL,FORM_LOOKUP_BODY))
    gate._route(request(POPUP_URL,POPUP_BODY))
    assert calls==['abort','lookup','fulfill','popup','fulfill']
    gate._route(request(POPUP_URL,POPUP_BODY));assert calls[-1]=='abort'
    gate.installed=True;gate.seal()
    class ProtectedBody:
        method='POST';url=POPUP_URL
        def is_navigation_request(self):return False
        @property
        def post_data(self):pytest.fail('post-seal request body inspected')
    gate._route(SimpleNamespace(request=ProtectedBody(),abort=lambda *_:calls.append('abort')))
    assert calls.count('popup')==1


def test_popup_broker_reconstructs_literal_without_forwarding_request_headers():
    from executor.preparation.session import DisposablePreparationSession
    calls=[];owner=DisposablePreparationSession()
    owner.client=SimpleNamespace(post=lambda *a,**kw:calls.append((a,kw)))
    owner._public_popup()
    assert calls==[((POPUP_URL,),{'data':POPUP_BODY,'headers':{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},
                               'max_redirects':0,'max_retries':0,'timeout':15000})]


def test_all_observed_script_versions_include_the_audited_popup_and_form_handlers():
    from executor.preparation.transport import SCRIPT_DIGESTS,PreparationTransport
    assert len(SCRIPT_DIGESTS)==19
    assert SCRIPT_DIGESTS['https://1-ss-sys.huaweicloudsite.cn/js/dist/partitionSite.min.js?v=202511071120']=='400d6555c37e246737952ca0f9c948b813abd9ad16e8444fa8409b1e81d13651'
    assert SCRIPT_DIGESTS['https://1-ss-sys.huaweicloudsite.cn/js/dist/module.min.js?v=202506121459']=='0050b03259f7044c5666b42a3075dabf9607c8d179eac070798a894997132031'
    for url in SCRIPT_DIGESTS:
        calls=[];gate=PreparationTransport(public_fetch=lambda _:SimpleNamespace(status=200,body=lambda:b'changed synthetic source'))
        request=SimpleNamespace(method='GET',url=url,post_data=None,is_navigation_request=lambda:False)
        gate._route(SimpleNamespace(request=request,abort=lambda *_:calls.append('abort'),
                                    fulfill=lambda **_:pytest.fail('changed source executed')))
        assert calls==['abort'] and gate.blocked==1
