from __future__ import annotations
import re,time
from pathlib import Path
from .browser import connect,latest_page
from .field_classifier import is_final_submit
from .runtime import load_runtime,save_runtime
from .state import RunState
ROOT=Path.home()/"Job-Application-Executor"
BUTTON_SELECTOR='button, input[type="button"], input[type="submit"], [role="button"]'

def submit_current(confirm: str):
    if confirm!='FINAL_SUBMIT': raise ValueError('explicit confirmation token FINAL_SUBMIT is required')
    state=load_runtime()
    if state.state!=RunState.WAITING_USER_CONFIRMATION or not state.final_submit_detected:
        raise RuntimeError(f'not at final confirmation gate: {state.state}')
    pw,browser,ctx,page=connect(state.active_url)
    try:
        candidates=[]; loc=page.locator(BUTTON_SELECTOR)
        for i in range(loc.count()):
            el=loc.nth(i)
            try:
                if not el.is_visible() or not el.is_enabled(): continue
                text=(el.inner_text() or el.get_attribute('value') or el.get_attribute('aria-label') or '').strip()
                if is_final_submit(text): candidates.append((el,text))
            except Exception: continue
        if len(candidates)!=1: raise RuntimeError(f'expected exactly one final submit control, found {len(candidates)}')
        before=ROOT/'screenshots'/f'{int(time.time())}-before-final-submit.png'; page.screenshot(path=str(before),full_page=True)
        state.state=RunState.SUBMITTING; save_runtime(state)
        candidates[0][0].click(); page.wait_for_timeout(2500); page=latest_page(ctx,page)
        text=(page.locator('body').inner_text(timeout=5000) or '')[-8000:]
        success=bool(re.search(r'application (has been )?submitted|thank you for applying|申请已提交|投递成功|申请成功|提交成功',text,re.I))
        state.active_url=page.url; state.state=RunState.SUBMITTED if success else RunState.SUBMITTING
        state.final_submit_detected=False; state.last_error=None if success else 'submit clicked; success receipt not yet confirmed'
        save_runtime(state)
        after=ROOT/'screenshots'/f'{int(time.time())}-after-final-submit.png'; page.screenshot(path=str(after),full_page=True)
        return state
    finally:
        pw.stop()
