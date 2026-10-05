"""Portable contracts for the independent owner; real acceptance is Mac-only."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import ci_signed_dmg_acceptance as oracle

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name,path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def adapted(tmp_path):
    source = tmp_path / 'source'
    oracle.copy_unmanifested_source(ROOT,source)
    original = (ROOT / 'executor/autonomy/publisher_policy.py').read_bytes()
    oracle.adapt_test_source(source,tmp_path)
    assert (ROOT / 'executor/autonomy/publisher_policy.py').read_bytes() == original
    return source,tmp_path


def test_adapter_is_fixed_test_source_before_manifest_and_all_processes_share_it(adapted):
    source,root = adapted
    policy = load(source / 'executor/autonomy/publisher_policy.py','test_fixed_publisher')
    assert policy.BUILT_IN_PUBLISHER_POLICY == oracle.POLICY
    assert policy.resolve_policy() == oracle.POLICY
    text = (source / 'executor/autonomy/publisher_policy.py').read_text()
    assert str(root) in text
    assert 'os.environ' not in text and 'getenv' not in text
    assert 'certificate leaf' not in text and 'anchor apple generic' not in text
    assert "'--verify', '--strict', '--all-architectures'" in text
    assert "'--test-requirement'" in text
    assert 'BUILT_IN_PUBLISHER_POLICY = None' in (ROOT / 'executor/autonomy/publisher_policy.py').read_text()
    assert not (source / 'release-source-manifest.json').exists()
    for path in source.rglob('*.py'):
        ast.parse(path.read_text())


def test_adapted_publisher_still_calls_actual_strict_all_arch_static_command(adapted,monkeypatch):
    source,root = adapted
    module = load(source / 'executor/autonomy/publisher_policy.py','test_static_publisher')
    module.sys = SimpleNamespace(platform='darwin')
    app = root / 'candidate.app';app.mkdir()
    calls = []
    monkeypatch.setattr(module.subprocess,'run',lambda argv,**kw:calls.append((argv,kw)) or SimpleNamespace(returncode=0))
    assert module.verify_publisher(app) == oracle.POLICY
    assert calls[0][0] == ['/usr/bin/codesign','--verify','--strict','--all-architectures',
        '--test-requirement','=identifier = "'+oracle.BUNDLE_ID+'"',str(app)]
    assert calls[0][1]['env'] == {'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'}
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1))
    with pytest.raises(ValueError,match='publisher_refused'):
        module.verify_publisher(app)


@pytest.mark.parametrize('fault',['wrong_team','wrong_bundle','outside','alias','parent_escape'])
def test_test_adapter_refuses_unapproved_policy_and_outside_root_before_verifier(adapted,monkeypatch,fault):
    source,root=adapted
    module=load(source/'executor/autonomy/publisher_policy.py','test_confined_publisher')
    module.sys=SimpleNamespace(platform='darwin')
    app=root/'candidate.app';app.mkdir();policy=dict(oracle.POLICY)
    if fault=='wrong_team':policy['team_id']='WRONGTEAM1'
    elif fault=='wrong_bundle':policy['bundle_id']='com.other.application'
    else:
        outside=root.parent/('outside-'+root.name);outside.mkdir(exist_ok=True)
        if fault=='outside':app=outside
        elif fault=='alias':
            app=root/'alias.app';app.symlink_to(outside,target_is_directory=True)
        else:app=root/'..'/outside.name
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:pytest.fail('unapproved fixture reached verifier'))
    with pytest.raises(ValueError,match='publisher'):
        module.verify_publisher(app,policy)


def test_adapter_cannot_modify_a_manifested_source(adapted):
    source,_=adapted
    (source/'release-source-manifest.json').write_text('{}')
    before=(source/'executor/autonomy/publisher_policy.py').read_bytes()
    with pytest.raises(ValueError,match='requires_unmanifested'):
        oracle.adapt_test_source(source,source.parent)
    assert (source/'executor/autonomy/publisher_policy.py').read_bytes()==before


def test_build_requirement_only_omits_certificate_identity(adapted):
    source,_=adapted
    helper=load(source/'scripts/prepare_macos_signing.py','test_signing_publisher')
    component=oracle.BUNDLE_ID+'.component.'+'a'*64
    for identifier in (oracle.BUNDLE_ID,component):
        assert helper._requirement(oracle.POLICY,identifier)=='identifier = "'+identifier+'"'
    for identifier in ('other',oracle.BUNDLE_ID+'.component.bad'):
        with pytest.raises(ValueError,match='identifier_refused'):
            helper._requirement(oracle.POLICY,identifier)
    with pytest.raises(ValueError,match='policy_refused'):
        helper._requirement({**oracle.POLICY,'team_id':'WRONGTEAM1'},oracle.BUNDLE_ID)


def test_fault_is_a_source_change_before_manifest_only_at_exact_final_serve_path(adapted):
    source,root=adapted
    target=root/'home/Applications'/oracle.APP_NAME
    trace=root/'fault-trace'
    oracle.inject_final_path_failure(source,target,trace)
    module=ast.parse((source/'executor/autonomy/cli.py').read_text())
    serve=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='serve')
    # Execute just the signed-in fault prologue; production serve remains after
    # it. No monkeypatch of health/transaction predicates appears in the source.
    prologue=ast.Module(body=serve.body[:3],type_ignores=[])
    for location,expected in ((source,'staging'),(target/'Contents/Resources/release','final')):
        scope={'Path':Path,'__file__':str(location/'executor/autonomy/cli.py')}
        if expected=='final':
            with pytest.raises(RuntimeError,match='synthetic_signed_final_path'):
                exec(compile(prologue,'<pre-sign fault>','exec'),scope)
        else:exec(compile(prologue,'<pre-sign fault>','exec'),scope)
    assert trace.read_text().splitlines()==['staging','final']
    assert not (source/'release-source-manifest.json').exists()


def receipt():
    return {'format':oracle.FORMAT,'status':'SYNTHETIC_SIGNED_DMG_ACCEPTANCE_CHECKED',
        'certification':'NOT_CERTIFIED','publisher':'ADHOC_TEST_COPY_ONLY',
        'consumer_admission':'NOT_ADMITTED','checked':sorted(oracle.REQUIRED),
        'signed_versions':3,'macho_objects_per_version':[32,32,32],
        'elapsed_seconds':1799,'phases':{'complete':1799},
        'coverage_per_version':[{'unsigned_runtime_files':30,'current_runtime_files':32,
            'added_runtime_files':['release-runtime-manifest.json','signing-input-bridge.json'],
            'wheel_count':1,'wheels':[['example-package','1.0']]} for _ in range(3)]}


@pytest.mark.parametrize('fault',['missing_case','duplicate_case','no_macos','too_long','no_time',
    'no_complete','missing_version','shrunk_runtime','different_catalog','certified'])
def test_acceptance_receipt_fails_closed_on_missing_or_skipped_evidence(fault):
    value=receipt()
    if fault=='missing_case':value['checked'].pop()
    elif fault=='duplicate_case':value['checked'].append(value['checked'][0])
    elif fault=='no_macos':value['status']='SKIPPED'
    elif fault=='too_long':value['elapsed_seconds']=1800
    elif fault=='no_time':value['elapsed_seconds']=0
    elif fault=='no_complete':value['phases']={}
    elif fault=='missing_version':value['signed_versions']=2
    elif fault=='shrunk_runtime':value['macho_objects_per_version']=[2,2,2]
    elif fault=='different_catalog':value['macho_objects_per_version']=[31,32,32]
    else:value['certification']='CERTIFIED'
    with pytest.raises(ValueError,match='receipt incomplete'):
        oracle.validate_receipt(value)
    assert oracle.validate_receipt(receipt())==receipt()


def test_owner_refuses_wrong_host_instead_of_skip_success(monkeypatch,tmp_path):
    monkeypatch.setattr(oracle.platform,'system',lambda:'Linux')
    monkeypatch.setattr(oracle.subprocess,'Popen',lambda *a,**kw:pytest.fail('wrong host executed oracle'))
    with pytest.raises(SystemExit) as result:
        oracle.main(['--standalone-runtime',str(tmp_path),'--receipt',str(tmp_path/'receipt')])
    assert result.value.code==2


def test_real_seed_and_typed_answer_readback_preserve_paused_journal(tmp_path):
    state=tmp_path/'state'
    env={**os.environ,'APPLICATION_EXECUTOR_BROWSER_MODE':'isolated'}
    result=subprocess.run([sys.executable,'-I','-B','-c',oracle.SEED_STATE,str(ROOT),str(state)],
        capture_output=True,text=True,env=env,timeout=30)
    assert result.returncode==0,result.stderr
    tasks=json.loads(result.stdout)['tasks']
    result=subprocess.run([sys.executable,'-I','-B','-c',oracle.VERIFY_STATE,str(ROOT),str(state),json.dumps(tasks)],
        capture_output=True,text=True,env=env,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'ok':True}


def test_owner_budget_aggregate_and_no_test_artifact_upload_are_mandatory():
    workflow=(ROOT/'.github/workflows/application-executor-ci.yml').read_text()
    job=workflow.split('\n  signed_dmg_acceptance:\n',1)[1].split('\n  full_suite:\n',1)[0]
    assert '    timeout-minutes: 40\n' in job
    assert '    runs-on: macos-latest\n' in job
    assert 'upload-artifact' not in job and 'continue-on-error' not in job
    assert 'python -I -B scripts/ci_signed_dmg_acceptance.py' in job
    assert 'Refuse stale or ineligible engineering closure head' in job
    aggregate=workflow.split('\n  test:\n',1)[1].split('\n  engineering_closure_macos:\n',1)[0]
    assert 'needs: [full_suite, signed_dmg_acceptance]' in aggregate
    assert 'needs.signed_dmg_acceptance.result' in aggregate
    assert 'needs.signed_dmg_acceptance.outputs.source_sha' in aggregate
    assert "os.environ['JAE_SIGNED_DMG_RESULT'] != 'success'" in aggregate
    assert "os.environ['JAE_SIGNED_DMG_SHA'] != expected" in aggregate
    assert oracle.WALL_SECONDS==1800
    script=(ROOT/'scripts/ci_signed_dmg_acceptance.py').read_text()
    assert 'process.wait(timeout=WALL_SECONDS)' in script
    assert 'terminate_tree(process.pid)' in script
    assert 'WIP, not acceptance' in script
    assert 'pytest.skip' not in script
    for name in ('ENTRY_DIAGNOSTICS','ENTRY_FIXTURE','ROLLBACK_FIXTURE','READERS',
                 'PRODUCTION_REFUSAL','SEED_STATE','VERIFY_STATE'):
        ast.parse(getattr(oracle,name))


@pytest.fixture
def executable_oracle(tmp_path):
    driver=oracle.Oracle(tmp_path)
    app=tmp_path/'executable-fixture.app'
    binary=app/'Contents/Resources/runtime/bin/python'
    binary.parent.mkdir(parents=True)
    binary.symlink_to(sys.executable)
    source=app/'Contents/Resources/release'
    (source/'executor/autonomy').mkdir(parents=True)
    (source/'executor/__init__.py').write_text('')
    (source/'executor/autonomy/__init__.py').write_text('')
    return driver,app,source


def test_app_run_parses_actual_multiline_cli_json(executable_oracle):
    driver,app,source=executable_oracle
    (source/'executor/autonomy/cli.py').write_text(
        'import json\nprint(json.dumps({"ok":True,"running":False},indent=2))\n')
    assert driver.cli(['health'],app=app)=={'ok':True,'running':False}


@pytest.mark.parametrize('code', [
    'import json;print("untrusted preface");print(json.dumps({"ok":True}))',
    'import json;print(json.dumps({"ok":False}));print(json.dumps({"ok":True}))',
    'import json;print(json.dumps([{"ok":True}]))',
])
def test_app_run_does_not_swallow_logs_or_multiple_json_documents(executable_oracle,code):
    driver,app,_=executable_oracle
    with pytest.raises(ValueError):driver.app_run(app,code)


def test_actual_exit_one_is_admitted_only_for_exact_expected_cli_refusal(executable_oracle):
    driver,app,source=executable_oracle
    cli=source/'executor/autonomy/cli.py'
    cli.write_text('import json\nprint(json.dumps({"ok":False,"reason":"rollback_unavailable"},indent=2))\nraise SystemExit(1)\n')
    expected={'ok':False,'reason':'rollback_unavailable'}
    assert driver.cli(['restore-app'],app=app,expected_refusal='rollback_unavailable')==expected
    with pytest.raises(AssertionError,match='oracle child failed'):
        driver.cli(['restore-app'],app=app)
    with pytest.raises(AssertionError):
        driver.cli(['restore-app'],app=app,expected_refusal='different_reason')
    cli.write_text('import json\nprint(json.dumps({"ok":True,"reason":"rollback_unavailable"},indent=2))\nraise SystemExit(1)\n')
    with pytest.raises(AssertionError):
        driver.cli(['restore-app'],app=app,expected_refusal='rollback_unavailable')
    cli.write_text('import json\nprint(json.dumps({"ok":False,"reason":"rollback_unavailable"},indent=2))\n')
    with pytest.raises(AssertionError,match='oracle child failed'):
        driver.cli(['restore-app'],app=app,expected_refusal='rollback_unavailable')
    cli.write_text('import json\nprint(json.dumps({"ok":False,"reason":"rollback_unavailable"},indent=2))\nraise SystemExit(2)\n')
    with pytest.raises(AssertionError,match='oracle child failed'):
        driver.cli(['restore-app'],app=app,expected_refusal='rollback_unavailable')


def test_xattr_fixture_uses_macos_tool_even_without_linux_python_apis(tmp_path,monkeypatch):
    driver=oracle.Oracle(tmp_path)
    target=tmp_path/'synthetic-file';target.write_bytes(b'UNCHANGED')
    monkeypatch.setattr(oracle.platform,'system',lambda:'Darwin')
    monkeypatch.delattr(oracle.os,'setxattr',raising=False)
    monkeypatch.delattr(oracle.os,'removexattr',raising=False)
    calls=[]
    monkeypatch.setattr(driver,'run',lambda args,**kw:calls.append((args,kw)))
    driver.test_xattr(target)
    driver.test_xattr(target,remove=True)
    assert calls==[
        (['/usr/bin/xattr','-w','com.example.jae-oracle','SYNTHETIC',target],{'timeout':30}),
        (['/usr/bin/xattr','-d','com.example.jae-oracle',target],{'timeout':30}),
    ]
    assert target.read_bytes()==b'UNCHANGED'


def test_xattr_exercise_never_substitutes_linux_api_for_macos_evidence(tmp_path,monkeypatch):
    driver=oracle.Oracle(tmp_path)
    target=tmp_path/'synthetic-file';target.write_bytes(b'UNCHANGED')
    monkeypatch.setattr(oracle.platform,'system',lambda:'Linux')
    monkeypatch.setattr(driver,'run',lambda *a,**kw:pytest.fail('wrong platform ran Mac fixture'))
    with pytest.raises(ValueError,match='requires real macOS'):
        driver.test_xattr(target)
    with pytest.raises(ValueError,match='requires real macOS'):
        driver.test_xattr(target,remove=True)
    assert target.read_bytes()==b'UNCHANGED'


def test_public_wheel_inventory_reads_real_owned_dist_info_with_existing_normalization(tmp_path):
    runtime=tmp_path/'runtime'
    site=runtime/'lib/python3.12/site-packages';site.mkdir(parents=True)
    for directory,name,version in [('Alpha_Pkg-1.0.dist-info','Alpha_Pkg','1.0'),
                                    ('beta.pkg-2.1.dist-info','beta.pkg','2.1')]:
        metadata=site/directory;metadata.mkdir()
        (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: '+name+'\nVersion: '+version+'\n')
        (metadata/'WHEEL').write_text('Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
    assert oracle.public_wheels(runtime)==[['alpha-pkg','1.0'],['beta-pkg','2.1']]


def test_runtime_coverage_is_exact_dynamic_inventory_not_fixed_file_or_wheel_counts():
    unsigned=['bin/python','lib/python3.12/site-packages/example-1.0.dist-info/WHEEL','ordinary.py']
    current=[*unsigned,'release-runtime-manifest.json','signing-input-bridge.json']
    wheels=[['example','1.0']]
    observed={'runtime_files':5,'wheel_count':1,'wheels':wheels}
    result=oracle.coverage_record(unsigned,current,wheels,observed)
    assert result['unsigned_runtime_files']==3 and result['current_runtime_files']==5
    assert result['wheel_count']==1 and result['wheels']==wheels
    for changed in ([*current,'unexpected'],current[1:],[*current,current[0]]):
        with pytest.raises(AssertionError):oracle.coverage_record(unsigned,changed,wheels,observed)
    for changed in ({**observed,'runtime_files':4},{**observed,'wheel_count':2},
                    {**observed,'wheels':[['different','1.0']]},
                    {**observed,'wheels':[['example','2.0']]}):
        with pytest.raises(AssertionError):oracle.coverage_record(unsigned,current,wheels,changed)


@pytest.mark.parametrize('fault',['absent','two_versions','wrong_count','unexpected_addition',
    'missing_wheel','duplicate_wheel','changed_version'])
def test_receipt_requires_each_complete_dynamic_runtime_and_wheel_coverage(fault):
    value=receipt()
    record=value['coverage_per_version'][1]
    if fault=='absent':del value['coverage_per_version']
    elif fault=='two_versions':value['coverage_per_version'].pop()
    elif fault=='wrong_count':record['current_runtime_files']+=1
    elif fault=='unexpected_addition':record['added_runtime_files'].append('unplanned')
    elif fault=='missing_wheel':record['wheels']=[]
    elif fault=='duplicate_wheel':record['wheels']*=2;record['wheel_count']=2
    else:record['wheels'][0][1]='2.0'
    with pytest.raises(ValueError,match='coverage incomplete'):
        oracle.validate_receipt(value)


def test_diagnostic_wrapper_calls_original_once_with_exact_values_and_reraises(capsys):
    scope = {}
    exec(oracle.ENTRY_DIAGNOSTICS, scope)
    calls, argument, returned = [], object(), object()
    failure = ValueError('synthetic diagnostic failure')
    def actual(value, *, fail=False):
        calls.append((value, fail))
        if fail: raise failure
        return returned
    module = SimpleNamespace(__name__='synthetic.owner', actual=actual)
    scope['_trace_function'](module, 'actual')
    assert module.actual.__wrapped__ is actual
    assert module.actual(argument) is returned
    with pytest.raises(ValueError) as caught:
        module.actual(argument, fail=True)
    assert caught.value is failure
    assert calls == [(argument, False), (argument, True)]
    captured = capsys.readouterr()
    assert captured.out == ''
    events = [json.loads(line.removeprefix('JAE_ORACLE_STAGE '))
              for line in captured.err.splitlines()]
    assert [event['event'] for event in events] == ['begin', 'return', 'begin', 'raise']
    assert [event['call'] for event in events] == [1, 1, 2, 2]
    assert all(event['stage'] == 'owner.actual' and event['duration_seconds'] >= 0 for event in events)


def test_actual_timeout_keeps_captured_output_and_remains_failure(tmp_path, capsys):
    driver = oracle.Oracle(tmp_path)
    code = ('import sys,time;print("SYNTHETIC PARTIAL",flush=True);'
            'print("JAE_ORACLE_STAGE synthetic pending",file=sys.stderr,flush=True);time.sleep(5)')
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        driver.run([sys.executable, '-I', '-B', '-c', code], timeout=.2)
    assert b'SYNTHETIC PARTIAL' in caught.value.stdout
    record = json.loads(capsys.readouterr().err)
    assert record['oracle_child_diagnostics'] == 'timeout'
    assert 'SYNTHETIC PARTIAL' in record['stdout_tail']
    assert 'synthetic pending' in record['stderr_tail']
    assert record['certification'] == 'NOT_CERTIFIED'


def test_child_diagnostics_are_bounded_and_do_not_turn_bad_exit_into_success(tmp_path, capsys):
    driver = oracle.Oracle(tmp_path)
    driver.child_diagnostics('timeout', b'O' * 5000, b'E' * 40000)
    record = json.loads(capsys.readouterr().err)
    assert record['stdout_tail'] == 'O' * 4096
    assert record['stderr_tail'] == 'E' * 32768
    with pytest.raises(AssertionError, match='oracle child failed'):
        driver.run([sys.executable, '-I', '-B', '-c',
                    'import sys;print("SYNTHETIC FAILURE");sys.exit(1)'])
    record = json.loads(capsys.readouterr().err)
    assert record['oracle_child_diagnostics'] == 'unexpected_exit'
    assert 'SYNTHETIC FAILURE' in record['stdout_tail']


def test_completed_child_retains_stage_diagnostics_without_changing_stdout(tmp_path, capsys):
    driver = oracle.Oracle(tmp_path)
    code = ('import sys;print("SYNTHETIC RESULT");'
            'print("JAE_ORACLE_STAGE synthetic returned",file=sys.stderr)')
    assert driver.run([sys.executable, '-I', '-B', '-c', code]) == 'SYNTHETIC RESULT\n'
    record = json.loads(capsys.readouterr().err)
    assert record['oracle_child_diagnostics'] == 'completed'
    assert record['stdout_tail'] == ''
    assert 'synthetic returned' in record['stderr_tail']


def test_transaction_subbudget_remains_capped_by_same_total_wall(tmp_path, monkeypatch):
    driver = oracle.Oracle(tmp_path)
    monkeypatch.setattr(oracle.time, 'monotonic', lambda: driver.started + oracle.WALL_SECONDS - 37)
    observed = []
    def run(argv, **kwargs):
        observed.append(kwargs['timeout'])
        return SimpleNamespace(returncode=0, stdout='{}', stderr='')
    monkeypatch.setattr(oracle.subprocess, 'run', run)
    assert driver.run(['synthetic'], timeout=600) == '{}'
    assert observed == [37]
    assert oracle.Oracle.run.__kwdefaults__['timeout'] == 180
    assert oracle.Oracle.app_run.__kwdefaults__['timeout'] == 180
    assert oracle.Oracle.cli.__kwdefaults__['timeout'] == 180
    assert oracle.WALL_SECONDS == 1800


def test_only_complete_positive_transactions_receive_six_hundred_second_outer_budget():
    module = ast.parse((ROOT / 'scripts/ci_signed_dmg_acceptance.py').read_text())
    calls = [node for node in ast.walk(module) if isinstance(node, ast.Call)
             and any(keyword.arg == 'timeout' and isinstance(keyword.value, ast.Constant)
                     and keyword.value.value == 600 for keyword in node.keywords)]
    assert len(calls) == 4
    assert sorted(node.func.attr for node in calls) == ['app_run', 'app_run', 'app_run', 'cli']
    for node in calls:
        if node.func.attr == 'cli': assert ast.literal_eval(node.args[0]) == ['restore-app']
        else:
            assert isinstance(node.args[1], ast.Name) and node.args[1].id == 'ENTRY_FIXTURE'
            assert ast.literal_eval(node.args[2]) in {'accept', 'update'}
    assert 'child.communicate(timeout=90)' in oracle.ENTRY_FIXTURE
    assert 'dump_traceback_later(45, repeat=True, file=sys.stderr)' in oracle.ENTRY_FIXTURE
