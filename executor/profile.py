from __future__ import annotations
import json
from pathlib import Path
from typing import Any

SENSITIVE_KEYS={"password","otp","验证码","token","secret"}

def load_profile(path: str|Path) -> dict[str,Any]:
    p=Path(path).expanduser().resolve()
    data=json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data,dict): raise ValueError("candidate profile must be a JSON object")
    for key in _walk_keys(data):
        if key.lower() in SENSITIVE_KEYS: raise ValueError(f"credential-like key is not allowed in candidate profile: {key}")
    return data

def _walk_keys(value):
    if isinstance(value,dict):
        for k,v in value.items():
            yield str(k); yield from _walk_keys(v)
    elif isinstance(value,list):
        for v in value: yield from _walk_keys(v)

def get_value(profile: dict, dotted: str|None):
    if not dotted: return None
    cur=profile
    for part in dotted.split('.'):
        if not isinstance(cur,dict) or part not in cur: return None
        cur=cur[part]
    return None if cur is None or cur=="" else cur

def masked_preview(key: str, value) -> str:
    s=str(value)
    if "email" in key:
        a,_,b=s.partition("@"); return a[:2]+"***@"+b if b else "***"
    if "phone" in key: return "***"+s[-4:] if len(s)>=4 else "***"
    return s[:80]
