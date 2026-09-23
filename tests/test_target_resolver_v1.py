from __future__ import annotations

from executor.adapters.generic_web import GenericWebAdapter
from executor.target_resolver import (
    OPPO_CAMPUS_ROOT,
    _collect_oppo_candidates,
    _oppo_candidate_from_snapshot,
    is_oppo_campus_landing,
)


def test_oppo_landing_detection_is_exact():
    assert is_oppo_campus_landing(OPPO_CAMPUS_ROOT)
    assert is_oppo_campus_landing(OPPO_CAMPUS_ROOT + "/")
    assert not is_oppo_campus_landing(OPPO_CAMPUS_ROOT + "/post/1665")
    assert not is_oppo_campus_landing("https://example.com/university/oppo/campus")


def test_oppo_candidate_parser_normalizes_title_spacing():
    candidate = _oppo_candidate_from_snapshot(
        "/university/oppo/campus/post/2048?recruitType=Campus",
        "AI 产品经理\n北京市\n产品类\n2027届校园招聘",
        "AI产品经理",
    )

    assert candidate is not None
    assert candidate.job_id == "2048"
    assert candidate.title == "AI 产品经理"
    assert candidate.location == "北京市"
    assert candidate.category == "产品类"
    assert candidate.exact_title is True
    assert candidate.job_url == (
        "https://careers.oppo.com/university/oppo/campus/post/2048"
        "?recruitType=Campus"
    )


def test_oppo_candidate_parser_rejects_non_job_links():
    assert _oppo_candidate_from_snapshot(
        "/university/oppo/campus",
        "AI产品经理",
        "AI产品经理",
    ) is None


def test_collect_oppo_candidates_finds_exact_job_from_rendered_cards(tmp_path):
    html = tmp_path / "oppo-campus.html"
    html.write_text(
        '''<!doctype html><meta charset="utf-8"><body>
        <section>
          <a href="https://careers.oppo.com/university/oppo/campus/post/1001?recruitType=Campus">
            <div>产品经理</div><div>深圳市</div><div>产品类</div>
          </a>
        </section>
        <section>
          <a href="/university/oppo/campus/post/1002?recruitType=Campus">
            <div>AI 产品经理</div><div>北京市</div><div>产品类</div>
          </a>
        </section>
        </body>''',
        encoding="utf-8",
    )

    with GenericWebAdapter(html.as_uri()) as adapter:
        items = _collect_oppo_candidates(adapter.page, "AI产品经理")

    assert [item.job_id for item in items] == ["1002", "1001"]
    assert items[0].exact_title is True
    assert items[0].title == "AI 产品经理"
