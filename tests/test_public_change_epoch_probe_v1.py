"""No-network compatibility-probe sequencing, refusal and output privacy."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC=importlib.util.spec_from_file_location('epoch_probe',Path(__file__).resolve().parents[1]/'scripts/probe_qiyunfang_change_epoch.py')
probe=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(probe)


def install(monkeypatch,fail=None):
    events=[]
    def step(name):
        events.append(name)
        if name==fail:raise RuntimeError('PRIVATE_CANARY http://secret.invalid/?value=PRIVATE_CANARY')
    class Session:
        def __init__(self,**kwargs):
            assert kwargs=={'headless':True,'channel':'chrome' if probe.sys.platform=='linux' else None}
            self.context=object();self.browser=object()
            self.transport=SimpleNamespace(seal=lambda:step('network_seal'),require_sealed=lambda:step('network_check'))
            self.identity=SimpleNamespace(verify=lambda _:step('identity_check'))
        def __enter__(self):step('open_session');return self
        def __exit__(self,*_):step('close_session')
    class Observer:
        def __init__(self,context):step('install_before_page')
        def seal(self):step('epoch_seal')
        def unchanged(self):step('epoch_unchanged')
        def diagnostic(self):return 'INTEGRITY_CHANGED'
    def open_form(owner):
        step('open_empty_public_form')
        return SimpleNamespace(page=SimpleNamespace(wait_for_timeout=lambda ms:step('wait_2000') if ms==2000 else pytest.fail('unbounded wait')))
    monkeypatch.setattr(probe,'DisposablePreparationSession',Session)
    monkeypatch.setattr(probe,'PreDocumentChangeEpoch',Observer)
    monkeypatch.setattr(probe,'RetainedXHRClassifier',Observer)
    monkeypatch.setattr(probe,'open_public_form',open_form)
    return events


def test_compatibility_is_only_reported_after_empty_form_and_unchanged_owned_closure(monkeypatch):
    events=install(monkeypatch);result=probe.probe()
    assert result['status']=='EMPTY_PUBLIC_FORM_COMPATIBLE'
    assert events==['open_session','install_before_page','open_empty_public_form','network_seal','network_check',
                    'epoch_seal','wait_2000','epoch_unchanged','network_check','identity_check','close_session']
    assert result['closure_proven'] and result['unchanged_after_settle']
    assert result['live_enabled'] is False and result['final_action_enabled'] is False
    assert result['applicant_data_entered'] is False


@pytest.mark.parametrize('fault',[
    'open_session','install_before_page','open_empty_public_form','network_seal','network_check',
    'epoch_seal','wait_2000','epoch_unchanged','identity_check','close_session',
])
def test_every_failed_stage_is_finite_private_and_never_becomes_compatibility(monkeypatch,fault):
    events=install(monkeypatch,fault);result=probe.probe()
    assert result['status'] in {'EMPTY_PUBLIC_FORM_UNSUPPORTED','PUBLIC_EPOCH_UNAVAILABLE'}
    assert 'PRIVATE_CANARY' not in json.dumps(result)
    assert 'secret.invalid' not in json.dumps(result)
    assert result['live_enabled'] is result['final_action_enabled'] is result['applicant_data_entered'] is False
    if fault not in {'open_session','close_session'}:assert events[-1]=='close_session' and result['closure_proven']
    if fault in {'open_session','close_session'}:assert result['closure_proven'] is False


def test_cli_artifact_and_stdout_contain_only_finite_no_authority_report(monkeypatch,tmp_path,capfd):
    install(monkeypatch);output=tmp_path/'public-probe.json'
    assert probe.main(['--output',str(output)])==0
    value=json.loads(output.read_text());stdout=json.loads(capfd.readouterr().out)
    assert stdout=={'status':'EMPTY_PUBLIC_FORM_COMPATIBLE','stage':'CLOSE_OWNED_SESSION','observer_reason':'UNOBSERVED',
                    'live_enabled':False,'final_action_enabled':False}
    assert all(type(item) in {bool,str} for item in value.values())


def test_diagnostic_workflow_has_one_explicit_readonly_branch_and_no_private_authority():
    source=(Path(__file__).resolve().parents[1]/'.github/workflows/public-epoch-compatibility.yml').read_text()
    for required in ('branches: [test/jae-public-epoch-compatibility]',
                     'permissions:\n  contents: read','persist-credentials: false',
                     'timeout-minutes: 6','timeout-minutes: 2',
                     'tests/test_public_change_epoch_probe_v1.py',
                     'python scripts/probe_qiyunfang_change_epoch.py','retention-days: 3'):
        assert required in source
    for forbidden in ('pull_request','workflow_dispatch','secrets.','contents: write','write-all',
                      'continue-on-error','build_macos_app','profiles/','--ignore-certificate-errors'):
        assert forbidden not in source


def test_optional_classifier_probe_never_arms_a_final_request(monkeypatch):
    events=install(monkeypatch);result=probe.probe(xhr_classifier=True)
    assert result['classifier_installed'] is True
    assert result['status']=='EMPTY_PUBLIC_FORM_COMPATIBLE'
    assert result['applicant_data_entered'] is result['final_action_enabled'] is result['live_enabled'] is False
    assert events.index('install_before_page')<events.index('open_empty_public_form')
