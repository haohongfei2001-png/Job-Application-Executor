#!/usr/bin/env python3
"""Explicit empty-public-form compatibility diagnostic, never a live grant.

No applicant file, user profile, upload, CAPTCHA or final request is opened.
The observer stays unregistered; this script owns a fresh deny-proxy session.
Only fixed status/stage strings and booleans can leave this probe.
"""
from __future__ import annotations

import argparse
import faulthandler
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from executor.preparation.change_epoch import PreDocumentChangeEpoch
from executor.preparation.preflight import open_public_form
from executor.preparation.qiyunfang import CONTRACT_URL
from executor.preparation.session import DisposablePreparationSession


def probe():
    result={
        'format':'jae-public-change-epoch-compatibility-v1',
        'source':CONTRACT_URL,
        'status':'PUBLIC_EPOCH_UNVERIFIED',
        'stage':'FRESH_SESSION',
        'observer_installed':False,
        'public_form_matched':False,
        'network_sealed':False,
        'epoch_sealed':False,
        'unchanged_after_settle':False,
        'closure_proven':False,
        'applicant_data_entered':False,
        'live_enabled':False,
        'final_action_enabled':False,
    }
    channel='chrome' if sys.platform=='linux' else None
    result['browser_target']='installed_chrome_headless_linux' if channel else 'bundled_headless_mac'
    try:
        with DisposablePreparationSession(headless=True,channel=channel) as owner:
            try:
                result['stage']='PRE_DOCUMENT_INSTALL'
                observer=PreDocumentChangeEpoch(owner.context)
                result['observer_installed']=True
                result['stage']='OPEN_EMPTY_PUBLIC_FORM'
                preflight=open_public_form(owner)
                result['public_form_matched']=True
                result['stage']='SEAL_DENY_TRANSPORT'
                owner.transport.seal();owner.transport.require_sealed()
                result['network_sealed']=True
                result['stage']='SEAL_CHANGE_EPOCH'
                observer.seal()
                result['epoch_sealed']=True
                result['stage']='OBSERVE_EMPTY_FORM_STABILITY'
                preflight.page.wait_for_timeout(2000)
                observer.unchanged()
                owner.transport.require_sealed()
                owner.identity.verify(owner.browser)
                result['unchanged_after_settle']=True
                result['status']='EMPTY_PUBLIC_FORM_COMPATIBLE'
                result['stage']='CLOSE_OWNED_SESSION'
            except Exception:
                result['status']='EMPTY_PUBLIC_FORM_UNSUPPORTED'
        result['closure_proven']=True
    except Exception:
        # Never print the exception, raw URL, request body, DOM or source bytes.
        result['status']='PUBLIC_EPOCH_UNAVAILABLE'
    return result


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    faulthandler.dump_traceback_later(90,exit=True)
    try:
        result=probe()
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':result['status'],'stage':result['stage'],
                          'live_enabled':False,'final_action_enabled':False}))
    finally:faulthandler.cancel_dump_traceback_later()
    return 0


if __name__=='__main__':raise SystemExit(main())
