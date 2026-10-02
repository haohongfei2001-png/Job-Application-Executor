"""Actual native child owner, synthetic private source and intercepted fixture.

Only this test bootstrap substitutes a synthetic page factory. Native process
admission, authority, per-primitive IPC checks and browser closure remain real.
"""
import json
from pathlib import Path
import sys
import subprocess

import pytest
from executor.preparation import private_child as m
from executor.preparation.authority import PreparationConflict
from test_preparation_authority_browser import setup,SESSION

pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='Exact installed headed Chrome native child requires hosted Mac')

FACTORY=r'''
from executor.autonomy.queue import TaskQueue
from executor.preparation.controller import PreparationController
from executor.preparation.flow import PreparationFlow
from executor.preparation.session import DisposablePreparationSession
from executor.preparation.native_admission import NativePreparationAdmission
from executor.preparation.qiyunfang import CONTRACT_URL,digest
from test_qiyunfang_preparation_browser import replica

def make(root,valid):
 queue=TaskQueue(root)
 admission=NativePreparationAdmission(still_authorized=lambda:controller._alive())
 def flow(authority,owner,**kwargs):
  owner.context.route(CONTRACT_URL,lambda route:route.fulfill(status=200,content_type='text/html',body=replica()))
  page=owner.context.new_page();page.goto(CONTRACT_URL)
  return PreparationFlow(authority,owner,page,resources_sha=digest('SYNTHETIC_PRIVATE_CHILD'),**kwargs)
 controller=PreparationController(queue,valid,flow_factory=flow,
  owner_factory=lambda:DisposablePreparationSession(headless=False,channel='chrome'),
  write_admission=admission.admit,upload_admission=admission.admit)
 return controller
'''

@pytest.mark.parametrize('revoke_before_fill',[False,True])
def test_real_native_child_exact_fill_or_revocation_and_closed_owner(tmp_path,monkeypatch,capfd,revoke_before_fill):
    q,task,_,_=setup(tmp_path)
    harness=tmp_path/'native_child_fixture.py';harness.write_text(FACTORY)
    real=subprocess.Popen;tests=Path(__file__).parent.resolve();launches=[]
    def launch(args,**options):
        launches.append(tuple(args));args=list(args)
        args[4]=('import sys;sys.path.insert(0,sys.argv[1]);sys.path.insert(0,'+repr(str(tests))+');'
                 'sys.path.insert(0,'+repr(str(tmp_path))+');'
                 'from native_child_fixture import make;'
                 'from executor.preparation.private_child import worker_main;'
                 'worker_main([int(x) for x in sys.argv[2:]],controller_factory=make)')
        return real(args,**options)
    monkeypatch.setattr(m.subprocess,'Popen',launch)
    allowed=[True];checks=[]
    owner=m.PrivatePreparationChild(q,lambda session:checks.append(session) is None and session==SESSION and allowed[0])
    try:
        offer=owner.open(task['task_id'],task['revision'],SESSION,['0','8'])
        assert offer['live_write_available'] is True
        before=len(checks)
        if revoke_before_fill:
            allowed[0]=False
            with pytest.raises(PreparationConflict):owner.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
            assert q.field_actions(task['task_id'])==[]
        else:
            result=owner.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)
            assert result['status']=='PREPARED_UNVERIFIED' and result['submit_capability'] is False
            assert len(q.field_actions(task['task_id']))==2
        assert len(checks)>before and len(launches)==1
        assert owner.shutdown(timeout=20)
        assert owner.status()['status']=='CLOSED' and not q.preparation_in_flight()
        assert owner._process.returncode==0
        output=''.join(capfd.readouterr())
        assert 'PRIVATE_AUTHORITY_CANARY' not in output and SESSION not in output
    finally:
        assert owner.shutdown(timeout=20)
