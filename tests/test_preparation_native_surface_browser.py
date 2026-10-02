"""Synthetic private native UI: explicit offers, no real applicant transmission."""
import json
import os
from pathlib import Path
import pytest
from playwright.sync_api import expect
from test_dashboard_diagnostics_browser import preparation_ui
from test_preparation_review_browser import open_review,payload,CANARY
from executor.preparation.qiyunfang import CONTRACT_URL


def install(page,observed,*,hold=False,unavailable=False,hold_fill=False,with_resume=False,before_native=None):
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
    if before_native is not None:before_native()
    page.locator('#preparation-native-open').click()
    return calls,pending,response


def public_preflight(page,*,cancel_status=200,cancel_value=None):
    pending=[];calls=[]
    def route(route):
        action=route.request.url.rsplit('/',1)[-1];data=route.request.post_data_json;calls.append((action,data))
        if action=='cancel':pending.append(route);return
        if action=='open':
            value={'mode':'READ_ONLY_PUBLIC_PREFLIGHT','status':'EMPTY_FORM_VERIFIED','request_id':data['request_id'],
                'task_id':data['task_id'],'task_revision':data['expected_revision'],'source_url':CONTRACT_URL,
                'profile_version':data['profile_version'],'resume_version':data['resume_version'],
                'plan':[{'field_id':item['field_id'],'value':item['value']} for item in payload()['proposals'] if item['field_id'] in data['selected_ids']],
                'expires_in_seconds':120,'capabilities':{'live_write':False,'submit':False,'account_verified':False,'server_draft_verified':False}}
        else:value={'status':'OFFERED','live_write_available':False,'submit_capability':False}
        route.fulfill(status=200,content_type='application/json',body=json.dumps(value))
    page.route('**/ui/api/preparation-session/*',route)
    def open_preflight():
        page.locator('#preparation-site-check').click()
        expect(page.locator('#preparation-site-status')).to_contain_text('已通过只读核对')
    def acknowledge():
        assert len(pending)==1
        pending[0].fulfill(status=cancel_status,content_type='application/json',body=json.dumps(
            cancel_value or {'status':'CANCELLATION_REQUESTED','context_closed':False,'submit_capability':False}))
    return open_preflight,acknowledge,pending,calls


def test_native_transition_waits_for_cancel_acknowledgement_without_duplicate_open(preparation_ui):
    page,observed=preparation_ui;preflight,acknowledge,pending,public_calls=public_preflight(page)
    calls,_,_=install(page,observed,before_native=preflight)
    expect(page.locator('#preparation-native-status')).to_contain_text('正在结束前次检查')
    page.wait_for_timeout(50);assert len(pending)==1 and calls==[]
    page.evaluate("document.getElementById('preparation-native-open').dispatchEvent(new MouseEvent('click'))")
    assert calls==[]
    acknowledge();expect(page.locator('#preparation-native-consent')).to_be_visible()
    assert [action for action,_ in calls]==['open']
    assert public_calls[0][1]['request_id']!=calls[0][1]['request_id']
    assert CANARY not in json.dumps(calls+public_calls) and observed['errors']==[] and observed['external']==[]


@pytest.mark.parametrize('interrupt',['hide','close','task_change','pagehide','selection'])
def test_cancel_acknowledgement_after_interruption_cannot_start_native_owner(preparation_ui,interrupt):
    page,observed=preparation_ui;preflight,acknowledge,pending,_=public_preflight(page)
    calls,_,_=install(page,observed,before_native=preflight)
    page.wait_for_timeout(50);assert len(pending)==1 and calls==[]
    if interrupt=='hide':page.locator('#preparation-review-hide').click()
    elif interrupt=='close':page.locator('#preparation-close').click()
    elif interrupt=='task_change':page.evaluate("openTaskPreparation('prep-b',9)")
    elif interrupt=='selection':page.get_by_role('checkbox',name='选择核对姓名',exact=True).uncheck()
    else:page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    acknowledge();page.wait_for_timeout(100)
    assert not any(action=='open' for action,_ in calls)
    expect(page.locator('#preparation-native-consent')).to_be_hidden()
    assert observed['errors']==[] and observed['external']==[]


@pytest.mark.parametrize('status,value',[(500,None),(200,{'status':'CLOSED','submit_capability':False}),
    (200,{'status':'CANCELLATION_REQUESTED','submit_capability':True})])
def test_unconfirmed_cancellation_refuses_replacement_without_automatic_retry(preparation_ui,status,value):
    page,observed=preparation_ui;preflight,acknowledge,_,_=public_preflight(page,cancel_status=status,cancel_value=value)
    calls,_,_=install(page,observed,before_native=preflight)
    page.wait_for_timeout(50);acknowledge()
    expect(page.locator('#preparation-review-status')).to_contain_text('条件或资料无法确认')
    assert not any(action=='open' for action,_ in calls)
    assert CANARY not in page.content() and observed['errors']==[] and observed['external']==[]


def test_changed_selection_keeps_prior_cancel_in_the_replacement_wait(preparation_ui):
    page,observed=preparation_ui;preflight,acknowledge,pending,_=public_preflight(page)
    calls,_,_=install(page,observed,before_native=preflight)
    page.wait_for_timeout(50);assert len(pending)==1
    choice=page.get_by_role('checkbox',name='选择核对姓名',exact=True)
    choice.uncheck();choice.check();page.locator('#preparation-native-open').click()
    page.wait_for_timeout(50)
    assert not any(action=='open' for action,_ in calls)
    acknowledge();expect(page.locator('#preparation-native-consent')).to_be_visible()
    assert len([action for action,_ in calls if action=='open'])==1
    assert observed['errors']==[] and observed['external']==[]


def test_lost_cancel_acknowledgement_has_a_bounded_wait_and_no_late_open(preparation_ui):
    page,observed=preparation_ui;preflight,acknowledge,_,_=public_preflight(page)
    calls,_,_=install(page,observed,before_native=preflight)
    expect(page.locator('#preparation-review-status')).to_contain_text('条件或资料无法确认',timeout=12000)
    acknowledge();page.wait_for_timeout(100)
    assert not any(action=='open' for action,_ in calls)
    assert CANARY not in page.content() and observed['errors']==[] and observed['external']==[]


def test_native_fill_is_separate_explicit_consent_and_cleared_on_hide(preparation_ui):
    page,observed=preparation_ui;calls,_,_=install(page,observed)
    expect(page.locator('#preparation-native-consent')).to_be_visible()
    if os.environ.get('JAE_UI_SCREENSHOT_DIR'):
        directory=Path(os.environ['JAE_UI_SCREENSHOT_DIR']);directory.mkdir(parents=True,exist_ok=True)
        page.locator('#preparation-native-consent').scroll_into_view_if_needed()
        page.screenshot(path=str(directory/'preparation-native-consent.png'))
    expect(page.locator('#preparation-fill-approve')).to_be_disabled()
    expect(page.locator('#preparation-resume-review')).to_be_hidden()
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
    expect(page.locator('#preparation-review-status')).to_contain_text('本机浏览器安全检查未通过')
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


@pytest.mark.parametrize('width',[375,800])
def test_private_confirmation_touch_targets_and_narrow_layout(preparation_ui,width):
    page,observed=preparation_ui;page.set_viewport_size({'width':width,'height':900})
    install(page,observed)
    panel=page.locator('#preparation-native-consent');expect(panel).to_be_visible()
    panel.scroll_into_view_if_needed()
    bounds=panel.bounding_box();assert bounds['x']>=0 and bounds['x']+bounds['width']<=width
    button=page.locator('#preparation-fill-approve').bounding_box();assert button['height']>=44
    label=page.locator('#preparation-fill-choice').locator('..').bounding_box()
    assert label['width']<=bounds['width'] and label['height']>=20
    assert page.locator('#preparation-dialog').evaluate('(node)=>node.scrollWidth<=node.clientWidth')
    if os.environ.get('JAE_UI_SCREENSHOT_DIR'):
        directory=Path(os.environ['JAE_UI_SCREENSHOT_DIR']);directory.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(directory/f'preparation-native-{width}.png'))
    assert observed['errors']==[] and observed['external']==[]


def test_human_review_explains_disabled_live_gate_without_final_approval(preparation_ui):
    page,observed=preparation_ui;calls,_,_=install(page,observed)
    page.locator('#preparation-fill-consent').check()
    page.locator('#preparation-fill-approve').click()
    expect(page.locator('#preparation-human-review')).to_be_enabled()
    reviews=[]
    def final_route(route):
        reviews.append(route.request.post_data_json)
        route.fulfill(status=200,content_type='application/json',body=json.dumps({
            'status':'UNAVAILABLE','reason':'PHYSICAL_NATIVE_AND_SITE_ACCEPTANCE_PENDING',
            'submit_capability':False,'automatic_retry':False,'server_application_verified':False}))
    page.route('**/ui/api/native-preparation/begin-human-review',final_route)
    page.locator('#preparation-human-review').click()
    expect(page.locator('#preparation-native-status')).to_contain_text('不会发送最终申请')
    assert len(reviews)==1 and set(reviews[0])=={'request_id','task_id','expected_revision'}
    assert CANARY not in json.dumps(reviews) and observed['external']==[] and observed['errors']==[]
    assert not any(name in json.dumps(reviews) for name in ('approve_submission','captcha','identity','confirm'))
    if os.environ.get('JAE_UI_SCREENSHOT_DIR'):
        directory=Path(os.environ['JAE_UI_SCREENSHOT_DIR']);directory.mkdir(parents=True,exist_ok=True)
        page.locator('#preparation-human-review').scroll_into_view_if_needed()
        page.screenshot(path=str(directory/'preparation-native-human-gate.png'))


@pytest.mark.parametrize('interrupt',['hide','close','task_change'])
def test_late_human_review_result_does_not_revive_private_state(preparation_ui,interrupt):
    page,observed=preparation_ui;install(page,observed)
    page.locator('#preparation-fill-consent').check();page.locator('#preparation-fill-approve').click()
    pending=[]
    page.route('**/ui/api/native-preparation/begin-human-review',lambda route:pending.append(route))
    page.locator('#preparation-human-review').click()
    expect(page.locator('#preparation-human-review')).to_be_disabled()
    if interrupt=='hide':page.locator('#preparation-review-hide').click()
    elif interrupt=='close':page.locator('#preparation-close').click()
    else:page.evaluate("openTaskPreparation('prep-b',9)")
    assert len(pending)==1
    pending[0].fulfill(status=200,content_type='application/json',body=json.dumps({
        'status':'MANUAL_REVIEW_ACTIVE','submit_capability':False,'automatic_retry':False,'server_application_verified':False}))
    page.wait_for_timeout(100)
    assert CANARY not in page.content() and observed['errors']==[] and observed['external']==[]
    expect(page.locator('#preparation-human-review')).to_be_hidden()
