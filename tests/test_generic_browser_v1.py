import json

from executor.application import ApplicationExecutor
from executor.models import ApplicationStage, ResolutionStatus


def test_generic_browser_fills_known_fields_and_stops_before_submit(tmp_path, monkeypatch):
    html = tmp_path / "application.html"
    html.write_text('''<!doctype html><meta charset="utf-8"><body>
      <label>姓名 <input id="name" name="full_name" required value="Stale Wrong Name"></label>
      <label>邮箱 <input id="email" name="email" type="email" required></label>
      <label>简历 <input id="resume" type="file" required></label>
      <button id="submit">Submit application</button>
    </body>''', encoding="utf-8")
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n%fixture\n")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "fields": {
            "identity.full_name": {"value": "Example User", "confidence": 1.0},
            "identity.email": {"value": "example@example.test", "confidence": 1.0},
        },
        "assets": {
            "resume": {"path": str(resume), "kind": "resume_pdf"}
        }
    }), encoding="utf-8")
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    runner = ApplicationExecutor(
        html.as_uri(),
        profile,
        {"deepseek": {"enabled": False}},
    )
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    resolved = {
        item.canonical_key
        for item in plan.fields
        if item.status == ResolutionStatus.RESOLVED
    }
    assert "identity.full_name" in resolved
    assert "identity.email" in resolved
    assert "assets.resume" in resolved
    assert not [x for x in plan.unresolved_fields if x.required]
