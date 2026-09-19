import pytest


@pytest.fixture(autouse=True)
def isolate_application_browser(monkeypatch):
    """Tests must never attach to the real Job-Application-Executor Chrome profile."""
    monkeypatch.setenv("APPLICATION_EXECUTOR_BROWSER_MODE", "isolated")
