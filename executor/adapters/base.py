from __future__ import annotations

from abc import ABC, abstractmethod


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
