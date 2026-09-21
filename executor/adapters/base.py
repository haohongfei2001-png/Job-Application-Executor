from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse


class SiteAdapter(ABC):
    site_id = "base"

    def __init__(self, target_url: str):
        self.target_url = target_url

    @classmethod
    @abstractmethod
    def can_handle(cls, target_url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def open(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    @abstractmethod
    def discover_fields(self):
        raise NotImplementedError

    @abstractmethod
    def apply_resolutions(self, resolutions):
        raise NotImplementedError

    @abstractmethod
    def validate(self, plan):
        raise NotImplementedError

    def auth_challenge(self) -> bool:
        return False

    def auth_challenge_kind(self) -> str | None:
        return "other" if self.auth_challenge() else None

    def current_page_hostname(self) -> str:
        return urlparse(self.target_url).hostname or ""

    def prepare_one_time_code_auth(
        self,
        phone: str | None,
        *,
        allow_standard_auth_terms: bool = False,
    ) -> str:
        """Prepare an already-visible SMS OTP login flow without exposing secrets.

        Return one of: not_needed, requested, already_requested, phone_required,
        consent_required, or ambiguous.
        """
        return "not_needed"

    def enter_one_time_code(self, code: str) -> bool:
        return False

    def confirm_one_time_code_auth(self) -> bool:
        """Click one provably scoped OTP-auth confirmation control, if present."""
        return False

    def otp_field_status(self) -> str:
        return "unavailable"

    def start_application(self) -> bool:
        return False

    def save_draft(self) -> bool:
        return False

    @abstractmethod
    def advance(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def final_submit_control(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def submit(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def verify_submission(self):
        raise NotImplementedError

    def screenshot(self, path: str) -> None:
        raise NotImplementedError
