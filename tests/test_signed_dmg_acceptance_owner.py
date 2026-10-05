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
        'signed_versions':3,'readonly_images':3,'macho_objects_per_version':[32,32,32],
        'elapsed_seconds':3899,'phases':{'complete':3899},
        'coverage_per_version':[{'unsigned_runtime_files':30,'current_runtime_files':32,
            'added_runtime_files':['release-runtime-manifest.json','signing-input-bridge.json'],
            'wheel_count':1,'wheels':[['example-package','1.0']]} for _ in range(3)]}


@pytest.mark.parametrize('fault',['missing_case','duplicate_case','no_macos','too_long','no_time',
    'no_complete','missing_version','missing_image','shrunk_runtime','different_catalog','certified'])
def test_acceptance_receipt_fails_closed_on_missing_or_skipped_evidence(fault):
    value=receipt()
    if fault=='missing_case':value['checked'].pop()
    elif fault=='duplicate_case':value['checked'].append(value['checked'][0])
    elif fault=='no_macos':value['status']='SKIPPED'
    elif fault=='too_long':value['elapsed_seconds']=3900
    elif fault=='no_time':value['elapsed_seconds']=0
    elif fault=='no_complete':value['phases']={}
    elif fault=='missing_version':value['signed_versions']=2
    elif fault=='missing_image':value['readonly_images']=2
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
    assert '    timeout-minutes: 75\n' in job
    assert '    runs-on: macos-latest\n' in job
    assert 'upload-artifact' not in job and 'continue-on-error' not in job
    assert 'python -I -B scripts/ci_signed_dmg_acceptance.py' in job
    assert 'Refuse stale or ineligible engineering closure head' in job
    aggregate=workflow.split('\n  test:\n',1)[1].split('\n  engineering_closure_macos:\n',1)[0]
    assert 'needs: [full_suite, signed_dmg_acceptance, macos_consumer_gate]' in aggregate
    assert 'needs.signed_dmg_acceptance.result' in aggregate
    assert 'needs.signed_dmg_acceptance.outputs.source_sha' in aggregate
    assert "os.environ['JAE_SIGNED_DMG_RESULT'] != 'success'" in aggregate
    assert "os.environ['JAE_SIGNED_DMG_SHA'] != expected" in aggregate
    assert oracle.WALL_SECONDS==3900
    script=(ROOT/'scripts/ci_signed_dmg_acceptance.py').read_text()
    assert 'process.wait(timeout=WALL_SECONDS)' in script
    assert 'terminate_tree(process.pid)' in script
    assert 'WIP, not acceptance' in script
    assert 'pytest.skip' not in script
    for name in ('ENTRY_DIAGNOSTICS','SERVICE_DIAGNOSTICS','REOPEN_DIAGNOSTICS','ENTRY_FIXTURE','ROLLBACK_FIXTURE','READERS',
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
    assert all(event['pid'] == os.getpid() for event in events)
    assert events[-1]['error_type'] == 'ValueError'


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
    assert driver.run(['synthetic'], timeout=900) == '{}'
    assert observed == [37]
    assert oracle.Oracle.run.__kwdefaults__['timeout'] == 180
    assert oracle.Oracle.app_run.__kwdefaults__['timeout'] == 180
    assert oracle.Oracle.cli.__kwdefaults__['timeout'] == 180
    assert oracle.WALL_SECONDS == 3900


def test_only_complete_positive_transactions_receive_nine_hundred_second_outer_budget():
    module = ast.parse((ROOT / 'scripts/ci_signed_dmg_acceptance.py').read_text())
    calls = [node for node in ast.walk(module) if isinstance(node, ast.Call)
             and any(keyword.arg == 'timeout' and isinstance(keyword.value, ast.Constant)
                     and keyword.value.value == 900 for keyword in node.keywords)]
    assert len(calls) == 4
    assert sorted(node.func.attr for node in calls) == ['app_run', 'app_run', 'app_run', 'cli']
    for node in calls:
        if node.func.attr == 'cli': assert ast.literal_eval(node.args[0]) == ['restore-app']
        else:
            assert isinstance(node.args[1], ast.Name) and node.args[1].id == 'ENTRY_FIXTURE'
            assert ast.literal_eval(node.args[2]) in {'accept', 'update'}
    assert 'child.communicate(timeout=600)' in oracle.ENTRY_FIXTURE
    assert 'faulthandler' not in oracle.ENTRY_FIXTURE + oracle.ROLLBACK_FIXTURE


def test_native_timeout_diagnostics_are_bounded_and_preserve_poll(capsys):
    scope = {}
    exec(oracle.ENTRY_DIAGNOSTICS, scope)
    calls = []
    child = SimpleNamespace(poll=lambda: calls.append('poll') or None)
    error = subprocess.TimeoutExpired(['synthetic'], 90, output=b'O' * 5000, stderr=b'E' * 20000)
    scope['_trace_native_timeout'](child, error)
    assert calls == ['poll']
    record = json.loads(capsys.readouterr().err.removeprefix('JAE_ORACLE_NATIVE_TIMEOUT '))
    assert record == {'returncode':None, 'timeout_seconds':90,
                      'stdout_tail':'O' * 4096, 'stderr_tail':'E' * 16384}


def test_actual_nested_timeout_is_reported_without_blocking_diagnostic_cleanup(tmp_path):
    code = oracle.ENTRY_DIAGNOSTICS + r'''
import subprocess
child = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(5)'],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    child.communicate(timeout=.2)
except subprocess.TimeoutExpired as error:
    _trace_native_timeout(child, error)
    raise
finally:
    child.kill()
    child.communicate(timeout=.5)
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code],
                            capture_output=True, text=True, timeout=2)
    assert result.returncode == 1
    assert 'JAE_ORACLE_NATIVE_TIMEOUT ' in result.stderr
    assert 'TimeoutExpired' in result.stderr


def test_reopen_prefix_executes_original_program_and_read_pipe_budget_unchanged(tmp_path):
    source = tmp_path / 'sealed-source'
    package = source / 'executor/autonomy'
    package.mkdir(parents=True)
    (source / 'executor/__init__.py').write_text('')
    (package / '__init__.py').write_text('')
    (package / 'consumer.py').write_text('def _bundle_transaction_identity(*args, **kwargs): return args,kwargs\n')
    (package / 'first_install.py').write_text('def launch_native_entry(*args, **kwargs): return args,kwargs\n')
    (package / 'first_use_recovery.py').write_text(
        'def prepare_recovery(*args, **kwargs): return args,kwargs\n'
        'def start_first_use(*args, **kwargs): return args,kwargs\n'
        'def _read_pipe(fd, *, seconds=15): return {"fd":fd,"seconds":seconds}\n')
    original = ('import json,sys;from executor.autonomy.first_use_recovery import _read_pipe;'
                'print(json.dumps({"argv":sys.argv[1:],"pipe":_read_pipe(17)}))')
    result = subprocess.run([sys.executable, '-I', '-B', '-c',
        oracle.REOPEN_DIAGNOSTICS + '\n' + original, str(source), '--port', '12345', 'native-entry'],
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'argv':[str(source),'--port','12345','native-entry'],
                                         'pipe':{'fd':17,'seconds':15}}
    events = [json.loads(line.removeprefix('JAE_ORACLE_STAGE ')) for line in result.stderr.splitlines()]
    assert [event['stage'] for event in events] == ['first_use_recovery._read_pipe'] * 2
    assert [event['event'] for event in events] == ['begin','return']


@pytest.mark.parametrize('count', [None, 0, 1, 2, 3.0, True])
def test_receipt_cannot_accept_incomplete_or_unverified_image_count(count):
    value = receipt()
    if count is None: value.pop('readonly_images')
    else: value['readonly_images'] = count
    with pytest.raises(ValueError, match='receipt incomplete'):
        oracle.validate_receipt(value)


def test_all_three_real_images_are_built_at_their_use_points_before_mounting():
    source = (ROOT / 'scripts/ci_signed_dmg_acceptance.py').read_text()
    module = ast.parse(source)
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'Oracle')
    journey = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'journey')
    calls = [node for node in ast.walk(journey) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'build_image']
    calls.sort(key=lambda node:node.lineno)
    assert [ast.literal_eval(node.args[1]) for node in calls] == [1,2,3]
    assert [ast.literal_eval(node.args[0].slice) for node in calls] == [0,1,2]
    text = ast.get_source_segment(source, journey)
    assert text.index('self.build_image(versions[0], 1)') < text.index('self.hide_build_roots(source_roots)')
    for number, label in enumerate(('first','second','third'), 1):
        assert text.index(f'self.build_image(versions[{number-1}], {number})') < text.index(f"self.mounted(images[{number-1}], '{label}')")
    assert text.index('build_version(2)') > text.index("self.checked.add('native_reopen_health')")
    assert text.index('build_version(3)') > text.index("self.checked.add('explicit_signed_rollback_health')")
    assert text.count('self.hide_build_roots(later_roots)') == 2
    assert text.index('return images') > text.index("self.checked.update({'real_failed_activation_restoration'")
    worker = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == 'worker')
    worker_text = ast.get_source_segment(source, worker)
    assert 'number == len(versions) + 1 and number in (1,2,3)' in worker_text
    assert 'first_roots = build_version(1)' in worker_text
    assert 'oracle.sign_version(' in worker_text and 'oracle.negatives(' in worker_text
    assert worker_text.index('images = oracle.journey(') < worker_text.index('assert len(versions) == len(catalogs) == len(images) == 3')
    assert worker_text.index('assert len(versions) == len(catalogs) == len(images) == 3') < worker_text.index("oracle.checked.add('three_complete_signed_versions')")
    assert "copy_unmanifested_source(root / 'source-1-unavailable',current_source)" in worker_text
    assert 'build_installer_image(' not in worker_text
    assert "'readonly_images':len(images)" in worker_text
    builder = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'build_image')
    assert 'build_installer_image(output=image_root,signed_app=signed,required_publisher_policy=POLICY)' in ast.get_source_segment(source, builder)


@pytest.mark.parametrize('mode', ['success', 'failure'])
def test_service_observer_runs_original_child_and_records_real_exit_and_ack(tmp_path, mode):
    source = tmp_path / 'app/Contents/Resources/release'
    package = source / 'executor/autonomy'
    package.mkdir(parents=True)
    binary = source.parent / 'runtime/bin/python'
    binary.parent.mkdir(parents=True)
    binary.symlink_to(sys.executable)
    (source / 'executor/__init__.py').write_text('')
    (package / '__init__.py').write_text('')
    (package / 'consumer.py').write_text('def _bundle_transaction_identity(*args, **kwargs): return "EXACT ORIGINAL IDENTITY"\n')
    (package / 'first_install.py').write_text('def launch_native_entry(*args, **kwargs): return args,kwargs\n')
    service_program = ('import sys;from executor.autonomy.first_use_recovery import serve_first_use;'
                       'serve_first_use(int(sys.argv[3]),sys.argv[4])')
    recovery = r'''
import json,os,subprocess,sys
from pathlib import Path
def prepare_recovery(*args, **kwargs): return args,kwargs
def _read_pipe(fd, *, seconds=15):
    assert seconds==15
    try: return json.loads(os.read(fd,4096))
    finally: os.close(fd)
def _write_pipe(fd,value):
    os.write(fd,json.dumps(value).encode());os.close(fd)
class _FirstUseStartup:
    def __enter__(self): return self
    def verify(self, *, empty):
        from .consumer import _bundle_transaction_identity
        return _bundle_transaction_identity()
    def bound_lock_fd(self,*args): return args
    def before_queue(self,*args): return args
    def after_queue(self,*args): return args
    def acknowledge(self,fd):
        _write_pipe(fd,{'argv':sys.argv[1:],'identity':self.verify(empty=True)})
    def __exit__(self,*args): return None
def serve_first_use(fd, mode):
    with _FirstUseStartup() as startup:
        if mode=='failure': raise RuntimeError('SYNTHETIC ACTUAL SERVICE FAILURE')
        startup.acknowledge(fd)
        print('SERVICE STDOUT MUST REMAIN DEVNULL',flush=True)
def start_first_use(source, mode):
    source=Path(source);read_fd,write_fd=os.pipe()
    try:
        child=subprocess.Popen([str(source.parent/'runtime/bin/python'),'-I','-B','-c',
            SERVICE_PROGRAM,str(source),'first-use-serve',str(write_fd),mode],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            close_fds=True,pass_fds=(write_fd,))
    finally: os.close(write_fd)
    child.wait(timeout=2)
    return _read_pipe(read_fd)
'''
    (package / 'first_use_recovery.py').write_text('SERVICE_PROGRAM = '+repr(service_program)+'\n'+recovery)
    diagnostic = tmp_path / 'service.log'
    original = ('import json,sys;from executor.autonomy.first_use_recovery import start_first_use;'
                'print(json.dumps(start_first_use(sys.argv[1],sys.argv[2])))')
    program = '_oracle_service_log_path = '+repr(str(diagnostic))+'\n'+oracle.REOPEN_DIAGNOSTICS+'\n'+original
    result = subprocess.run([sys.executable,'-I','-B','-c',program,str(source),mode],
                            capture_output=True,text=True,timeout=5)
    assert diagnostic.is_file() and diagnostic.stat().st_mode & 0o777 == 0o600
    assert 'SERVICE STDOUT MUST REMAIN DEVNULL' not in result.stdout
    status = [json.loads(line.removeprefix('JAE_ORACLE_SERVICE_STATUS '))
              for line in result.stderr.splitlines() if line.startswith('JAE_ORACLE_SERVICE_STATUS ')]
    assert len(status)==1 and status[0]['pid'] > 0
    logged = diagnostic.read_text()
    events = [json.loads(line.removeprefix('JAE_ORACLE_STAGE '))
              for line in logged.splitlines() if line.startswith('JAE_ORACLE_STAGE ')]
    assert events and all(event['pid']==status[0]['pid'] for event in events)
    if mode=='success':
        assert result.returncode==0, result.stderr
        observed=json.loads(result.stdout)
        assert observed['argv'][0:2]==[str(source),'first-use-serve']
        assert observed['argv'][3]=='success'
        assert observed['identity']=='EXACT ORIGINAL IDENTITY'
        assert status[0]['returncode']==0
        assert any(e['stage']=='_FirstUseStartup.acknowledge' and e['event']=='return' for e in events)
    else:
        assert result.returncode!=0 and status[0]['returncode']==1
        assert 'SYNTHETIC ACTUAL SERVICE FAILURE' in logged
        assert any(e['stage']=='first_use_recovery.serve_first_use' and e['event']=='raise' for e in events)


def test_service_trace_does_not_change_production_deadlines_or_result_checks():
    recovery=(ROOT/'executor/autonomy/first_use_recovery.py').read_text()
    assert 'def _read_pipe(fd, *, seconds=15):' in recovery
    assert 'child.communicate(timeout=600)' in oracle.ENTRY_FIXTURE
    assert oracle.WALL_SECONDS==3900
    assert "assert child.returncode == 0" in oracle.ENTRY_FIXTURE
    assert "'stderr':stream" in oracle.REOPEN_DIAGNOSTICS
    assert 'observed_args[4] = _service_diagnostics' in oracle.REOPEN_DIAGNOSTICS


def test_future_sources_use_the_same_hidden_template_without_restoring_build_paths(tmp_path):
    driver=oracle.Oracle(tmp_path)
    source=tmp_path/'source-1';oracle.copy_unmanifested_source(ROOT,source)
    unsigned=tmp_path/'unsigned-1';unsigned.mkdir()
    marker=unsigned/'unchanged-input';marker.write_bytes(b'SYNTHETIC COMPLETE INPUT')
    before=(source/'executor/autonomy/first_use_recovery.py').read_bytes()
    driver.hide_build_roots([source,unsigned])
    assert not source.exists() and not unsigned.exists()
    next_source=tmp_path/'source-2'
    oracle.copy_unmanifested_source(tmp_path/'source-1-unavailable',next_source)
    assert (next_source/'executor/autonomy/first_use_recovery.py').read_bytes()==before
    assert (tmp_path/'unsigned-1-unavailable/unchanged-input').read_bytes()==b'SYNTHETIC COMPLETE INPUT'
    assert not source.exists() and not unsigned.exists()


@pytest.mark.parametrize('outcome', ['ack', 'raise'])
def test_failed_observation_sees_real_child_terminal_trace_without_changing_failure(tmp_path,capsys,outcome):
    import time
    path=tmp_path/'first-use-service-diagnostic.log'
    path.touch(mode=0o600)
    event={'stage':'_FirstUseStartup.acknowledge' if outcome=='ack' else 'first_use_recovery.serve_first_use',
           'event':'return' if outcome=='ack' else 'raise'}
    code=('import pathlib,sys,time;time.sleep(.05);'
          'p=pathlib.Path(sys.argv[1]);p.write_text(sys.argv[2])')
    child=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(path),
                            'JAE_ORACLE_STAGE '+json.dumps(event)+'\n'])
    try:
        oracle.observe_failed_service(tmp_path,time.monotonic()+2)
        child.wait(timeout=2)
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=2)
    record=json.loads(capsys.readouterr().err)
    assert record['oracle_failed_service_observation']==('observed_ack_completion' if outcome=='ack' else 'observed_service_raise')
    assert record['acceptance']=='FAILED_UNCHANGED'
    assert record['certification']=='NOT_CERTIFIED'
    assert not (tmp_path/'receipt.json').exists()


def test_failed_observation_never_extends_total_wall_or_replays_actions(tmp_path,monkeypatch,capsys):
    ticks=[100.0]
    monkeypatch.setattr(oracle.time,'monotonic',lambda:ticks[0])
    monkeypatch.setattr(oracle.time,'sleep',lambda seconds:ticks.__setitem__(0,ticks[0]+seconds))
    path=tmp_path/'first-use-service-diagnostic.log';path.touch(mode=0o600)
    monkeypatch.setattr(oracle.os,'kill',lambda *a:pytest.fail('observer signalled a process'))
    monkeypatch.setattr(oracle.subprocess,'Popen',lambda *a,**k:pytest.fail('observer replayed a process'))
    oracle.observe_failed_service(tmp_path,100.25)
    assert ticks[0]<=100.25
    record=json.loads(capsys.readouterr().err)
    assert record['acceptance']=='FAILED_UNCHANGED' and record['elapsed_seconds']==.25
    ticks[0]=100.0
    oracle.observe_failed_service(tmp_path,1000)
    assert ticks[0]<=280.0
    record=json.loads(capsys.readouterr().err)
    assert record['elapsed_seconds']==180.0


@pytest.mark.parametrize('fault',['symlink','hardlink','public','fifo'])
def test_service_diagnostic_reader_refuses_nonprivate_or_aliased_files(tmp_path,fault):
    path=tmp_path/'first-use-service-diagnostic.log'
    other=tmp_path/'retained';other.write_text('SYNTHETIC PRIVATE');other.chmod(0o600)
    if fault=='symlink':path.symlink_to(other)
    elif fault=='hardlink':os.link(other,path)
    elif fault=='fifo':os.mkfifo(path,0o600)
    else:path.write_text('SYNTHETIC');path.chmod(0o644)
    assert oracle.service_diagnostic_tail(tmp_path) is None
    assert other.read_text()=='SYNTHETIC PRIVATE'


def test_failed_observation_is_failure_only_and_precedes_existing_cleanup():
    source=(ROOT/'scripts/ci_signed_dmg_acceptance.py').read_text()
    main=source.split('def main(argv=None):',1)[1]
    assert 'if code not in (None, 0):' in main
    assert 'observe_failed_service(root, parent_started + WALL_SECONDS)' in main
    assert main.index('observe_failed_service(')<main.index('cleanup_test_processes(root)')
    assert main.index('if code != 0:')<main.index('result = validate_receipt(')
    assert 'raise RuntimeError(\'signed DMG acceptance child failed; WIP, not acceptance\')' in main
    assert oracle.FAILURE_OBSERVATION_SECONDS==180 and oracle.WALL_SECONDS==3900
