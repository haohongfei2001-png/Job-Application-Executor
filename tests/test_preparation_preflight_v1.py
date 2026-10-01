"""Public bootstrap selection and ordering without profiles or browser effects."""
from types import SimpleNamespace
import pytest
from executor.preparation import preflight
from executor.preparation.qiyunfang import CONTRACT_URL,ROOT,ContractChanged
from test_qiyunfang_preparation_v1 import observation


@pytest.fixture
def owner():
    events=[];pages=[];root=SimpleNamespace(wait_for=lambda **_:events.append('root_visible'),evaluate=lambda _:observation())
    button=SimpleNamespace(count=lambda:1,get_attribute=lambda _:'javascript: JZ.transformForLinkFunc("linkForm", 6);',
                           click=lambda **_:events.append('open_public_popup'))
    def goto(url,**kwargs):assert url==CONTRACT_URL;events.append('public_get')
    page=SimpleNamespace(goto=goto,locator=lambda selector:root if selector==ROOT else button,wait_for_timeout=lambda _:None)
    def new_page():pages.append(page);return page
    transport=SimpleNamespace(phase='READ_ONLY',certify_discovery=lambda:events.append('certify_sources') or {'resources_sha':'a'*64})
    identity=SimpleNamespace(verify=lambda _:events.append('verify_process') or 'b'*64)
    def observe(actual,*,resources_sha):
        assert actual is page and resources_sha=='a'*64
        events.append('observe_document');return object()
    value=SimpleNamespace(identity=identity,browser=object(),context=SimpleNamespace(pages=pages,new_page=new_page),
                          transport=transport,observe=observe)
    return value,events,button


def test_readonly_loader_opens_only_fixed_public_link_then_certifies(owner):
    value,events,_=owner;result=preflight.open_public_form(value)
    assert result.page is value.context.pages[0] and result.resources_sha=='a'*64
    assert events==['verify_process','public_get','open_public_popup','root_visible','certify_sources','observe_document','verify_process']


@pytest.mark.parametrize('fault',['existing_page','wrong_link','unsealed_discovery'])
def test_unsupported_public_surface_does_not_create_an_offer_or_fill(owner,fault):
    value,events,button=owner
    if fault=='existing_page':value.context.pages.append(object())
    elif fault=='wrong_link':button.get_attribute=lambda _:'javascript: Site.submit()'
    else:value.transport.phase='SEALED'
    with pytest.raises(ContractChanged):preflight.open_public_form(value)
    assert 'open_public_popup' not in events and 'certify_sources' not in events
