from pathlib import Path
import json
from executor.engine import Executor
from executor.state import RunState

def test_browser_fill_and_stop_before_submit(tmp_path, monkeypatch):
    html=tmp_path/'form.html'
    html.write_text('''<!doctype html><meta charset="utf-8"><body>
    <section id="p1">
      <label>姓名 <input id="name" name="full_name" required></label>
      <label>邮箱 <input id="email" type="email" autocomplete="email" required></label>
      <label>简历 <input id="resume" type="file" required></label>
      <button id="next" onclick="p1.hidden=true;p2.hidden=false">下一步</button>
    </section>
    <section id="p2" hidden>
      <p>Review application</p><button id="submit">Submit application</button>
    </section>
    </body>''',encoding='utf-8')
    resume=tmp_path/'resume.pdf'; resume.write_bytes(b'%PDF-1.4\n%fake test\n')
    profile=tmp_path/'profile.json'; profile.write_text(json.dumps({'identity':{'full_name':'Test User','email':'test@example.com'}}),encoding='utf-8')
    monkeypatch.setattr('executor.engine.save_runtime', lambda state: None)
    e=Executor(html.as_uri(),str(resume),str(profile)); state=e.run(max_pages=3)
    assert state.state==RunState.WAITING_USER_CONFIRMATION
    assert state.final_submit_detected is True
    keys={x.get('key') for x in state.filled_fields}
    assert 'identity.full_name' in keys and 'identity.email' in keys
    assert state.page_index==1

def test_legacy_submit_helper_cannot_click_final_submit():
    import pytest
    from executor.submit import submit_current

    with pytest.raises(RuntimeError, match="must be clicked by the user"):
        submit_current("FINAL_SUBMIT")
