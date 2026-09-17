from __future__ import annotations
import json,re,time
from pathlib import Path
from .browser import connect,latest_page
from .field_classifier import classify,is_final_submit,is_next,is_initial_apply
from .profile import load_profile,get_value,masked_preview
from .runtime import save_runtime
from .state import RuntimeState,RunState

ROOT=Path.home()/"Job-Application-Executor"
VISIBLE_FIELD_SELECTOR='input:not([type="hidden"]), textarea, select'
BUTTON_SELECTOR='button, input[type="button"], input[type="submit"], [role="button"], a'

class Executor:
    def __init__(self,job_url,resume_path,profile_path,auth_wait_seconds=900):
        self.job_url=job_url
        self.resume=Path(resume_path).expanduser().resolve()
        self.profile_path=Path(profile_path).expanduser().resolve()
        if not self.resume.is_file(): raise FileNotFoundError(self.resume)
        if not self.profile_path.is_file(): raise FileNotFoundError(self.profile_path)
        self.profile=load_profile(self.profile_path)
        self.auth_wait_seconds=int(auth_wait_seconds or 900)
        self.state=RuntimeState(state=RunState.OPENING,job_url=job_url,profile_path=str(self.profile_path),resume_path=str(self.resume))
        self.pw=self.browser=self.ctx=self.page=None

    def checkpoint(self,state=None,error=None):
        if state: self.state.state=state
        if self.page: self.state.active_url=self.page.url
        self.state.last_error=error
        save_runtime(self.state)

    def screenshot(self,name):
        p=ROOT/"screenshots"/f"{int(time.time())}-{name}.png"
        self.page.screenshot(path=str(p),full_page=True); return str(p)

    @staticmethod
    def _visible(loc):
        try: return loc.is_visible() and loc.is_enabled()
        except Exception: return False

    def _hint(self,el):
        script="""e=>{const labels=[...(e.labels||[])].map(x=>x.innerText);const near=e.closest('label,fieldset,[class*=field],[class*=form]');return [labels.join(' '),e.getAttribute('aria-label'),e.getAttribute('placeholder'),e.getAttribute('name'),e.id,near?.innerText?.slice(0,240)].filter(Boolean).join(' | ')}"""
        try: return el.evaluate(script)
        except Exception: return ""

    def _selector_desc(self,el,i):
        try:
            eid=el.get_attribute('id'); name=el.get_attribute('name'); tag=el.evaluate('e=>e.tagName.toLowerCase()')
            if eid: return '#'+eid
            if name: return f'{tag}[name="{name}"]'
        except Exception: pass
        return f'field[{i}]'

    def _fill_select(self,el,value):
        target=str(value).strip().lower(); opts=el.locator('option')
        for i in range(opts.count()):
            o=opts.nth(i); txt=(o.inner_text() or '').strip(); val=o.get_attribute('value') or ''
            if target in {txt.lower(),val.lower()} or (target and target in txt.lower()):
                el.select_option(value=val); return True
        return False

    def inspect_and_fill(self):
        filled=[]; unresolved=[]; fields=self.page.locator(VISIBLE_FIELD_SELECTOR)
        for i in range(fields.count()):
            el=fields.nth(i)
            if not self._visible(el): continue
            try: tag=el.evaluate('e=>e.tagName.toLowerCase()')
            except Exception: continue
            typ=(el.get_attribute('type') or tag).lower(); hint=self._hint(el); autocomplete=el.get_attribute('autocomplete') or ''
            action,key,reason=classify(hint,typ,autocomplete)
            required=el.get_attribute('required') is not None or el.get_attribute('aria-required')=='true'
            selector=self._selector_desc(el,i)
            if action=='AUTO_UPLOAD':
                if typ=='file':
                    el.set_input_files(str(self.resume)); filled.append({'selector':selector,'label':hint[:180],'key':'resume','value':self.resume.name,'action':'UPLOAD'})
                continue
            if action=='ASK_USER':
                unresolved.append({'selector':selector,'label':hint[:240],'required':required,'reason':reason,'action':action}); continue
            if action=='AUTO_FILL':
                value=get_value(self.profile,key)
                if value is None:
                    if required: unresolved.append({'selector':selector,'label':hint[:240],'key':key,'required':True,'reason':'profile value missing','action':'MISSING_PROFILE'})
                    continue
                try:
                    if tag=='select': ok=self._fill_select(el,value)
                    elif typ in ('checkbox','radio'): ok=False
                    else:
                        current=el.input_value() if tag in ('input','textarea') else ''
                        if not current.strip(): el.fill(str(value))
                        ok=True
                    if ok: filled.append({'selector':selector,'label':hint[:180],'key':key,'value':masked_preview(key,value),'action':'FILL'})
                    elif required: unresolved.append({'selector':selector,'label':hint[:240],'key':key,'required':True,'reason':'could not map option/control','action':'UNRESOLVED'})
                except Exception as e:
                    unresolved.append({'selector':selector,'label':hint[:240],'key':key,'required':required,'reason':f'fill error: {type(e).__name__}','action':'UNRESOLVED'})
            elif required and typ not in ('checkbox','radio','submit','button'):
                try:
                    if not (el.input_value() or '').strip(): unresolved.append({'selector':selector,'label':hint[:240],'required':True,'reason':'unknown required field','action':'UNKNOWN'})
                except Exception: pass
        seen={(x.get('selector'),x.get('key'),x.get('action')) for x in self.state.filled_fields}
        for item in filled:
            sig=(item.get('selector'),item.get('key'),item.get('action'))
            if sig not in seen:
                self.state.filled_fields.append(item); seen.add(sig)
        self.state.unresolved_fields=unresolved
        self.checkpoint(RunState.FORM_FILLING)
        return filled,unresolved

    def buttons(self):
        out=[]; loc=self.page.locator(BUTTON_SELECTOR)
        for i in range(min(loc.count(),180)):
            el=loc.nth(i)
            if not self._visible(el): continue
            try: text=(el.inner_text() or el.get_attribute('value') or el.get_attribute('aria-label') or '').strip()
            except Exception: continue
            if text: out.append((el,text))
        return out

    def has_auth_challenge(self):
        try:
            if self.page.locator('input[type="password"]:visible').count(): return True
            text=(self.page.locator('body').inner_text(timeout=3000) or '')[-6000:].lower()
            auth=bool(re.search(r'验证码|verification code|one.?time code|sign in|log in|登录|扫码|captcha|verify you are human',text,re.I))
            return auth and self.page.locator(VISIBLE_FIELD_SELECTOR).count()<8
        except Exception: return False

    def click_initial_apply_if_needed(self):
        if self.page.locator(VISIBLE_FIELD_SELECTOR).count()>3: return False
        for el,text in self.buttons():
            if is_initial_apply(text) and not is_final_submit(text):
                el.click(); self.page.wait_for_timeout(1500); self.page=latest_page(self.ctx,self.page); return True
        return False

    def wait_for_auth(self,max_seconds=None):
        max_seconds=self.auth_wait_seconds if max_seconds is None else max_seconds
        if not self.has_auth_challenge(): return True
        self.checkpoint(RunState.AUTHENTICATING); self.screenshot('authentication-needed')
        deadline=time.time()+max_seconds
        while time.time()<deadline:
            self.page.wait_for_timeout(1500); self.page=latest_page(self.ctx,self.page)
            if not self.has_auth_challenge():
                self.checkpoint(RunState.LOCATING_APPLICATION); return True
        self.checkpoint(RunState.AUTHENTICATING,'authentication wait timed out'); return False

    def final_submit(self):
        for _,text in self.buttons():
            if is_final_submit(text): return text
        return None

    def click_next(self):
        for el,text in self.buttons():
            if is_next(text):
                el.click(); self.page.wait_for_timeout(1200); self.page=latest_page(self.ctx,self.page); return text
        return None

    def write_review(self):
        shot=self.screenshot('review')
        data={'state':self.state.state,'job_url':self.job_url,'active_url':self.page.url,'resume':self.resume.name,'filled_fields':self.state.filled_fields,'unresolved_fields':self.state.unresolved_fields,'final_submit_detected':self.state.final_submit_detected,'screenshot':shot}
        p=ROOT/'runs'/f'review-{int(time.time())}.json'; p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); return p

    def run(self,max_pages=12):
        self.pw,self.browser,self.ctx,self.page=connect(self.job_url)
        try:
            self.checkpoint(RunState.LOCATING_APPLICATION)
            for page_index in range(max_pages):
                self.state.page_index=page_index; self.checkpoint(); self.page.wait_for_timeout(700)
                if self.has_auth_challenge() and not self.wait_for_auth(): return self.state
                if self.click_initial_apply_if_needed() and self.has_auth_challenge() and not self.wait_for_auth(): return self.state
                _,unresolved=self.inspect_and_fill(); final=self.final_submit()
                if final:
                    self.state.final_submit_detected=True; self.checkpoint(RunState.WAITING_USER_CONFIRMATION); self.write_review(); return self.state
                required=[x for x in unresolved if x.get('required')]
                if required:
                    self.checkpoint(RunState.WAITING_USER_INPUT); self.write_review(); return self.state
                if self.click_next(): continue
                self.checkpoint(RunState.WAITING_USER_INPUT); self.write_review(); return self.state
            self.checkpoint(RunState.WAITING_USER_INPUT,'max page limit reached'); return self.state
        except Exception as e:
            self.checkpoint(RunState.ERROR,f'{type(e).__name__}: {e}')
            try: self.screenshot('error')
            except Exception: pass
            return self.state
        finally:
            try: self.pw.stop()
            except Exception: pass
