"""Exact public script/stylesheet DOM URLs observed in the empty public page.

Scripts: independent capture2026-10-01T08:40:15Z; styles: read-only dot cloud
browser observation2026-10-01T10:21Z. No resource here grants a form endpoint,
applicant upload or final request. Runtime hash completeness is still a gate.
"""
OBSERVED_STATIC_RESOURCES = frozenset({
    'https://1-ss-sys.huaweicloudsite.cn/js/comm/SWFUpload2v/jquery.uploadify.min.js?v=202307101206',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/city2.min.js?v=202407171154',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/svg.min.js?v=202506121459',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/imageEffect.min.js?v=202506121459',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/photoSlide.min.js?v=202407171154',
    'https://1-ss-sys.huaweicloudsite.cn/js/comm/vue/vue-2.7.14.min.js?v=202310161432',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/comMethods.min.js?v=202407171154',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/jzUtils.min.js?v=202506121459',
    'https://1-ss-sys.huaweicloudsite.cn/js/comm/polyfill.min.js?v=202307101206',
    'https://jzfe-sys.huaweicloudsite.cn/dist/jz/request/jzRequest.min.js?v=202506121719',
    'https://jzfe-sys.huaweicloudsite.cn/dist/jz/utils/jzUtils.min.js?v=202506121754',
    'https://jzfe-sys.huaweicloudsite.cn/dist/jz/biz-shared/bizShared.min.js?v=202608251625',
    'https://1-ss-sys.huaweicloudsite.cn/js/comm/jquery/jquery-core.min.js?v=202307101206',
    'https://1-ss-sys.huaweicloudsite.cn/js/comm/jquery/jquery-mousewheel.min.js?v=202307101206',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/frontend.min.js?v=202506121459',
    'https://1-ss-sys.huaweicloudsite.cn/js/comm/jquery/jquery-ui-core.min.js?v=202307101206',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/partitionSite.min.js?v=202511071120',
    'https://jzfe-sys.huaweicloudsite.cn/dist/jz/locale/2052.min.js?v=202607211512',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/site.min.js?v=202506121459',
    'https://1-ss-sys.huaweicloudsite.cn/js/dist/module.min.js?v=202506121459',
    'https://2-ss-sys.huaweicloudsite.cn/css/dist/styles/siteBase2.min.css?v=202506121459',
    'https://2-ss-sys.huaweicloudsite.cn/css/dist/styles/base2.min.css?v=202506121459',
    'https://2-ss-sys.huaweicloudsite.cn/css/dist/module.min.css?v=202506121459',
    'https://jzs-sys.huaweicloudsite.cn/822/fkTheme.min.css?v=20210928182422&aid=50002009&wid=0&isNavV2=true&isBannerV2=true',
    'https://2-ss-sys.huaweicloudsite.cn/css/dist/styles/themeMixin.min.css?v=202312121718',
    'https://jzs-sys.huaweicloudsite.cn/3205/fkNav.min.css?v=20210928182422&aid=50002009&wid=0',
    'https://2-ss-sys.huaweicloudsite.cn/css/dist/styles/newSearchBoxStyle.min.css?v=202407171154',
    'https://2-ss-sys.huaweicloudsite.cn/css/dist/styles/floatBtn1.min.css?v=202506121459',
    'https://www.qiyunfang.com/jzcusstyle.jsp?id=124&colId=124&extId=0&_csw=0&clientSupportWebp=true',
    'https://jzfe-sys.huaweicloudsite.cn/dist/jz/biz-shared/bizShared.min.css?v=202608251625',
    'https://2-ss-sys.huaweicloudsite.cn/css/dist/styles/datepicker.min.css?v=202407171154',
    'https://2-ss-sys.huaweicloudsite.cn/css/dist/styles/fontsIco.min.css?v=202506121459',
})

# Independently fetched matching bytes on both platforms, research run36860470239.
# Version pins are not a claim that every vendor function was audited.
OBSERVED_SCRIPT_DIGESTS = {'https://1-ss-sys.huaweicloudsite.cn/js/comm/jquery/jquery-core.min.js?v=202307101206': '38f1125b3cc096838e19d54e1bbf6578a8317e886adb2800c420a035206a7b35',
 'https://1-ss-sys.huaweicloudsite.cn/js/comm/jquery/jquery-mousewheel.min.js?v=202307101206': '54dec2ba8994cc6d2390fc57f26a0a4646b636500e8ed230f83fbaf92c7454ff',
 'https://1-ss-sys.huaweicloudsite.cn/js/comm/jquery/jquery-ui-core.min.js?v=202307101206': 'f58942d0d35162da9636fcea892fba1de9b1be791ece58c6956bd842736e2e57',
 'https://1-ss-sys.huaweicloudsite.cn/js/comm/polyfill.min.js?v=202307101206': '2927614f719f25935568a8e0dd2b8de5fd1b0e7ef01b0bd987bac5d7264c159f',
 'https://1-ss-sys.huaweicloudsite.cn/js/comm/vue/vue-2.7.14.min.js?v=202310161432': 'd4209379bfc91ad4caa5b491ec8eba9f07314e00ced0bcec7d96d6a9d6ec7136',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/frontend.min.js?v=202506121459': '7e4433d8a9288352b5b143702875fa482aefb19adb674dc14e9b33b11907cfcc',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/city2.min.js?v=202407171154': 'e6701c3acfade5f170f8151e96844142446f4774c6749ab9588dfad0986a0f60',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/comMethods.min.js?v=202407171154': '9f180b14ce17e32e5bff47d49578dbedfb78cdf4452e326ca0a9659c3e571651',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/imageEffect.min.js?v=202506121459': '1ec26e1c592449966c9f2cee278ddf2d7065d94847154595c07a38bd96866606',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/jzUtils.min.js?v=202506121459': '56d85c77f8a38e321b4adf704861698479a5a3321af00fc2bb50904d965f3048',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/libs/photoSlide.min.js?v=202407171154': 'acb434b33a088c400d80308eba03a70e71c09d532d489c4a8e73c1945dea895d',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/module.min.js?v=202506121459': '0050b03259f7044c5666b42a3075dabf9607c8d179eac070798a894997132031',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/partitionSite.min.js?v=202511071120': '400d6555c37e246737952ca0f9c948b813abd9ad16e8444fa8409b1e81d13651',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/site.min.js?v=202506121459': 'd9e0a901236460425c9cef8a16bc06614b9336347f0384c420d840162b630da3',
 'https://1-ss-sys.huaweicloudsite.cn/js/dist/svg.min.js?v=202506121459': 'dc73930367cbc698ec8faaec9dd534889f029298d7986fa4743f0c3b49f15bd4',
 'https://jzfe-sys.huaweicloudsite.cn/dist/jz/biz-shared/bizShared.min.js?v=202608251625': '601a5b40bc7f891b9eaa74f11086877b5423d4de5a477ab16bb4563003ed7942',
 'https://jzfe-sys.huaweicloudsite.cn/dist/jz/locale/2052.min.js?v=202607211512': 'da2ca3295fd1cf2e32354878c2918d1e3998c52274aa3538f7d4884e95eb0f59',
 'https://jzfe-sys.huaweicloudsite.cn/dist/jz/request/jzRequest.min.js?v=202506121719': '2152b73f3bf4267bd49f391e77a536e3dbdebc8a4f464b92ce3e01134499a60d',
 'https://jzfe-sys.huaweicloudsite.cn/dist/jz/utils/jzUtils.min.js?v=202506121754': 'a85273cdcf54a9bfcf9e24667f2691c21c41cd80b4a23ee40ceaac43e078e442'}
# Popup upload-widget initialization source observed read-only; no upload grant.
OBSERVED_SCRIPT_DIGESTS['https://1-ss-sys.huaweicloudsite.cn/js/comm/SWFUpload2v/jquery.uploadify.min.js?v=202307101206'] = 'aa8443a2e1f53f8cf4deb2471d5796dde9e5e67ed6de487cb7ee3284f07d7383'
