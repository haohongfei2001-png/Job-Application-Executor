"""Synthetic private native UI: explicit offers, no real applicant transmission."""
import json
import os
from pathlib import Path
import pytest
from playwright.sync_api import expect
from test_dashboard_diagnostics_browser import preparation_ui
from test_preparation_review_browser import open_review,payload,CANARY
from executor.preparation.qiyunfang import CONTRACT_URL


def install(page,observed,*,hold=False,unavailable=False,hold_fill=False,with_resume=False):
    calls=[];pending=[]
    def response(data):
        return {'mode':'PRIVATE_NATIVE_FILL_OFFER','request_id':data['request_id'],
          'task_id':data['task_id'],'task_revision':data['expected_revision'],
          'source_url':CONTRACT_URL,'profile_version':data['profile_version'],
          'resume_version':data['resume_version'],'submit_capability':False,
          'nonce':'n'*40,'scope_sha':'a'*64,'expires_in_seconds':120,
          'plan':[{'field_id':item['field_id'],'value':item['value']} for item in payload()['proposals'] if item['field_id'] in data['selected_ids']]}
    def route(route):
        action=route.request.url.rsplit('/',1)[-1];data=route.request.post_data_json;calls.append((action,data))
        if (action=='open' and hold) or (action=='approve-fill' and hold_fill):pending.append((route,data));return
        if action=='open':value=response(data)
        elif action=='approve-fill':value={'status':'PREPARED_UNVERIFIED','field_count':1,'task_revision':8,'submit_capability':False,'server_draft_verified':False}
        elif action=='review-resume':value={'submit_capability':False,'recipient':'https://www.qiyunfang.com/ajax/advanceUpload.jsp',
          'nonce':'r'*40,'scope_sha':'b'*64,'expires_in_seconds':120,'resume':{'resume_sha256':'b'*64,
          'kind':'resume_docx','byte_count':1000,'destination_filename':'resume.docx'}}
        elif action=='approve-resume':value={'status':'RETURNED_UNVERIFIED','server_attachment_verified':False,'automatic_retry':False,'submit_capability':False}
        elif action=='status':value={'status':'OFFERED','submit_capability':False}
        else:value={'status':'CANCELLATION_REQUESTED','submit_capability':False}
        if unavailable and action=='open':value={'error':'native_preparation_runtime_not_admitted'}
        route.fulfill(status=409 if unavailable and action=='open' else 200,content_type='application/json',body=json.dumps(value))
    page.route('**/ui/api/native-preparation/*',route)
    review=payload()
    if with_resume:review['resume_version']='b'*64
    open_review(page,observed,result=review)
    page.get_by_role('checkbox',name='选择核对姓名',exact=True).check()
    page.locator('#preparation-native-open').click()
    return calls,pending,response


def test_native_fill_is_separate_explicit_consent_and_cleared_on_hide(preparation_ui):
    page,observed=preparation_ui;calls,_,_=install(page,observed)
    expect(page.locator('#preparation-native-consent')).to_be_visible()
    if os.environ.get('JAE_UI_SCREENSHOT_DIR'):
        directory=Path(os.environ['JAE_UI_SCREENSHOT_DIR']);directory.mkdir(parents=True,exist_ok=True)
        page.locator('#preparation-native-consent').scroll_into_view_if_needed()
        page.screenshot(path=str(directory/'preparation-native-consent.png'))
    expect(page.locator('#preparation-fill-approve')).to_be_disabled()
    assert [action for action,_ in calls]==['open']
    assert CANARY not in json.dumps(calls)
    page.locator('#preparation-fill-consent').check()
    page.locator('#preparation-fill-approve').click()
    expect(page.locator('#preparation-native-status')).to_contain_text('已执行本次填写')
    assert len([action for action,_ in calls if action=='approve-fill'])==1
    expect(page.locator('#preparation-fill-approve')).to_be_disabled()
    expect(page.locator('#preparation-resume-review')).to_be_visible()
    page.locator('#preparation-review-hide').click()
    expect(page.locator('#preparation-native-consent')).to_be_hidden()
    assert CANARY not in page.content() and observed['errors']==[] and observed['external']==[]


@pytest.mark.parametrize('interrupt',['hide','close','task_change','pagehide'])
def test_late_native_offer_does_not_restore_private_values(preparation_ui,interrupt):
    page,observed=preparation_ui;calls,pending,response=install(page,observed,hold=True)
    page.wait_for_timeout(50);assert len(pending)==1
    if interrupt=='hide':page.locator('#preparation-review-hide').click()
    elif interrupt=='close':page.locator('#preparation-close').click()
    elif interrupt=='task_change':page.evaluate("openTaskPreparation('prep-b',9)")
    else:page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    route,data=pending[0];route.fulfill(status=200,content_type='application/json',body=json.dumps(response(data)))
    page.wait_for_timeout(50)
    expect(page.locator('#preparation-native-consent')).to_be_hidden()
    assert CANARY not in page.content() and not any(action=='approve-fill' for action,_ in calls)
    assert observed['errors']==[] and observed['external']==[]


def test_unadmitted_runtime_has_actionable_truthful_state(preparation_ui):
    page,observed=preparation_ui;calls,_,_=install(page,observed,unavailable=True)
    expect(page.locator('#preparation-review-status')).to_contain_text('尚未通过完整准入验收')
    assert not any(action=='approve-fill' for action,_ in calls)
    assert CANARY not in page.content() and observed['errors']==[]


@pytest.mark.parametrize('polled_revision',[8,9])
def test_fill_revision_handover_retains_own_revision_and_rejects_unrelated(preparation_ui,polled_revision):
    page,observed=preparation_ui;calls,pending,_=install(page,observed,hold_fill=True)
    page.locator('#preparation-fill-consent').check();page.locator('#preparation-fill-approve').click()
    page.wait_for_timeout(50);assert len(pending)==1
    page.evaluate("revision => reconcilePreparationTasks([{task_id:'prep-a',revision}])",polled_revision)
    expect(page.locator('#preparation-native-consent')).to_be_visible()
    route,_=pending[0];route.fulfill(status=200,content_type='application/json',body=json.dumps({
      'status':'PREPARED_UNVERIFIED','field_count':1,'task_revision':8,'server_draft_verified':False,'submit_capability':False}))
    if polled_revision==8:
        expect(page.locator('#preparation-resume-review')).to_be_visible()
        page.evaluate("reconcilePreparationTasks([{task_id:'prep-a',revision:7}])")
        expect(page.locator('#preparation-native-consent')).to_be_visible()
        page.evaluate("reconcilePreparationTasks([{task_id:'prep-a',revision:9}])")
    expect(page.locator('#preparation-native-consent')).to_be_hidden()
    assert observed['errors']==[] and observed['external']==[]


def test_resume_upload_requires_its_own_explicit_confirmation(preparation_ui):
    page,observed=preparation_ui;calls,_,_=install(page,observed,with_resume=True)
    page.locator('#preparation-fill-consent').check();page.locator('#preparation-fill-approve').click()
    page.locator('#preparation-resume-review').click()
    expect(page.locator('#preparation-resume-consent')).to_be_visible()
    expect(page.locator('#preparation-upload-approve')).to_be_disabled()
    assert not any(action=='approve-resume' for action,_ in calls)
    page.locator('#preparation-upload-consent').check();page.locator('#preparation-upload-approve').click()
    expect(page.locator('#preparation-native-status')).to_contain_text('网站是否实际保留附件尚未验证')
    expect(page.locator('#preparation-upload-approve')).to_be_disabled()
    assert len([action for action,_ in calls if action=='approve-resume'])==1
    assert observed['errors']==[] and observed['external']==[]
