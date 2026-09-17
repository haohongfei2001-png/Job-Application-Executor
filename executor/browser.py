from __future__ import annotations
import subprocess,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP="http://127.0.0.1:9333"
PROFILE=Path.home()/"Job-Application-Executor/chrome-profile"

def _alive():
    try:
        with urllib.request.urlopen(CDP+"/json/version",timeout=1.5) as r: return r.status==200
    except Exception: return False

def ensure_chrome(start_url="about:blank"):
    PROFILE.mkdir(parents=True,exist_ok=True)
    if _alive(): return
    subprocess.Popen([CHROME,f"--user-data-dir={PROFILE}","--remote-debugging-port=9333","--remote-debugging-address=127.0.0.1","--no-first-run","--no-default-browser-check",start_url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    for _ in range(40):
        if _alive(): return
        time.sleep(.5)
    raise RuntimeError("application Chrome did not expose CDP on 9333")

def connect(target_url=None):
    ensure_chrome("about:blank")
    pw=sync_playwright().start(); browser=pw.chromium.connect_over_cdp(CDP); ctx=browser.contexts[0]
    pages=[p for p in ctx.pages if not p.is_closed()]
    if target_url:
        exact=[p for p in pages if p.url==target_url]
        if exact:
            page=exact[-1]
        else:
            blanks=[p for p in pages if p.url in ("about:blank","chrome://newtab/","chrome://new-tab-page/")]
            page=blanks[-1] if blanks else ctx.new_page()
            page.goto(target_url,wait_until="domcontentloaded",timeout=60000)
    else:
        page=pages[-1] if pages else ctx.new_page()
    return pw,browser,ctx,page

def latest_page(ctx,fallback):
    pages=[p for p in ctx.pages if not p.is_closed()]; return pages[-1] if pages else fallback
