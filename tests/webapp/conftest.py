"""Shared fixtures for the webapp test suite.

``webapp/app.py`` calls ``load_dotenv`` at import time, so importing it from a
test process (as many blueprint tests do) merges the developer's local
``webapp/.env`` into ``os.environ`` for the whole pytest worker.  That breaks
tests that assert library defaults (e.g. the NPC autonomous-loop and
background-LLM toggles defaulting to enabled, TTS defaults, provider defaults)
whenever such a test runs in a worker after an ``webapp.app`` import.

We snapshot the shell environment when this conftest is imported — which
happens before pytest imports any test module — and restore it around every
test, so the suite's behaviour is independent of a local ``.env``.
"""

import os

import pytest

# Snapshot taken at conftest import time: the shell environment, before any
# test module has imported webapp.app (and thus run load_dotenv).
_ENV_SNAPSHOT = dict(os.environ)


@pytest.fixture(autouse=True)
def _isolate_environ(monkeypatch):
    """Run each test with the shell environment, not a .env-polluted one."""
    for key, value in _ENV_SNAPSHOT.items():
        monkeypatch.setenv(key, value)
    for key in list(os.environ):
        if key not in _ENV_SNAPSHOT and not key.startswith('PYTEST'):
            monkeypatch.delenv(key, raising=False)
    yield
