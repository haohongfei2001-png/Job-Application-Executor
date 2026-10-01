"""Actual owned process/context identity with only an empty synthetic popup."""
import sys
from executor.preparation.session import DisposablePreparationSession
from executor.preparation.process_identity import OwnedProcessIdentity
from executor.preparation.qiyunfang import CONTRACT_URL,digest
from test_qiyunfang_preparation_browser import replica,bounded_browser_oracle


def test_owned_disposable_process_identity_persists_and_requires_both_processes_absent(tmp_path):
    # Distinct declared targets, never a fallback after a failed sandbox launch.
    with DisposablePreparationSession(headless=True,channel='chrome' if sys.platform=='linux' else None) as owner:
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


def test_one_pass_cleanup_recovers_lost_acknowledgements_without_retrying_native_close():
    from types import SimpleNamespace
    owner=DisposablePreparationSession(headless=True,channel='chrome' if sys.platform=='linux' else None)
    owner.__enter__();events=[]
    try:
        identity=owner.identity;assert identity.absence()['status']=='PRESENT'
        browser,client,pw=owner.browser,owner.client,owner.pw
        def lost(name,callback):
            events.append(name);callback();raise OSError('synthetic post-effect acknowledgement loss')
        owner.browser=SimpleNamespace(close=lambda:lost('browser',browser.close))
        owner.client=SimpleNamespace(dispose=lambda:lost('client',client.dispose))
        owner.pw=SimpleNamespace(stop=lambda:lost('driver',pw.stop))
        assert owner.close() is True
        assert identity.absence()=={'status':'ABSENT','process_sha':identity.process_sha}
        assert events==['browser','client','driver'] and owner.proxy is None
        assert owner.close() is True and events==['browser','client','driver']
    finally:owner.close()
