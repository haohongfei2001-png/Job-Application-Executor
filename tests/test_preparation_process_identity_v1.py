"""Identity proofs are read-only, double-fenced and value-free."""
import copy
import os
from types import SimpleNamespace

import pytest

from executor.preparation import process_identity as p


def record():
    return {key:{'pid':pid,'ppid':234 if key=='browser' else os.getpid(),'uid':os.geteuid(),'start_sha':str(pid)[0]*64,'command_sha':'c'*64,'zombie':False}
            for key,pid in [('browser',123),('driver',234)]}


def test_capture_and_verify_require_consistent_native_and_os_identity(monkeypatch):
    values=record();mapping={value['pid']:value for value in values.values()}
    monkeypatch.setattr(p,'_snapshot',lambda pid:copy.deepcopy(mapping.get(pid)))
    monkeypatch.setattr(p,'_browser_pid',lambda _:123)
    pw=SimpleNamespace(_impl_obj=SimpleNamespace(_connection=SimpleNamespace(_transport=SimpleNamespace(_proc=SimpleNamespace(pid=234)))))
    owned=p.OwnedProcessIdentity.capture(pw,object())
    assert owned.verify(object())==owned.process_sha
    assert owned.absence()['status']=='PRESENT'
    mapping.clear();assert owned.absence()['status']=='ABSENT'


@pytest.mark.parametrize('drift',['command','same_pid_missing_driver','new_epoch','permission'])
def test_absence_does_not_infer_death_from_command_drift_or_unreadable_process(monkeypatch,drift):
    values=record();owned=p.OwnedProcessIdentity(copy.deepcopy(values));mapping={value['pid']:value for value in values.values()}
    if drift=='command':mapping[123]['command_sha']='d'*64
    elif drift=='same_pid_missing_driver':mapping.pop(234)
    elif drift=='new_epoch':
        for value in mapping.values():value['start_sha']='f'*64
    def observe(pid):
        if drift=='permission':raise p.ProcessIdentityUnknown()
        return copy.deepcopy(mapping.get(pid))
    monkeypatch.setattr(p,'_snapshot',observe)
    expected={'command':'UNKNOWN','same_pid_missing_driver':'PRESENT','new_epoch':'UNKNOWN','permission':'UNKNOWN'}[drift]
    assert owned.absence()['status']==expected


def test_os_snapshot_never_returns_raw_command_or_start_time(monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs));return SimpleNamespace(returncode=0,stderr='',stdout=f'{os.geteuid()} 99 Thu Oct 1 11:11:11 2026 Sl /synthetic/Chrome --private-path=SYNTHETIC_PATH')
    monkeypatch.setattr(p.subprocess,'run',run)
    value=p._snapshot(123)
    assert set(value)=={'pid','ppid','uid','start_sha','command_sha','zombie'}
    assert 'SYNTHETIC_PATH' not in repr(value)
    assert calls[0][0][3]=='123' and calls[0][1]['env']['TZ']=='UTC'


def test_missing_vs_ambiguous_ps_failures(monkeypatch):
    monkeypatch.setattr(p.subprocess,'run',lambda *_,**__:SimpleNamespace(returncode=1,stdout='',stderr=''))
    assert p._snapshot(123) is None
    monkeypatch.setattr(p.subprocess,'run',lambda *_,**__:SimpleNamespace(returncode=1,stdout='',stderr='permission denied'))
    with pytest.raises(p.ProcessIdentityUnknown):p._snapshot(123)



def test_receipt_is_immutable_private_and_bound_to_original_hash(tmp_path):
    import json,stat
    root=tmp_path.resolve();owned=p.OwnedProcessIdentity(record());sha=owned.save(root)
    receipt=root/('preparation-process-'+sha+'.json')
    assert stat.S_IMODE(receipt.stat().st_mode)==0o600
    assert owned.save(root)==sha and p.OwnedProcessIdentity.load(root,sha).record==owned.record
    data=json.loads(receipt.read_text());data['identity']['browser']['pid']=345
    receipt.write_text(json.dumps(data))
    with pytest.raises(p.ProcessIdentityUnknown):p.OwnedProcessIdentity.load(root,sha)
    with pytest.raises(p.ProcessIdentityUnknown):owned.save(root)


def test_receipt_alias_cannot_be_read_or_overwritten(tmp_path):
    root=tmp_path.resolve();owned=p.OwnedProcessIdentity(record());sha=owned.process_sha
    outside=root/'outside';outside.write_text('outside canary');outside.chmod(0o600)
    (root/('preparation-process-'+sha+'.json')).symlink_to(outside)
    with pytest.raises(p.ProcessIdentityUnknown):owned.save(root)
    assert outside.read_text()=='outside canary'


def test_snapshot_pins_locale_without_mutating_inherited_environment(monkeypatch):
    monkeypatch.setenv('LC_TIME','fr_FR.UTF-8');monkeypatch.setenv('LANG','de_DE.UTF-8')
    calls=[]
    def run(*args,**kwargs):
        calls.append(kwargs['env']);return SimpleNamespace(returncode=1,stdout='',stderr='')
    monkeypatch.setattr(p.subprocess,'run',run);p._snapshot(123)
    assert calls[0]['LC_ALL']=='C' and calls[0]['TZ']=='UTC'
    assert os.environ['LC_TIME']=='fr_FR.UTF-8' and os.environ['LANG']=='de_DE.UTF-8'



def test_capture_rejects_same_user_browser_outside_own_driver_tree(monkeypatch):
    values=record();values['browser']['ppid']=999
    monkeypatch.setattr(p,'_snapshot',lambda pid:copy.deepcopy(values['browser'] if pid==123 else values['driver']))
    monkeypatch.setattr(p,'_browser_pid',lambda _:123)
    pw=SimpleNamespace(_impl_obj=SimpleNamespace(_connection=SimpleNamespace(_transport=SimpleNamespace(_proc=SimpleNamespace(pid=234)))))
    with pytest.raises(p.ProcessIdentityUnknown):p.OwnedProcessIdentity.capture(pw,object())


def test_os_only_callback_guard_never_issues_browser_rpc(monkeypatch):
    value=record();owned=p.OwnedProcessIdentity(copy.deepcopy(value));mapping={part['pid']:part for part in value.values()}
    monkeypatch.setattr(p,'_browser_pid',lambda _:pytest.fail('browser RPC during synchronous callback'))
    monkeypatch.setattr(p,'_snapshot',lambda pid:copy.deepcopy(mapping.get(pid)))
    assert owned.verify_os()==owned.process_sha
    mapping.pop(234)
    with pytest.raises(p.ProcessIdentityUnknown):owned.verify_os()
