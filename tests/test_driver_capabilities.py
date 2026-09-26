from __future__ import annotations

from executor.adapters.generic_web import GenericWebAdapter
from executor.adapters.registry import adapter_for_url, declared_driver_capabilities


def test_registered_routes_disclose_the_actual_driver_and_uncertified_paths():
    for url, route in (
        ("https://example.com/apply", "generic_web"),
        ("https://xiaoyuan.zhipin.com/apply", "boss_campus"),
    ):
        adapter = adapter_for_url(url)
        assert type(adapter) is GenericWebAdapter
        declared = declared_driver_capabilities(url)
        assert declared["route"] == route
        assert declared["production_adapter"] == adapter.site_id == "generic_web"
        assert declared["read_only_form_observation"] is True
        assert declared["final_submit_actor"] == "user"
        assert all(value is False for key, value in declared.items()
                   if key.startswith("certified_"))


def test_route_label_does_not_grant_page_advance_or_row_replay():
    declared = declared_driver_capabilities("https://xiaoyuan.zhipin.com/apply")
    assert declared["certified_page_advance"] is False
    assert declared["certified_repeated_rows"] is False
    assert declared["certified_draft_readback"] is False
