from __future__ import annotations
import math,re
from dataclasses import dataclass,asdict
from urllib.parse import urljoin, urlencode
from .browser import connect

@dataclass
class Candidate:
    title:str
    job_id:str
    location:str
    category:str
    job_url:str
    apply_url:str|None=None
    exact_title:bool=False

SCHNEIDER_ALIASES={'施耐德','施耐德电气','schneider','schneider electric'}

def _ancestor_text(anchor):
    return anchor.evaluate("""e=>{let p=e;for(let i=0;i<7&&p;i++,p=p.parentElement){const t=(p.innerText||'').trim();if((t.includes('Req ID:')||t.includes('申请ID'))&&(t.includes('Location')||t.includes('位置')))return t;}return (e.parentElement?.innerText||'').trim();}""")

def _parse_card(title,href,text,apply_url,query_title):
    jid=''; loc=''; cat=''
    m=re.search(r'(?:Req ID:|申请ID[:：]?)\s*(\d+)',text,re.I); jid=m.group(1) if m else ''
    m=re.search(r'(?:Location|位置)\s*\n([^\n]+)\n([^\n]+)',text,re.I)
    if m: loc=' / '.join(x.strip() for x in m.groups() if x.strip())
    m=re.search(r'(?:Categories|分类)\s*\n([^\n]+)',text,re.I); cat=m.group(1).strip() if m else ''
    return Candidate(title=title.strip(),job_id=jid,location=loc,category=cat,job_url=urljoin('https://careers.se.com',href),apply_url=apply_url,exact_title=title.strip().casefold()==query_title.strip().casefold())

def resolve_schneider(title:str,location:str='China',max_pages:int=8):
    pw,browser,ctx,page=connect(); out=[]
    try:
        base='https://careers.se.com/jobs?'+urlencode({'keywords':title,'location':location,'stretch':'10','stretchUnit':'MILES','sortBy':'relevance','page':'1'})
        page.goto(base,wait_until='domcontentloaded',timeout=60000); page.wait_for_timeout(2000)
        body=page.locator('body').inner_text()
        m=re.search(r'(\d+)\s*(?:Results|结果)',body,re.I); total=int(m.group(1)) if m else 10
        pages=min(max_pages,max(1,math.ceil(total/10)))
        for n in range(1,pages+1):
            if n>1:
                page.goto(re.sub(r'page=\d+',f'page={n}',base),wait_until='domcontentloaded',timeout=60000); page.wait_for_timeout(1200)
            anchors=page.locator('a[href^="/jobs/"]')
            for i in range(anchors.count()):
                a=anchors.nth(i); txt=(a.inner_text() or '').strip(); href=a.get_attribute('href') or ''
                if not txt or not re.match(r'^/jobs/\d+',href): continue
                card=_ancestor_text(a)
                jidm=re.search(r'/jobs/(\d+)',href); jid=jidm.group(1) if jidm else ''
                apply=None
                if jid:
                    al=page.locator(f'a[href*="/jobs/{jid}/login"]')
                    if al.count(): apply=al.first.get_attribute('href')
                c=_parse_card(txt,href,card,apply,title)
                if c.job_id and all(x.job_id!=c.job_id for x in out): out.append(c)
        out.sort(key=lambda c:(not c.exact_title,c.title.casefold(),c.location.casefold()))
        return out
    finally:
        page.close(); browser.close(); pw.stop()

def resolve(company:str,title:str,location:str='China'):
    if company.strip().casefold() in SCHNEIDER_ALIASES:
        return resolve_schneider(title,location)
    raise NotImplementedError(f'company resolver not implemented yet: {company}')

def candidate_dicts(items): return [asdict(x) for x in items]
