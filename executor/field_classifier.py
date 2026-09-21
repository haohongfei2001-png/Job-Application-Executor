from __future__ import annotations
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Rule:
    key: str; patterns: tuple[str,...]

AUTO_RULES=(
 Rule("identity.email",(r"\be-?mail\b",r"邮箱",r"电子邮件")),
 Rule("identity.phone",(r"\bphone\b",r"mobile",r"tel",r"手机",r"电话")),
 Rule("identity.full_name",(r"full.?name",r"姓名",r"name.?zh",r"中文名")),
 Rule("identity.first_name",(r"first.?name",r"given.?name",r"名(?!称)")),
 Rule("identity.last_name",(r"last.?name",r"family.?name",r"surname",r"姓")),
 Rule("identity.location",(r"current.?location",r"location",r"所在地",r"现居",r"居住地")),
 Rule("education.school",(r"school",r"university",r"college",r"学校",r"院校",r"大学")),
 Rule("education.degree",(r"degree",r"学历",r"学位")),
 Rule("education.major",(r"major",r"专业")),
 Rule("education.graduation_date",(r"graduat",r"毕业时间",r"毕业日期",r"毕业年份")),
 Rule("education.gpa",(r"\bgpa\b",r"绩点")),
 Rule("links.linkedin",(r"linkedin",)), Rule("links.github",(r"github",)),
 Rule("links.website",(r"website",r"portfolio",r"个人网站",r"作品集")),
 Rule("application_defaults.preferred_city",(r"preferred.?city",r"preferred.?location",r"意向城市",r"期望城市",r"工作地点")),
)
MANUAL_PATTERNS=(
 r"salary|薪资|薪酬|期望年薪|期望月薪", r"relocat|调剂|服从分配|异地",
 r"visa|work.?authori|sponsor|签证|工作许可", r"gender|sex\b|race|ethnic|disabil|veteran|性别|民族|残疾",
 r"non.?compete|竞业", r"criminal|background.?check|犯罪|背景调查", r"travel|出差|overtime|加班",
 r"signature|electronic.?sign|电子签名|签名", r"truth|accurate|certif|声明|承诺|真实有效",
)

def classify(hint: str,input_type: str="text",autocomplete: str=""):
    h=" ".join([str(hint or ""),str(autocomplete or ""),str(input_type or "")]).lower()
    if input_type=="file" or re.search(r"resume|cv|简历|附件",h,re.I): return "AUTO_UPLOAD","resume","resume/file field"
    for pat in MANUAL_PATTERNS:
        if re.search(pat,h,re.I): return "ASK_USER",None,f"manual-decision pattern: {pat}"
    ac=autocomplete.lower()
    ac_map={"email":"identity.email","tel":"identity.phone","name":"identity.full_name","given-name":"identity.first_name","family-name":"identity.last_name"}
    if ac in ac_map: return "AUTO_FILL",ac_map[ac],f"autocomplete={ac}"
    for rule in AUTO_RULES:
        if any(re.search(p,h,re.I) for p in rule.patterns): return "AUTO_FILL",rule.key,"deterministic label/name rule"
    return "UNKNOWN",None,"no deterministic rule"

FINAL_SUBMIT_PATTERNS=(r"^submit$",r"submit application",r"complete application",r"final submit",r"提交",r"立即投递",r"确认投递",r"正式投递",r"确认.*(?:更新|申请|报名)",r"(?:confirm|final).*(?:update|application)")
NEXT_PATTERNS=(r"^next$",r"continue",r"save and continue",r"下一步",r"继续",r"保存并继续")
APPLY_PATTERNS=(r"apply now",r"start application",r"开始申请",r"申请职位",r"立即申请")

def _clean(text): return " ".join((text or "").split()).lower()
def is_final_submit(text): return any(re.search(p,_clean(text),re.I) for p in FINAL_SUBMIT_PATTERNS)
def is_next(text): return any(re.search(p,_clean(text),re.I) for p in NEXT_PATTERNS) and not is_final_submit(text)
def is_initial_apply(text): return any(re.search(p,_clean(text),re.I) for p in APPLY_PATTERNS)
