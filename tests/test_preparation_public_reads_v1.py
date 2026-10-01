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
