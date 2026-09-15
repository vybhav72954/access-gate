import os

import pytest

os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crew.tools import Tools                      # noqa: E402
from generate.fixtures import build_scenarios     # noqa: E402


@pytest.fixture(scope="session")
def tools():
    return Tools()


@pytest.fixture(scope="session")
def world(tools):
    scenarios, store = build_scenarios(tools)
    return {s.sid: s for s in scenarios}, store
