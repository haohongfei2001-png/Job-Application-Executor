from __future__ import annotations
import json
from pathlib import Path
ROOT=Path.home()/"Job-Application-Executor"; PATH=ROOT/"config/settings.json"
def load_settings():
    return json.loads(PATH.read_text(encoding='utf-8')) if PATH.exists() else {'resume_path':None,'profile_path':None,'auth_wait_seconds':900}
def save_settings(data): PATH.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
