from __future__ import annotations

from urllib.parse import urlparse

from .generic_web import GenericWebAdapter
from ..protected_targets import assert_target_not_protected


def site_id_for_url(target_url: str) -> str:
    host = (urlparse(target_url).hostname or "").casefold()
    if host == "xiaoyuan.zhipin.com":
        return "boss_campus"
    return "generic_web"


def adapter_for_url(target_url: str):
    assert_target_not_protected(target_url)
    if GenericWebAdapter.can_handle(target_url):
        return GenericWebAdapter(target_url)
    raise RuntimeError(f"no adapter can handle URL: {target_url}")


def declared_driver_capabilities(target_url: str) -> dict[str, object]:
    """Report the active production driver without implying site certification.

    A route label (including boss_campus) is not a site-specific form driver.
    Keep unproven write/readback paths false until a driver and its independent
    oracle are added together. This is a diagnostic contract, not permission
    to perform a browser action.
    """
    adapter = adapter_for_url(target_url)
    return {
        "route": site_id_for_url(target_url),
        "production_adapter": adapter.site_id,
        "read_only_form_observation": True,
        "certified_draft_readback": False,
        "certified_page_advance": bool(getattr(adapter, "safe_advance_certified", False)),
        "certified_repeated_rows": bool(getattr(adapter, "repeated_rows_certified", False)),
        "certified_attachment_readback": False,
        "certified_iframe_controls": False,
        "certified_shadow_controls": False,
        "certified_virtualized_choices": False,
        "final_submit_actor": "user",
    }
