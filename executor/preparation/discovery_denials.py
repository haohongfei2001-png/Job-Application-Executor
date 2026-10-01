"""Finite classification of requests that remain DENIED before applicant data.

This module grants no network permission. Historical classified refusals can be
retained in an empty-form preflight receipt; unknown activity remains a blocker.
Never inspect request bodies or classify away activity after transport sealing.
"""
from urllib.parse import parse_qsl,urlsplit
import re

# Actual public asset attempts in research run36862577094, retained as refusals.
DENIED_PUBLIC_ASSETS = frozenset(['https://0-ss-sys.huaweicloudsite.cn/image/loading/dot.gif',
 'https://2-ss-sys.huaweicloudsite.cn/image/styleSiteForm/siteFormFUDescIcons.png?v=201807251417',
 'https://2-ss-sys.huaweicloudsite.cn/image/v2/vbg01.png?v=201907171253',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg-5-BtgYo2PK9_wMwQDhA!60x60.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg-5-BtgYo2PK9_wMwQDhA!60x60.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg3JbxrgYovKmWhQIw_Ao4kAM.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg3JbxrgYovKmWhQIw_Ao4kAM.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg4NHQrwYooJj9ggMwgA84uAI.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg4NHQrwYooJj9ggMwgA84uAI.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg5J-BtgYovMGjazBAOEA!60x60.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg5J-BtgYovMGjazBAOEA!60x60.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg6cDEtgYoiOPHowMweDh4!100x100.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAg6cDEtgYoiOPHowMweDh4!100x100.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgh8q4uAYoza2mwgEw9wI4XA.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgipDxrgYo4eOvEDDUBzj4Cg.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgipDxrgYo4eOvEDDUBzj4Cg.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgja-LrwYo2ObV_AUwsAk4AQ.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgja-LrwYo2ObV_AUwsAk4AQ.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAglaDBtgYoluac6QYwQDhA!60x60.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAglaDBtgYoluac6QYwQDhA!60x60.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgpKDBtgYo3LHwlQcwQjhA!60x60.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgpKDBtgYo3LHwlQcwQjhA!60x60.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgtqDBtgYog4b65wYwQDhA!60x60.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgtqDBtgYog4b65wYwQDhA!60x60.png.webp',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgxYfLrwYoze-VoQYwNjg7.png',
 'https://50002009.s21i.huaweicloudsite.cn/4/ABUIABAEGAAgxYfLrwYoze-VoQYwNjg7.png.webp'])

QUERY_COMMANDS = {
 ('POST','/ajax/statistics_h.jsp'): {'wafNotCk_visited'},
 ('POST','/ajax/log_h.jsp'): {'wafNotCk_siteLogDog'},
 ('POST','/ajax/logAjaxErr_h.jsp'): {'wafNotCk_ajaxErr'},
 ('GET','/ajax/login_h.jsp'): {'wafNotCk_checkMemberSameTimeLogin'},
 ('GET','/ajax/site_h.jsp'): {'getWafNotCk_getCookiePolicyOpen'},
 ('GET','/ajax/setCookie_h.jsp'): {'setWafCk_setCheckSiteLvBrowser'},
 ('POST','/ajax/siteDomain_h.jsp'): {'wafNotCk_checkFaiDomain'},
}
BODY_COMMANDS = {
 '/ajax/site_h.jsp': {'wafNotCk_checkBaiduAutomaticPush'},
 '/ajax/log_h.jsp': {'wafNotCk_dog','wafNotCk_logFdpForWebVitals'},
 '/ajax/ajaxLoadModuleDom_h.jsp': {'getWafNotCk_loadModuleDom'},
 '/ajax/module_h.jsp': {'getWafNotCk_getHiddenModuleList'},
}


def _pairs(raw,limit):
    pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True,max_num_fields=limit)
    value=dict(pairs)
    if len(value)!=len(pairs):raise ValueError('duplicate selector')
    return value


def expected_discovery_denial(request):
    """Return a constant category, never a URL, body or permission to fetch."""
    try:
        url=request.url;method=request.method
        if not isinstance(url,str) or len(url)>8192:return None
        if method=='GET' and url in DENIED_PUBLIC_ASSETS and request.post_data is None:
            return 'unused_public_asset'
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc!='www.qiyunfang.com' or parsed.fragment:return None
        if method=='GET' and parsed.path=='/validateCode.jsp':
            if re.fullmatch(r'[0-9]{1,3}&vCodeId=15676',parsed.query) and request.post_data is None:
                return 'captcha_not_retrieved'
            return None
        query=_pairs(parsed.query,16) if parsed.query else {}
        allowed=QUERY_COMMANDS.get((method,parsed.path),set())
        if query.get('cmd') in allowed:
            extras={'error','status'} if parsed.path=='/ajax/logAjaxErr_h.jsp' else {'_v'} if parsed.path=='/ajax/site_h.jsp' else set()
            if set(query)-{'cmd'}<=extras:
                body=request.post_data
                if method=='GET' and body is not None:return None
                if body:
                    if not isinstance(body,str) or len(body)>8192:return None
                    data=_pairs(body,64)
                    if 'cmd' in data and data['cmd']!=query['cmd']:return None
                return 'known_background_command_denied'
        if method!='POST' or parsed.path not in BODY_COMMANDS or set(query)-{'_v'}:return None
        body=request.post_data
        if not isinstance(body,str) or len(body)>8192:return None
        data=_pairs(body,64)
        if data.get('cmd') not in BODY_COMMANDS[parsed.path]:return None
        if parsed.path=='/ajax/module_h.jsp' and (data.get('_colId')!='124' or data.get('_manageMode')!='false'):
            return None
        return 'known_background_command_denied'
    except (ValueError,TypeError,AttributeError,UnicodeError):return None
