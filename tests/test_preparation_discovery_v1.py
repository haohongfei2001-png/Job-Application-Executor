"""A denied request never becomes a network permission or erased history."""
from types import SimpleNamespace
import pytest
from executor.preparation.discovery_denials import expected_discovery_denial,DENIED_PUBLIC_ASSETS
from executor.preparation.transport import PreparationTransport,SCRIPT_DIGESTS


def request(url,method='GET',body=None):
    return SimpleNamespace(url=url,method=method,post_data=body,is_navigation_request=lambda:False)


@pytest.mark.parametrize('url',sorted(DENIED_PUBLIC_ASSETS))
def test_observed_unused_asset_is_classified_but_never_fetched(url):
    calls=[];gate=PreparationTransport(public_fetch=lambda _:pytest.fail('denied asset fetched'))
    gate._route(SimpleNamespace(request=request(url),abort=lambda *_:calls.append('abort')))
    assert calls==['abort'] and gate.blocked==1 and gate.discovery_denials=={'unused_public_asset':1}


@pytest.mark.parametrize('url,method,body',[
 ('https://www.qiyunfang.com/ajax/site_h.jsp','POST','cmd=wafNotCk_checkBaiduAutomaticPush'),
 ('https://www.qiyunfang.com/ajax/log_h.jsp','POST','cmd=wafNotCk_dog&dogId=1&dogSrc=2'),
 ('https://www.qiyunfang.com/ajax/log_h.jsp?_v=1','POST','cmd=wafNotCk_logFdpForWebVitals'),
 ('https://www.qiyunfang.com/ajax/module_h.jsp','POST','cmd=getWafNotCk_getHiddenModuleList&_colId=124&_manageMode=false'),
 ('https://www.qiyunfang.com/ajax/login_h.jsp?cmd=wafNotCk_checkMemberSameTimeLogin','GET',None),
 ('https://www.qiyunfang.com/validateCode.jsp?749&vCodeId=15676','GET',None),
])
def test_known_background_commands_remain_denied(url,method,body):
    assert expected_discovery_denial(request(url,method,body))
    assert expected_discovery_denial(request(url.replace('www.qiyunfang.com','other.example'),method,body)) is None


@pytest.mark.parametrize('url,method,body',[
 ('https://www.qiyunfang.com/ajax/site_h.jsp','POST','cmd=setWafCk_set'),
 ('https://www.qiyunfang.com/ajax/log_h.jsp','POST','cmd=wafNotCk_dog&cmd=addWafCk_addSubmit'),
 ('https://www.qiyunfang.com/ajax/siteForm_h.jsp','POST','cmd=addWafCk_addSubmit'),
 ('https://www.qiyunfang.com/ajax/module_h.jsp','POST','cmd=deleteModule'),
 ('https://www.qiyunfang.com/validateCode.jsp?749&vCodeId=1','GET',None),
 ('https://www.qiyunfang.com/ajax/login_h.jsp?cmd=wafNotCk_checkMemberSameTimeLogin','GET','synthetic'),
])
def test_unknown_or_conflicting_activity_is_not_classified_away(url,method,body):
    assert expected_discovery_denial(request(url,method,body)) is None


def gate():
    result=PreparationTransport();result.installed=True
    result.resource_evidence=dict(SCRIPT_DIGESTS)
    result.public_read_evidence=[{'kind':'formId6_popup_lookup'},{'kind':'popup1566_public_dom'}]
    result.blocked=3;result.discovery_denials={'known_background_command_denied':3}
    return result


def test_certified_epoch_preserves_all_denials_and_rejects_post_seal_activity():
    transport=gate();receipt=transport.certify_discovery();transport.seal();transport.require_sealed()
    assert receipt['denial_epoch']==transport.blocked==3
    class NoBodyRead:
        method='POST';url='https://www.qiyunfang.com/ajax/log_h.jsp'
        def is_navigation_request(self):return False
        @property
        def post_data(self):pytest.fail('post-seal body inspected')
    transport._route(SimpleNamespace(request=NoBodyRead(),abort=lambda *_:None))
    assert transport.blocked==4 and transport.discovery_denials=={'known_background_command_denied':3}
    with pytest.raises(RuntimeError):transport.require_sealed()


@pytest.mark.parametrize('fault',['unknown_denial','missing_script','changed_script','unknown_script','missing_popup'])
def test_partial_or_unknown_discovery_never_certifies(fault):
    transport=gate()
    if fault=='unknown_denial':transport.blocked+=1
    elif fault=='missing_script':transport.resource_evidence.pop(next(iter(SCRIPT_DIGESTS)))
    elif fault=='changed_script':transport.resource_evidence[next(iter(SCRIPT_DIGESTS))]='0'*64
    elif fault=='unknown_script':transport.resource_evidence['https://www.qiyunfang.com/unknown.js']='0'*64
    else:transport.public_read_evidence.pop()
    with pytest.raises(RuntimeError):transport.certify_discovery()


@pytest.mark.parametrize('fault',['request','resource'])
def test_activity_between_certificate_and_seal_cannot_borrow_old_epoch(fault):
    transport=gate();transport.certify_discovery()
    if fault=='request':transport.blocked+=1;transport.discovery_denials['unused_public_asset']=1
    else:transport.resource_evidence['https://www.qiyunfang.com/extra.css']='0'*64
    transport.seal()
    with pytest.raises(RuntimeError):transport.require_sealed()


def test_zero_denial_epoch_still_rejects_a_changed_certificate():
    transport=gate();transport.blocked=0;transport.discovery_denials={}
    transport.certify_discovery()
    transport.public_read_evidence[1]['response_sha']='changed'
    transport.seal()
    with pytest.raises(RuntimeError):transport.require_sealed()


def test_identical_resource_refetch_after_certificate_still_invalidates_sealing():
    import hashlib
    from executor.preparation.qiyunfang import CONTRACT_URL
    transport=gate();raw=b'synthetic same document'
    transport.resource_evidence[CONTRACT_URL]=hashlib.sha256(raw).hexdigest()
    before=dict(transport.resource_evidence);transport.certify_discovery()
    transport.public_fetch=lambda _:SimpleNamespace(status=200,body=lambda:raw)
    transport._route(SimpleNamespace(request=request(CONTRACT_URL),fulfill=lambda **_:None,
                                     abort=lambda *_:pytest.fail('allowed predata fixture rejected')))
    assert transport.resource_evidence==before and transport.blocked==3
    transport.seal()
    with pytest.raises(RuntimeError):transport.require_sealed()


@pytest.mark.parametrize('fault',[None,'double_leading','internal_empty','duplicate','extra_command','other_column','missing_key'])
def test_source_shaped_stats_leading_ampersand_is_finite_denial_only(fault):
    body='&colId=124&pdId=-1&ndId=-1&browserType=1&screenType=1&sc=synthetic&rf=&visitUrl=synthetic&visitEquipment=1&statId=1'
    if fault=='double_leading':body='&'+body
    elif fault=='internal_empty':body=body.replace('&pdId','&&pdId')
    elif fault=='duplicate':body+='&colId=124'
    elif fault=='extra_command':body+='&cmd=addWafCk_addSubmit'
    elif fault=='other_column':body=body.replace('colId=124','colId=125')
    elif fault=='missing_key':body=body.replace('&rf=','')
    item=request('https://www.qiyunfang.com/ajax/statistics_h.jsp?cmd=wafNotCk_visited','POST',body)
    result=expected_discovery_denial(item)
    assert bool(result) is (fault is None)
    calls=[];transport=PreparationTransport(public_fetch=lambda _:pytest.fail('stats forwarded'))
    transport._route(SimpleNamespace(request=item,abort=lambda *_:calls.append('abort')))
    assert calls==['abort'] and transport.blocked==1
