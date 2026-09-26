import pytest


@pytest.fixture(autouse=True)
def keep_tests_offline(monkeypatch):
    """Never let a developer's local .env make tests call a paid model API."""
    monkeypatch.setenv("CFD_MEMO_MODEL_PROVIDER", "rules")
