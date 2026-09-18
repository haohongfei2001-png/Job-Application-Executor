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
