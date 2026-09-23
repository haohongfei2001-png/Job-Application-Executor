from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteChoice:
    canonical_value: str
    site_value: str
    method: str


def represent_choice(canonical_value, observed_options: list[str],
                     explicit_mapping: dict[str, str] | None = None) -> SiteChoice | None:
    """Represent a fact on one site without rewriting its canonical value."""
    canonical = str(canonical_value).strip()
    options = [str(option).strip() for option in observed_options]
    if not canonical or len(options) != len(set(options)):
        return None
    if canonical in options:
        return SiteChoice(canonical, canonical, "exact_observed_option")
    mapping = explicit_mapping or {}
    if any(not isinstance(key, str) or not isinstance(value, str)
           for key, value in mapping.items()):
        return None
    if len(set(mapping.values())) != len(mapping):
        return None
    represented = mapping.get(canonical)
    if represented not in options:
        return None
    return SiteChoice(canonical, represented, "explicit_reversible_mapping")
