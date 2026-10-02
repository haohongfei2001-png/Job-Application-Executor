"""Synthetic renderer evidence only; no transmission or human authorization."""
import pytest
from test_qiyunfang_preparation_browser import browser, bounded_browser_oracle
from executor.preparation.change_epoch import PreDocumentChangeEpoch, ChangeEpochConflict

HTML='''<div id="popupLevelWrap"><div id="module1567"><div class="m_siteform">
<div class="form_container"><input id="field" value="SYNTHETIC"><input id="check" type="checkbox">
<textarea id="area">SYNTHETIC</textarea><select id="select"><option>a</option><option>b</option></select>
</div></div></div></div>'''

@pytest.fixture
def watched(browser):
    context=browser.new_context(service_workers='block')
    observer=PreDocumentChangeEpoch(context)
    context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=HTML))
    page=context.new_page();page.goto('https://synthetic.invalid/')
    observer.seal()
    try:yield page,observer
    finally:context.close()

def test_readback_never_invokes_protected_value_getter(watched):
    page,observer=watched
    page.evaluate("Object.defineProperty(document.querySelector('#field'),'value',{get(){throw Error('value getter must not run')}})")
    with pytest.raises(ChangeEpochConflict,match='preparation_change_epoch_conflict'):
        observer.unchanged()

@pytest.mark.parametrize('mutation',[
    "field.value='changed';field.value='SYNTHETIC'",
    "check.checked=true;check.checked=false",
    "area.value='changed';area.value='SYNTHETIC'",
    "select.selectedIndex=1;select.selectedIndex=0",
    "field.setRangeText('x',0,1);field.setRangeText('S',0,1)",
    "field.dispatchEvent(new Event('input',{bubbles:true}))",
    "field.replaceWith(field.cloneNode(true))",
    "const root=field.parentElement;root.replaceWith(root.cloneNode(true))",
    "const frame=document.createElement('iframe');document.body.append(frame);frame.remove()",
    "Object.defineProperty(HTMLInputElement.prototype,'value',{get(){return ''},set(_){}})",
])
def test_change_or_instrumentation_drift_invalidates_even_after_reversal(watched,mutation):
    page,observer=watched
    page.evaluate('''code => {
      const field=document.querySelector('#field'), check=document.querySelector('#check');
      const area=document.querySelector('#area'), select=document.querySelector('#select');
      try { eval(code); } catch (_) {}
    }''',mutation)
    with pytest.raises(ChangeEpochConflict):observer.unchanged()

def test_native_keyboard_edit_invalidates(watched):
    page,observer=watched
    page.locator('#field').press('End');page.keyboard.type('x')
    with pytest.raises(ChangeEpochConflict):observer.unchanged()

def test_navigation_cannot_restore_epoch(watched):
    page,observer=watched;page.reload()
    with pytest.raises(ChangeEpochConflict):observer.unchanged()


def test_untouched_snapshot_is_repeatable_and_value_free(watched):
    page,observer=watched
    assert observer.unchanged()==observer.baseline
    assert observer.unchanged()==observer.baseline
    result=page.evaluate('key=>globalThis[key].snapshot()',observer.key)
    assert set(result)=={'format','epoch','invalid','sealed'}
    assert result['invalid'] is False
    assert 'SYNTHETIC' not in str(result)


@pytest.mark.parametrize('mutation',[
    "field.type='number';field.value='3'",
    "Object.defineProperty(field,'value',{configurable:true,get(){return 'other'}});delete field.value",
    "Reflect.defineProperty(field,'value',{configurable:true,get(){return 'other'}});delete field.value",
    "Object.defineProperties(field,{value:{configurable:true,get(){return 'other'}}});delete field.value",
    "field.__defineGetter__('value',()=> 'other');delete field.value",
    "field.__defineSetter__('value',()=> {});delete field.value",
    "const p=Object.getPrototypeOf(field);Object.setPrototypeOf(field,{});Object.setPrototypeOf(field,p)",
    "const p=Object.getPrototypeOf(field);Reflect.setPrototypeOf(field,{});Reflect.setPrototypeOf(field,p)",
    "const p=Object.getPrototypeOf(field);field.__proto__={};field.__proto__=p",
    "const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');try{Object.defineProperty(HTMLInputElement.prototype,'value',{...d,set(x){}})}finally{Object.defineProperty(HTMLInputElement.prototype,'value',d)}",
    "const host=document.createElement('div');host.attachShadow({mode:'closed'})",
    "const frame=document.createElement('iframe');document.body.append(frame);const set=Object.getOwnPropertyDescriptor(frame.contentWindow.HTMLInputElement.prototype,'value').set;set.call(field,'changed');frame.remove()",
    "const object=document.createElement('object');document.body.append(object);object.remove()",
    "document.open();document.write('replaced');document.close()",
    "const restore=Array.prototype[Symbol.iterator];Array.prototype[Symbol.iterator]=function*(){};field.value='changed';Array.prototype[Symbol.iterator]=restore",
    "const restore=Array.prototype.some;Array.prototype.some=()=>false;field.replaceWith(field.cloneNode(true));Array.prototype.some=restore",
])
def test_extended_mutation_and_reflection_paths_revoke_permanently(watched,mutation):
    page,observer=watched
    page.evaluate("code=>{const field=document.querySelector('#field');try{eval(code)}catch(_){}}",mutation)
    with pytest.raises(ChangeEpochConflict):observer.unchanged()
    with pytest.raises(ChangeEpochConflict):observer.unchanged()


def test_captured_wrapped_setter_before_seal_cannot_evade_epoch(browser):
    context=browser.new_context(service_workers='block')
    observer=PreDocumentChangeEpoch(context)
    source=HTML+'''<script>globalThis.capturedSetter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set</script>'''
    context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=source))
    page=context.new_page();page.goto('https://synthetic.invalid/');observer.seal()
    page.evaluate("Reflect.apply(capturedSetter,document.querySelector('#field'),['changed'])")
    with pytest.raises(ChangeEpochConflict):observer.unchanged()
    context.close()


@pytest.mark.parametrize('kind',['number','date'])
def test_typed_native_value_setters_revoke_without_input_event(browser,kind):
    context=browser.new_context(service_workers='block');observer=PreDocumentChangeEpoch(context)
    source=HTML.replace('id="field" value="SYNTHETIC"',f'id="field" type="{kind}"')
    context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=source))
    page=context.new_page();page.goto('https://synthetic.invalid/');observer.seal()
    page.evaluate("kind=>{const field=document.querySelector('#field');if(kind==='number'){field.valueAsNumber=7;field.valueAsNumber=NaN}else{field.valueAsDate=new Date('2026-10-02');field.valueAsDate=null}}",kind)
    with pytest.raises(ChangeEpochConflict):observer.unchanged()
    context.close()


@pytest.mark.parametrize('native_click',[False,True])
def test_outside_radio_peer_cannot_silently_uncheck_inside_control(browser,native_click):
    context=browser.new_context(service_workers='block');observer=PreDocumentChangeEpoch(context)
    source=HTML.replace('id="check" type="checkbox"','id="check" type="radio" name="group" checked')+'<input id="outside" type="radio" name="group">'
    context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=source))
    page=context.new_page();page.goto('https://synthetic.invalid/');observer.seal()
    if native_click:page.locator('#outside').click()
    else:page.evaluate("document.querySelector('#outside').checked=true")
    with pytest.raises(ChangeEpochConflict):observer.unchanged()
    context.close()


def test_reflection_wrapper_replacement_cannot_suppress_own_descriptor_refusal(watched):
    page,observer=watched
    page.evaluate('''() => {
      const original=Object.defineProperty;
      try { Object.defineProperty=()=>{}; } catch (_) {}
      try { Object.defineProperty(document.querySelector('#field'),'value',{get(){throw Error('PRIVATE_CANARY')},configurable:true}); } catch (_) {}
      try { Object.defineProperty=original; } catch (_) {}
    }''')
    with pytest.raises(ChangeEpochConflict):observer.unchanged()


def test_unsupported_control_accessor_is_refused_without_reading_it(browser):
    context=browser.new_context(service_workers='block');observer=PreDocumentChangeEpoch(context)
    source=HTML+'''<script>globalThis.protectedReads=0;Object.defineProperty(document.querySelector('#field'),'value',{get(){protectedReads++;return 'PRIVATE_CANARY'},configurable:true})</script>'''
    context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=source))
    page=context.new_page();page.goto('https://synthetic.invalid/')
    with pytest.raises(ChangeEpochConflict):observer.seal()
    assert page.evaluate('protectedReads')==0
    context.close()


def test_earliest_window_capture_survives_page_propagation_suppression(watched):
    page,observer=watched
    page.evaluate("window.addEventListener('input',event=>event.stopImmediatePropagation(),true)")
    page.locator('#field').press('End');page.keyboard.type('x');page.keyboard.press('Backspace')
    with pytest.raises(ChangeEpochConflict):observer.unchanged()
    with pytest.raises(ChangeEpochConflict):observer.unchanged()


def test_writable_global_alias_cannot_forge_a_healthy_native_read(watched):
    page,observer=watched
    page.evaluate('''({key,epoch}) => {
      document.querySelector('#field').value='changed';
      const fake={};fake[key]={snapshot:()=>({format:'preparation-change-epoch-v1',epoch,invalid:false,sealed:true})};
      try { window.globalThis=fake; } catch (_) {}
    }''',{'key':observer.key,'epoch':observer.baseline})
    with pytest.raises(ChangeEpochConflict):observer.unchanged()
    with pytest.raises(ChangeEpochConflict):observer.unchanged()


def test_reason_diagnostic_never_exports_protected_descriptor_data(watched):
    page,observer=watched
    page.evaluate("Object.defineProperty(document.querySelector('#field'),'value',{get(){throw Error('PRIVATE_REASON_CANARY')},configurable:true})")
    with pytest.raises(ChangeEpochConflict):observer.unchanged()
    assert observer.diagnostic()=='POST_SEAL_REFLECTION'
    with pytest.raises(ChangeEpochConflict):observer.unchanged()
