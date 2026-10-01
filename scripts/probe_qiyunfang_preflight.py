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
import time
import copy
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from executor.preparation.session import DisposablePreparationSession
from executor.preparation.qiyunfang import CONTRACT_URL, ROOT, OBSERVE_ROOT, validate_observation, ContractChanged
from executor.preparation.public_reads import public_style_url


def descriptor(url,method):
    try:
        parsed=urlsplit(url);query=parse_qs(parsed.query,keep_blank_values=True)
        result={'scheme':parsed.scheme,'host':parsed.hostname,'path':parsed.path,'method':method,
                'query_keys':sorted(query)}
        for key in ('cmd','formId','id','moduleId','colId','extId','_csw','clientSupportWebp','v','vCodeId'):
            values=query.get(key,[])
            if len(values)==1 and re.fullmatch(r'[A-Za-z0-9_]{1,70}',values[0]):result[key]=values[0]
        return result
    except Exception:return {'status':'UNCLASSIFIED'}


def value_free_contract(observation):
    """Public empty-form research only; never include an input value."""
    value=copy.deepcopy(observation)
    for field in value.get('fields',[]):
        for control in field.get('controls',[]):
            entered=control.pop('value',None)
            control['value_empty']=entered in ('',None)
    return value


def probe():
    result={'source':CONTRACT_URL,'status':'READ_ONLY_PREFLIGHT_UNVERIFIED',
            'root_count':0,'contract':'UNOBSERVED','outside_static_get_manifest':[],'fetched':[],
            'applicant_data_entered':False,'live_enabled':False,'public_reads':[],
            'public_html_style_links':[]}
    # Explicit platform target. Never retry a sandbox rejection with weaker
    # flags, a renamed executable, or changes to host security settings.
    channel='chrome' if sys.platform=='linux' else None
    result['browser_target']='installed_chrome_headless_linux' if channel else 'bundled_headless_mac'
    with DisposablePreparationSession(headless=True,channel=channel) as session:
        result['browser_version']=session.browser.version
        transport=session.transport;original=transport._route
        def route(request_route):
            request=request_route.request
            if not transport._public_get(request) and len(result['outside_static_get_manifest'])<100:
                item=descriptor(request.url,request.method)
                # This probe never loads profiles or enters a field. Inspect
                # only public template selectors, never arbitrary request bodies.
                if request.method=='POST' and request.url=='https://www.qiyunfang.com/ajax/module_h.jsp':
                    try:
                        from urllib.parse import parse_qsl
                        body=request.post_data or ''
                        params=dict(parse_qsl(body,keep_blank_values=True,max_num_fields=16)) if len(body)<2048 else {}
                        known={}
                        for key in ('cmd','_fresh','_colId','_extId','popupZoneId','manageMode','_manageMode','_majorColor','_vueStyleGrayTest'):
                            value=params.get(key)
                            if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_#-]{0,70}',value):known[key]=value
                        item['public_template_parameters']=known
                    except Exception:item['public_template_parameters']={'status':'UNAVAILABLE'}
                elif request.method=='POST' and request.url.split('?',1)[0] in {
                        'https://www.qiyunfang.com/ajax/site_h.jsp','https://www.qiyunfang.com/ajax/log_h.jsp',
                        'https://www.qiyunfang.com/ajax/ajaxLoadModuleDom_h.jsp'}:
                    try:
                        body=request.post_data or ''
                        params=parse_qs(body,keep_blank_values=True,max_num_fields=32) if len(body)<4096 else {}
                        cmd=params.get('cmd',[])
                        if len(cmd)==1 and re.fullmatch(r'[A-Za-z0-9_]{1,80}',cmd[0]):item['public_body_command']=cmd[0]
                    except Exception:pass
                if item not in result['outside_static_get_manifest']:result['outside_static_get_manifest'].append(item)
            original(request_route)
        session.context.unroute('**/*',transport._route)
        session.context.route('**/*',route)
        public_get=transport.public_fetch
        def fetch(url):
            response=public_get(url)
            result['fetched'].append({'url':url,'status':response.status,'sha256':hashlib.sha256(response.body()).hexdigest()})
            if url==CONTRACT_URL and response.status==200:
                from html.parser import HTMLParser
                from urllib.parse import urljoin
                class Styles(HTMLParser):
                    def handle_starttag(self,tag,attrs):
                        if tag!='link':return
                        href=dict(attrs).get('href','');candidate=urljoin(CONTRACT_URL,href)
                        if public_style_url(candidate):
                            result['public_html_style_links'].append(descriptor(candidate,'GET'))
                raw=response.body()
                if len(raw)<=2000000:Styles().feed(raw.decode('utf-8',errors='replace'))
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
            # The popup container can be visible before its async controls are
            # initialized. Wait for the complete existing contract, never accept
            # a weaker shape just because the root became visible.
            deadline=time.monotonic()+3
            while True:
                observation=page.locator(ROOT).evaluate(OBSERVE_ROOT)
                try:validate_observation(observation);break
                except ContractChanged:
                    if time.monotonic()>=deadline:
                        result['observed_contract']=value_free_contract(observation)
                        raise
                    page.wait_for_timeout(100)
            result['contract']='EMPTY_FORM_MATCHED'
            result['status']='READ_ONLY_EMPTY_FORM_OBSERVED'
            result['discovery_denials']=dict(transport.discovery_denials)
            result['unclassified_denials']=transport.blocked-sum(transport.discovery_denials.values())
            try:
                result['source_receipt']=transport.certify_discovery()
                transport.seal();transport.require_sealed()
                result['sealed_empty_preflight']=True
            except RuntimeError:result['sealed_empty_preflight']=False
        except Exception:
            # No exception repr/URL/body values in diagnostic output.
            result['status']='PUBLIC_PREFLIGHT_INCOMPLETE'
        result['public_reads']=list(transport.public_read_evidence)
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    faulthandler.dump_traceback_later(90,exit=True)
    try:result=probe()
    except Exception as error:
        phase=getattr(error,'phase','UNCLASSIFIED')
        allowed={'PRIVATE_ENVIRONMENT','DENY_LISTENER','DRIVER_START','SANDBOXED_BROWSER_LAUNCH','NATIVE_LAUNCH_RECIPE','OWNED_PROCESS_IDENTITY','API_CLIENT','EMPTY_CONTEXT','REQUEST_GUARD'}
        result={'status':'PUBLIC_PROBE_UNAVAILABLE','phase':phase if phase in allowed else 'UNCLASSIFIED','live_enabled':False,'applicant_data_entered':False}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'live_enabled':False}))
