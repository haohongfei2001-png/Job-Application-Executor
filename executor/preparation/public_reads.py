"""Finite pre-data public form lookup, reconstructed from audited source.

This is not a generic POST pass-through. The only admitted body is the literal
formId6 lookup emitted by pinned partitionSite.js. Never call after sealing or
with applicant fields present; no submission/upload/login command is admitted.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qsl,urlsplit

FORM_LOOKUP_URL='https://www.qiyunfang.com/ajax/siteForm_h.jsp?cmd=getWafNotCk_getFormPopupId'
FORM_LOOKUP_BODY='&formId=6'
CSS_URL='https://www.qiyunfang.com/jzcusstyle.jsp'


def public_style_url(url):
    if not isinstance(url,str) or len(url)>1024:return False
    parsed=urlsplit(url)
    if parsed.scheme+'://'+parsed.netloc+parsed.path!=CSS_URL or parsed.fragment:return False
    try:pairs=parse_qsl(parsed.query,keep_blank_values=True,strict_parsing=True,max_num_fields=5)
    except ValueError:return False
    params=dict(pairs)
    return (len(pairs)==len(params)==5 and set(params)=={'id','colId','extId','_csw','clientSupportWebp'}
            and all(params[key]==value for key,value in {'id':'124','colId':'124','extId':'0','_csw':'0'}.items())
            and params['clientSupportWebp'] in {'true','false'})


def form_lookup_request(request):
    # Invoked only inside READ_ONLY before form rendering/field approval.
    try:
        return (request.method=='POST' and request.url==FORM_LOOKUP_URL
                and request.post_data==FORM_LOOKUP_BODY)
    except (AttributeError,TypeError,UnicodeError):return False


def lookup_evidence(response):
    raw=response.body()
    if len(raw)>4096 or response.status!=200:raise ValueError('public_lookup_unverified')
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('public_lookup_unverified')
            value[key]=item
        return value
    value=json.loads(raw,object_pairs_hook=unique)
    if (not isinstance(value,dict) or value.get('success') is not True
            or type(value.get('popupId')) is not int or not 0<value['popupId']<10000000):
        raise ValueError('public_lookup_unverified')
    # Public template ID only; no raw response, cookies or body retained.
    return {'kind':'formId6_popup_lookup','popup_id':value['popupId'],
            'response_sha':hashlib.sha256(raw).hexdigest()}
