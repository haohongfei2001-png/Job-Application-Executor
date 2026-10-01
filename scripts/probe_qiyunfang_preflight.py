#!/usr/bin/env python3
"""Explicit public read-only probe. Never imports applicant files or fills fields.

Run only as an engineering diagnostic. Unknown requests are refused and reported
as value-free descriptors, never automatically learned as an allowlist.
"""
from __future__ import annotations
import argparse
import faulthandler
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from executor.preparation.session import DisposablePreparationSession
from executor.preparation.qiyunfang import CONTRACT_URL, ROOT, OBSERVE_ROOT, validate_observation


def descriptor(url,method):
    try:
        parsed=urlsplit(url);query=parse_qs(parsed.query,keep_blank_values=True)
        result={'scheme':parsed.scheme,'host':parsed.hostname,'path':parsed.path,'method':method,
                'query_keys':sorted(query)}
        for key in ('cmd','formId','id','moduleId'):
            values=query.get(key,[])
            if len(values)==1 and re.fullmatch(r'[A-Za-z0-9_]{1,70}',values[0]):result[key]=values[0]
        return result
    except Exception:return {'status':'UNCLASSIFIED'}


def probe():
    result={'source':CONTRACT_URL,'status':'READ_ONLY_PREFLIGHT_UNVERIFIED',
            'root_count':0,'contract':'UNOBSERVED','blocked':[],'fetched':[],
            'applicant_data_entered':False,'live_enabled':False}
    with DisposablePreparationSession(headless=True,channel=None) as session:
        transport=session.transport;original=transport._route
        def route(request_route):
            request=request_route.request
            if not transport._public_get(request) and len(result['blocked'])<100:
                item=descriptor(request.url,request.method)
                if item not in result['blocked']:result['blocked'].append(item)
            original(request_route)
        session.context.unroute('**/*',transport._route)
        session.context.route('**/*',route)
        public_get=transport.public_fetch
        def fetch(url):
            response=public_get(url)
            result['fetched'].append({'url':url,'status':response.status,'sha256':hashlib.sha256(response.body()).hexdigest()})
            return response
        transport.public_fetch=fetch
        page=session.context.new_page()
        try:
            page.goto(CONTRACT_URL,wait_until='domcontentloaded',timeout=30000)
            button=page.locator('#module2398FlBtn')
            if button.count()!=1 or button.get_attribute('href')!='javascript: JZ.transformForLinkFunc("linkForm", 6);':
                result['status']='APPLY_LINK_CONTRACT_CHANGED';return result
            button.click(timeout=10000)
            page.locator(ROOT).wait_for(state='visible',timeout=15000)
            result['root_count']=page.locator(ROOT).count()
            observation=page.locator(ROOT).evaluate(OBSERVE_ROOT)
            validate_observation(observation)
            result['contract']='EMPTY_FORM_MATCHED'
            result['status']='READ_ONLY_EMPTY_FORM_OBSERVED'
        except Exception:
            # No exception repr/URL/body values in diagnostic output.
            result['status']='PUBLIC_PREFLIGHT_INCOMPLETE'
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    faulthandler.dump_traceback_later(90,exit=True)
    try:result=probe()
    except Exception:result={'status':'PUBLIC_PROBE_UNAVAILABLE','live_enabled':False,'applicant_data_entered':False}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'live_enabled':False}))
