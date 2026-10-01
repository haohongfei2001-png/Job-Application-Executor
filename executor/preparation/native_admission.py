"""Runtime identity admission for the separate private preparation surface.

Each owned browser proves its native launch and synthetic loopback behavior.
The result stays bound to that exact live process, never a settings flag,
persisted credential, version-name guess or transferable approval. Read-only
preflight remains available independently.
"""
from __future__ import annotations

import importlib.metadata
import platform
import re
import sys
from pathlib import Path

from .native_selfcheck import check_owned_native_browser

from .session import DisposablePreparationSession, require_private_transport_environment


# The driver version is pinned by requirements.txt and the tested release.
SUPPORTED_PLAYWRIGHT = '1.63.0'


class NativeAdmissionUnavailable(RuntimeError):
    def __init__(self): super().__init__('native_preparation_runtime_not_admitted')


class NativePreparationAdmission:
    """Owned-session check on its owner thread; never launches or changes an OS."""
    def __init__(self, *, still_authorized=lambda:True):
        self._authorized=still_authorized
        self._owner = None
        self._identity = None
        self._target = None
        self._receipt = None

    def __repr__(self): return '<NativePreparationAdmission>'

    @staticmethod
    def available():
        try:
            require_private_transport_environment()
            return (sys.platform=='darwin' and platform.machine()=='arm64'
                    and importlib.metadata.version('playwright')==SUPPORTED_PLAYWRIGHT
                    and Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').is_file())
        except Exception:
            return False

    def admit(self, owner):
        try:
            require_private_transport_environment()
            if self._authorized() is not True:return False
            if (type(owner) is not DisposablePreparationSession or owner.headless is not False
                    or owner.channel != 'chrome' or owner._close_attempted
                    or owner.browser is None or owner.context is None or owner.identity is None
                    or owner.proxy is None or owner.transport is None):
                return False
            target = (sys.platform, platform.machine(), importlib.metadata.version('playwright'),
                      owner.browser.version, owner.channel)
            if (sys.platform != 'darwin' or target[1]!='arm64' or target[2]!=SUPPORTED_PLAYWRIGHT
                    or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}', target[3])):
                return False
            if owner.proxy.verify_launch(owner.browser) is not True:
                return False
            identity = owner.identity.verify(owner.browser)
            if self._owner is None:
                receipt=check_owned_native_browser(owner,still_authorized=self._authorized)
                if (receipt.get('process_sha')!=identity or receipt.get('browser_version')!=target[3]
                        or owner.identity.verify(owner.browser)!=identity or self._authorized() is not True):return False
                self._owner, self._identity, self._target = owner, identity, target
                self._receipt=receipt
            return (self._authorized() is True and self._owner is owner and self._identity == identity
                    and self._target == target)
        except Exception:
            return False
