"""Which model runs the crew.

    python -m crew.run --scenarios --llm --provider gemini   # development and the demo (free tier)
    python -m crew.run --scenarios --llm --provider claude   # the final presentation

`LLM_PROVIDER` in .env sets the default. Both adapters honour the same contract,
so nothing outside this module and the adapters knows which one is running.
"""
from __future__ import annotations

import os
from typing import Any

PROVIDERS = ("claude", "gemini")
KEYS = {"claude": ("ANTHROPIC_API_KEY",), "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


def default_provider() -> str:
    return (os.environ.get("LLM_PROVIDER") or "claude").strip().lower()


def make_llm(provider: str, client: Any = None):
    if provider == "claude":
        from crew.llm import ClaudeLLM
        llm = ClaudeLLM()
    elif provider == "gemini":
        from crew.gemini import GeminiLLM
        llm = GeminiLLM()
    else:
        raise ValueError(f"unknown provider {provider!r}; choose one of {', '.join(PROVIDERS)}")
    return llm.with_client(client) if client is not None else llm


def missing_key(provider: str) -> str | None:
    """The environment variable to set, or None when a key is present."""
    return None if any(os.environ.get(k) for k in KEYS[provider]) else KEYS[provider][0]
