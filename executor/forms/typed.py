"""Typed form representations that refuse implicit applicant commitments."""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation


SALARY_UNITS = {"yuan": Decimal(1), "ten_thousand_yuan": Decimal(10000)}


def represent_scoped_salary(value, canonical: dict, site: dict,
                            target_url: str | None) -> str | None:
    """Convert only a confirmed amount, target, currency, period and tax basis.

    The source and site units are explicit. No annual/monthly, currency or
    gross/net conversion is inferred from a field label.
    """
    if not target_url or not isinstance(canonical, dict) or not isinstance(site, dict):
        return None
    if canonical.get("target_sha256") != hashlib.sha256(target_url.encode()).hexdigest():
        return None
    for key in ("currency", "period", "tax_basis"):
        if not canonical.get(key) or canonical.get(key) != site.get(key):
            return None
    source_unit = SALARY_UNITS.get(canonical.get("unit"))
    site_unit = SALARY_UNITS.get(site.get("unit"))
    if source_unit is None or site_unit is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        step = Decimal(str(site.get("step", "1")))
        represented = amount * source_unit / site_unit
        if (not amount.is_finite() or amount <= 0 or not step.is_finite()
                or step <= 0 or represented % step != 0):
            return None
        for bound, predicate in (("min", lambda x: represented < x),
                                 ("max", lambda x: represented > x)):
            if site.get(bound) is not None and predicate(Decimal(str(site[bound]))):
                return None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None
    return format(represented.normalize(), "f")
