"""Actual owned process/context identity with only an empty synthetic popup."""
from executor.preparation.session import DisposablePreparationSession
from executor.preparation.process_identity import OwnedProcessIdentity
from executor.preparation.qiyunfang import CONTRACT_URL,digest
from test_qiyunfang_preparation_browser import replica,bounded_browser_oracle


def test_owned_disposable_process_identity_persists_and_requires_both_processes_absent(tmp_path):
    with DisposablePreparationSession(headless=True,channel=None) as owner:
        assert owner.identity.absence()['status']=='PRESENT'
        sha=owner.identity.save(tmp_path.resolve())
        restored=OwnedProcessIdentity.load(tmp_path.resolve(),sha)
        assert restored.verify(owner.browser)==sha
        owner.context.route(CONTRACT_URL,lambda route:route.fulfill(status=200,content_type='text/html',body=replica()))
        page=owner.context.new_page();page.goto(CONTRACT_URL)
        document=owner.observe(page,resources_sha=digest('synthetic independent popup'))
        assert document.binding['process_sha']==sha
        assert owner.identity.absence()['status']=='PRESENT'
    assert restored.absence()=={'status':'ABSENT','process_sha':sha}
