"""Explicit local profile selection; separate versions, no task/secret migration."""
from __future__ import annotations

import json
import os
import secrets
import stat

from .. import settings

PROFILE_LIMIT = 256 * 1024


def profile_setup_state():
    current = settings.load_settings()
    return {"settings_version": settings.settings_version(current),
            "profile_selected": bool(current.get("profile_path")),
            "submit_capability": False}


def _validate_profile(profile):
    from ..field_classifier import AUTO_RULES
    from ..models import ApplicantProfile
    from ..profile import DEFAULT_ALIASES

    # Validate the reader's actual envelope, including provenance and assets.
    # Keep the original object below: model defaults/coercion must not rewrite
    # facts, confirmation flags, or extension metadata during an import.
    if profile.get("schema_version", "1.0") != "1.0":
        raise ValueError("invalid_profile")
    ApplicantProfile.model_validate(profile, strict=True)
    if "fields" in profile:
        if any(not key.strip() for key in profile["fields"]):
            raise ValueError("invalid_profile")
        return

    # The generic reader also accepts the documented nested legacy candidate
    # form. Require a recognized leaf, not just an arbitrary JSON object, while
    # retaining additional legacy facts and unknown evidence without migration.
    paths = set(DEFAULT_ALIASES) | {rule.key for rule in AUTO_RULES}
    paths.update({"identity.household_before_gaokao", "identity.household_address"})
    paths.update(key.replace("policy.", "application_policy.", 1)
                 for key in DEFAULT_ALIASES if key.startswith("policy."))
    found = False
    for path in paths:
        current = profile
        for part in path.split("."):
            if not isinstance(current, dict):
                raise ValueError("invalid_profile")
            if part not in current:
                break
            current = current[part]
        else:
            if isinstance(current, dict):
                raise ValueError("invalid_profile")
            found = True
    if not found:
        raise ValueError("invalid_profile")


def _profile_bytes(text):
    from ..profile import CREDENTIAL_KEYS, _walk_keys

    if not isinstance(text, str) or len(text.encode("utf-8")) > PROFILE_LIMIT:
        raise ValueError("invalid_profile")
    profile = json.loads(text, object_pairs_hook=settings._object,
                         parse_constant=settings._constant)
    if not isinstance(profile, dict) or not profile:
        raise ValueError("invalid_profile")
    forbidden = CREDENTIAL_KEYS | {"api_key", "access_token", "refresh_token", "deepseek"}
    if any(key.strip().lower().split(".")[-1] in forbidden for key in _walk_keys(profile)):
        raise ValueError("invalid_profile")
    _validate_profile(profile)
    encoded = json.dumps(profile, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(encoded) > PROFILE_LIMIT:
        raise ValueError("invalid_profile")
    return encoded


def _publish_profile(parent, encoded):
    info = os.fstat(parent)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("invalid_profile")
    # A fresh authority for this explicit selection. Existing confirmed-fact
    # promotion keeps its established writer/fences; never overwrite its file.
    name = "profile-" + secrets.token_hex(16) + ".json"
    created = False
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        created = True
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent)
        return name
    except Exception:
        if created:
            os.unlink(name, dir_fd=parent)
        raise


def select_profile(text, expected_version):
    # Validate the complete file before creating any private authority.
    try:
        encoded = _profile_bytes(text)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("invalid_profile") from None

    def publish(parent, current):
        name = _publish_profile(parent, encoded)
        # Preserve credentials, unknown preferences and existing task profile refs.
        # A settings publication fault can retain an inactive private version.
        return {**current, "profile_path": str(settings.PATH.parent / name)}

    return settings.change_settings(expected_version, publish)
