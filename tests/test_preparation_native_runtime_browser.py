"""Installed, headed Mac Chrome evidence using synthetic local recipients only.

These tests do not edit CERTIFIED_NATIVE_RUNTIMES or activate real applicants.
They keep the original browser-independent suite and add the production-shaped
launch target to the selected exact-file/one-shot cases.
"""
import sys
import json
import platform
import subprocess
import importlib.metadata
from pathlib import Path
import pytest
import test_preparation_resume_browser as original
from executor.preparation.session import DisposablePreparationSession
from test_qiyunfang_preparation_browser import bounded_browser_oracle

pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='Headed installed Chrome Mac target; Linux evidence is separate')


@pytest.fixture
def native_target(monkeypatch):
    assert Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').is_file(), 'required native Chrome missing'
    version=subprocess.check_output(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--version'],text=True).strip()
    print('NATIVE_SYNTHETIC_TARGET '+json.dumps({'os':sys.platform,'architecture':platform.machine(),
        'os_version':platform.mac_ver()[0],'playwright':importlib.metadata.version('playwright'),'chrome':version,
        'headless':False,'channel':'chrome','live_admission':False}),flush=True)
    def owner(**unused):
        return DisposablePreparationSession(headless=False,channel='chrome')
    monkeypatch.setattr(original,'DisposablePreparationSession',owner)


@pytest.mark.parametrize('kind',['resume_pdf','resume_docx'])
@pytest.mark.parametrize('fault',['none','root_before_approve'])
def test_headed_installed_chrome_exact_file_and_changed_document_refusal(tmp_path,native_target,kind,fault):
    original.test_owned_resume_stage_is_exact_private_nonreplayable_and_synchronous_xhr_safe(tmp_path,kind,fault)


def test_headed_installed_chrome_two_mib_controller_and_single_upload(tmp_path,native_target):
    original.test_private_controller_preserves_two_mib_docx_and_one_upload_authority(tmp_path)


def test_current_owned_native_browser_proves_its_own_capabilities_without_private_material():
    from executor.preparation.native_admission import NativePreparationAdmission
    assert NativePreparationAdmission.available()
    with DisposablePreparationSession(headless=False,channel='chrome') as owner:
        original=owner.context
        from executor.preparation.native_selfcheck import check_owned_native_browser
        try:receipt=check_owned_native_browser(owner)
        except Exception as error:
            print('NATIVE_SELF_CHECK_DIAGNOSTIC '+json.dumps(getattr(error,'diagnostic',{}),sort_keys=True),flush=True)
            raise
        assert receipt['protocols']==['http','https','ws','wss']
        admission=NativePreparationAdmission()
        assert admission.admit(owner) is True
        assert admission._receipt['temporary_context_closed'] is True
        assert admission._receipt['external_recipient_used'] is False
        assert admission._receipt['applicant_data_used'] is False
        assert owner.browser.contexts==[original] and original.pages==[]
        receipt=admission._receipt
        assert admission.admit(owner) is True and admission._receipt is receipt
        assert admission._receipt['browser_version']==owner.browser.version
