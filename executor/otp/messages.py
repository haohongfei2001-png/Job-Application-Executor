from __future__ import annotations
import re,sqlite3,time
from pathlib import Path
from urllib.parse import quote

APPLE_EPOCH=978307200
OTP_RE=re.compile(r'(?<!\d)(\d{4,8})(?!\d)')

def _apple_ns_to_unix(value):
    try:
        v=int(value)
        seconds=v/1_000_000_000 if abs(v)>10_000_000_000 else v
        return seconds+APPLE_EPOCH
    except Exception: return 0

def find_recent_sms_code(db_path=None,window_seconds=180,sender_hint=None,body_keyword=None,now=None,not_before=None):
    db=Path(db_path or '~/Library/Messages/chat.db').expanduser().resolve()
    if not db.exists(): return None
    now=time.time() if now is None else now
    uri='file:'+quote(str(db))+'?mode=ro'
    con=sqlite3.connect(uri,uri=True)
    try:
        rows=con.execute('''SELECT m.text,m.date,COALESCE(h.id,'') FROM message m LEFT JOIN handle h ON m.handle_id=h.ROWID WHERE m.text IS NOT NULL ORDER BY m.date DESC LIMIT 80''').fetchall()
    finally: con.close()
    hits=[]
    for body,date,sender in rows:
        ts=_apple_ns_to_unix(date)
        if not ts or ts > now or now-ts > window_seconds: continue
        if not_before is not None and ts < not_before: continue
        if sender_hint and sender_hint.lower() not in str(sender).lower(): continue
        if body_keyword and body_keyword.lower() not in str(body).lower(): continue
        codes=OTP_RE.findall(str(body))
        for code in codes: hits.append((code,str(sender),ts))
    unique={x[0] for x in hits}
    if len(unique)!=1: return None
    code=next(iter(unique)); hit=next(x for x in hits if x[0]==code)
    return {'code':code,'sender':hit[1],'age_seconds':max(0,int(now-hit[2])) if hit[2] else None}
