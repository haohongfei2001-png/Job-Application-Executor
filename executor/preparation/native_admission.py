"""Runtime identity admission for the separate private preparation surface.

Certificates are release-owned exact tuples, never settings, HTTP flags, model
decisions, or a claim that a nearby browser version passed. The initial empty
set deliberately keeps real filling/upload closed until an exact headed native
target has complete lifecycle/transport evidence. Read-only preflight remains
available independently.
"""
from __future__ import annotations

import importlib.metadata
import platform
import re
import sys

from .session import DisposablePreparationSession, require_private_transport_environment


# (OS, architecture, Playwright version, browser version, channel)
# Do not populate from the user's environment or an unverified CI partial pass.
CERTIFIED_NATIVE_RUNTIMES = frozenset()


class NativeAdmissionUnavailable(RuntimeError):
    def __init__(self): super().__init__('native_preparation_runtime_not_admitted')


class NativePreparationAdmission:
    """Owned-session check on its owner thread; never launches or changes an OS."""
    def __init__(self):
        self._owner = None
        self._identity = None
        self._target = None

    def __repr__(self): return '<NativePreparationAdmission>'

    @staticmethod
    def available():
        try:
            require_private_transport_environment()
            key = (sys.platform, platform.machine(), importlib.metadata.version('playwright'))
            return any(target[:3] == key and target[4] == 'chrome'
                       for target in CERTIFIED_NATIVE_RUNTIMES)
        except Exception:
            return False

    def admit(self, owner):
        try:
            require_private_transport_environment()
            if (type(owner) is not DisposablePreparationSession or owner.headless is not False
                    or owner.channel != 'chrome' or owner._close_attempted
                    or owner.browser is None or owner.context is None or owner.identity is None
                    or owner.proxy is None or owner.transport is None):
                return False
            target = (sys.platform, platform.machine(), importlib.metadata.version('playwright'),
                      owner.browser.version, owner.channel)
            if (target not in CERTIFIED_NATIVE_RUNTIMES or sys.platform != 'darwin'
                    or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}', target[3])):
                return False
            if owner.proxy.verify_launch(owner.browser) is not True:
                return False
            identity = owner.identity.verify(owner.browser)
            if self._owner is None:
                self._owner, self._identity, self._target = owner, identity, target
            return (self._owner is owner and self._identity == identity
                    and self._target == target)
        except Exception:
            return False
