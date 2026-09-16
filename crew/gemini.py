"""Gemini, through Google's official google-genai SDK, as a CrewAI LLM.

The same contract as crew/llm.py (Claude): the crew, the policy and the tests do
not care which model runs. Use Gemini for development and the demo (free tier),
Claude for the final presentation.

Why not CrewAI's built-in Gemini provider: inspected at crewai 1.15.20, it strips
`null` from structured-output schemas, so an agent could never answer
"inconclusive" (`supports_fraud: null`) -- and inconclusive is exactly what makes
the policy order a field audit (scenario S7).

Safeguards, matching the Claude adapter where Gemini allows:
* structured output via `response_json_schema`, nulls kept, validated by Pydantic
* a safety block falls back ONCE to a backup model -- client-side, because Gemini
  has no server-side fallback -- and still blocked raises GeminiBlocked
* truncation raises GeminiTruncated
* free-tier protection: calls paced to `max_rpm`; 408/429/5xx retried with backoff,
  honouring the server's retryDelay. An overloaded primary (5xx) gets one quick retry
  and a rate-limited one (429) none; then the backup model takes over for the rest of
  the run (seen live: 503 "high demand" on one day, a 429 asking for 43 s on the next)
* safety thresholds set explicitly: case files discuss death and fraud, which the
  default filters can over-block
* native tool calling, CrewAI's protocol: given tools, a function call is returned to
  CrewAI as a list of calls, and CrewAI runs the tools. Gemini 3 rejects a function-call
  turn sent back without its thought signature, and CrewAI rebuilds that turn without
  one, so each function-call turn is kept as Gemini returned it and re-sent verbatim
Every raise lands in crew.run.process, which degrades the case to the rules path.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Callable

from crewai import BaseLLM
from crewai.utilities.pydantic_schema_utils import generate_model_description
from pydantic import BaseModel, PrivateAttr

MODEL = "gemini-3.8-flash"                # stable, free tier (ai.google.dev models + pricing, 2026-09-16)
FALLBACK_MODEL = "gemini-3.5-flash-lite"  # stable, free tier, a different model to fall back to
MAX_RPM = 10
MAX_WAIT_S = 90.0                         # a longer server-requested wait means a spent daily quota: degrade

TOOL_TURNS_KEPT = 2_000                   # function-call turns remembered for re-sending; a run needs a few hundred
TOOL_ERROR = "Error executing tool:"      # how CrewAI reports a tool that raised

RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})
BLOCKED_FINISH = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE", "OTHER"})
MALFORMED_FINISH = frozenset({"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"})
SAFETY_SETTINGS = [
    {"category": c, "threshold": "BLOCK_ONLY_HIGH"}
    for c in ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
              "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
]
# The JSON Schema keywords response_json_schema accepts (google-genai 1.65.0, GenerateContentConfig).
SCHEMA_KEYS = frozenset({"$id", "$defs", "$ref", "$anchor", "type", "format", "title", "description", "enum",
                         "items", "prefixItems", "minItems", "maxItems", "minimum", "maximum", "anyOf", "oneOf",
                         "properties", "additionalProperties", "required", "propertyOrdering"})

log = logging.getLogger("access_gate.gemini")


class GeminiBlocked(RuntimeError):
    """The request was blocked on the primary model and on the fallback."""


class GeminiTruncated(RuntimeError):
    """The response hit max_output_tokens before finishing."""


class GeminiUnavailable(RuntimeError):
    """Rate-limited or unavailable after every retry, or the daily quota is spent."""


class GeminiMalformed(RuntimeError):
    """The model twice failed to form a valid function call."""


def response_schema(model: type[BaseModel]) -> dict:
    """A Pydantic model as the JSON Schema subset Gemini accepts, with nulls preserved."""
    schema = generate_model_description(model, strip_null_types=False)["json_schema"]["schema"]
    return _gemini_schema(schema)


def _gemini_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [_gemini_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in SCHEMA_KEYS:
            continue
        if key in ("properties", "$defs"):                  # keys here are field names, not keywords
            out[key] = {name: _gemini_schema(sub) for name, sub in value.items()}
        else:
            out[key] = _gemini_schema(value)
    options = out.get("anyOf")
    if isinstance(options, list) and len(options) == 2 and {"type": "null"} in options:
        other = next(o for o in options if o != {"type": "null"})
        if set(other) == {"type"} and isinstance(other["type"], str):
            del out["anyOf"]
            out["type"] = [other["type"], "null"]           # the nullable form Gemini documents
    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        out.setdefault("propertyOrdering", list(out["properties"]))
    return out


def function_declarations(tools: Any) -> list[dict]:
    """CrewAI's OpenAI-style tool schemas -> Gemini function declarations (parameters in the subset Gemini accepts)."""
    out = []
    for tool in tools or []:
        fn = tool.get("function", tool) if isinstance(tool, dict) else {}
        if not fn.get("name"):
            continue
        decl: dict[str, Any] = {"name": fn["name"], "description": fn.get("description") or ""}
        if fn.get("parameters"):
            decl["parameters_json_schema"] = _gemini_schema(fn["parameters"])
        out.append(decl)
    return out


def _dump(obj: Any) -> dict:
    """A response Content or Part as the plain dict the request accepts, thought signature included."""
    return obj.model_dump(exclude_none=True) if hasattr(obj, "model_dump") else dict(obj)


def _arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _name(value: Any) -> str | None:
    """SDK enums and plain strings alike."""
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _error_items(exc: Exception) -> list[dict]:
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return []
    body = details.get("error", details)
    items = body.get("details") if isinstance(body, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _daily_quota(exc: Exception) -> str | None:
    """The spent daily quota a 429 names, e.g. '500 requests a day'. Its retryDelay is under a minute (seen live:
    53 s), so the delay alone would have the adapter wait four times a call for a quota that resets at midnight."""
    for item in _error_items(exc):
        for v in item.get("violations") or []:
            if isinstance(v, dict) and "PerDay" in str(v.get("quotaId", "")):
                return f"{v.get('quotaValue', '?')} requests a day"
    return None


def _retry_delay(exc: Exception) -> float | None:
    """The server's RetryInfo.retryDelay ('37s'), when the error carries one."""
    for item in _error_items(exc):
        if str(item.get("@type", "")).endswith("RetryInfo"):
            m = re.fullmatch(r"([\d.]+)s", str(item.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None


class GeminiLLM(BaseLLM):
    llm_type: str = "google-genai-sdk"
    model: str = MODEL
    fallback_model: str | None = FALLBACK_MODEL
    max_tokens: int = 16_000
    max_rpm: int = MAX_RPM
    max_retries: int = 4
    retries_before_fallback: int = 1       # an overloaded primary gets one quick retry, then the backup takes over

    _client: Any = PrivateAttr(default=None)
    _sleep: Callable[[float], None] = PrivateAttr(default=time.sleep)
    _clock: Callable[[], float] = PrivateAttr(default=time.monotonic)
    _last_call: float | None = PrivateAttr(default=None)
    _lock: Any = PrivateAttr(default_factory=threading.Lock)
    _last_response_id: str | None = PrivateAttr(default=None)
    _active_model: str | None = PrivateAttr(default=None)
    # call id -> the function-call turn as Gemini returned it: {"content": dict, "ids": [call id per call, in order],
    # "issued": {call id: whether Gemini issued it}}
    _tool_turns: dict[str, dict] = PrivateAttr(default_factory=dict)
    _spent: set[str] = PrivateAttr(default_factory=set)    # models whose daily quota ran out during this run

    def __init__(self, **data: Any):
        # CrewAI's BaseLLM validates the raw input before field defaults apply.
        data.setdefault("model", (os.environ.get("GEMINI_MODEL") or "").strip() or MODEL)
        if "fallback_model" not in data and "GEMINI_FALLBACK_MODEL" in os.environ:
            data["fallback_model"] = os.environ["GEMINI_FALLBACK_MODEL"].strip() or None
        if "max_rpm" not in data and (rpm := (os.environ.get("GEMINI_MAX_RPM") or "").strip()):
            if not rpm.isdigit():
                raise ValueError(f"GEMINI_MAX_RPM must be a whole number of requests a minute (0 = no pacing), "
                                 f"not {rpm!r}")
            data["max_rpm"] = int(rpm)
        super().__init__(**data)

    def with_client(self, client: Any, sleep: Callable[[float], None] | None = None,
                    clock: Callable[[], float] | None = None) -> "GeminiLLM":
        """Inject a client (tests use a fake; production reads GEMINI_API_KEY) and, for tests, time."""
        self._client = client
        if sleep is not None:
            self._sleep = sleep
        if clock is not None:
            self._clock = clock
        return self

    def _sdk(self) -> Any:
        if self._client is None:
            from google import genai
            self._client = genai.Client()
        return self._client

    @property
    def model_in_use(self) -> str:
        """The model calls go to now: the primary, or the backup once the primary proved unavailable."""
        return self._active_model or self.model

    # CrewAI capability probes, as for Claude: function calling = True is what makes
    # CrewAI pass `response_model` through to call() for tasks with output_pydantic.
    def supports_function_calling(self) -> bool:
        return True

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 1_000_000

    @staticmethod
    def to_gemini(messages: Any, tool_turns: dict[str, dict] | None = None) -> tuple[str | None, list[dict]]:
        """CrewAI's message list -> (system instruction, user/model turns).

        Tool use arrives in CrewAI's OpenAI shape: an assistant message with `tool_calls`, then one `tool` message per
        call executed. The first becomes the model turn Gemini returned (from `tool_turns`, so its thought signature
        survives); the results become one user turn of function responses.
        """
        if isinstance(messages, str):
            return None, [{"role": "user", "parts": [{"text": messages}]}]
        system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system") or None
        turns: list[dict] = []
        issued: dict[str, bool] = {}
        for m in messages:
            role = m.get("role")
            if role == "assistant" and m.get("tool_calls"):
                parts, ids = GeminiLLM._call_parts(m["tool_calls"], tool_turns or {})
                issued.update(ids)
                turns.append({"role": "model", "parts": parts})
                continue
            if role == "tool":
                content = m.get("content")
                content = content if isinstance(content, str) else json.dumps(content, default=str)
                key = "error" if content.startswith(TOOL_ERROR) else "output"
                response: dict[str, Any] = {"name": m.get("name") or "", "response": {key: content}}
                if issued.get(m.get("tool_call_id") or ""):
                    response["id"] = m["tool_call_id"]          # Gemini's own call id is echoed back
                part = {"function_response": response}
                if turns and turns[-1]["role"] == "user" and all("function_response" in p for p in turns[-1]["parts"]):
                    turns[-1]["parts"].append(part)
                else:
                    turns.append({"role": "user", "parts": [part]})
                continue
            role = {"user": "user", "assistant": "model"}.get(role)
            if role is None:
                continue
            text = m.get("content") or ""
            text = text if isinstance(text, str) else str(text)
            if turns and turns[-1]["role"] == role and all(set(p) == {"text"} for p in turns[-1]["parts"]):
                turns[-1]["parts"][-1]["text"] += "\n\n" + text
            else:
                turns.append({"role": role, "parts": [{"text": text}]})
        if not turns or turns[0]["role"] != "user":
            turns.insert(0, {"role": "user", "parts": [{"text": "Begin."}]})
        if turns[-1]["role"] == "model":
            if any("function_call" in p for p in turns[-1]["parts"]):
                raise ValueError("the conversation ends on a function call with no function response")
            turns.append({"role": "user", "parts": [{"text": "Continue."}]})
        return system, turns

    @staticmethod
    def _call_parts(tool_calls: list[dict], tool_turns: dict[str, dict]) -> tuple[list[dict], dict[str, bool]]:
        """The model turn for the calls CrewAI executed: Gemini's own turn when this adapter issued them."""
        ids = [str(tc.get("id") or "") for tc in tool_calls]
        turn = tool_turns.get(ids[0]) if ids else None
        if turn is None or not set(ids) <= set(turn["ids"]):
            # Not a turn this adapter returned (another process, another model): plain calls, no signature.
            return ([{"function_call": {"name": (tc.get("function") or {}).get("name") or tc.get("name") or "",
                                        "args": _arguments((tc.get("function") or {}).get("arguments"))}}
                     for tc in tool_calls], {i: False for i in ids})
        keep, parts, order, carried = set(ids), [], iter(turn["ids"]), None
        for part in turn["content"].get("parts", []):
            if "function_call" in part:
                if next(order) not in keep:
                    # CrewAI runs only the first of several calls in some cases. A call left out must go, or Gemini
                    # finds a call with no response; its signature moves to the next call kept.
                    carried = carried or part.get("thought_signature")
                    continue
                if carried and "thought_signature" not in part:
                    part, carried = {**part, "thought_signature": carried}, None
            parts.append(part)
        return parts, {i: turn["issued"].get(i, False) for i in ids}

    def _function_calls(self, response: Any) -> list[dict]:
        """The response's function calls in CrewAI's shape, remembering the turn they came in."""
        content = getattr(response.candidates[0], "content", None)
        parts = list(getattr(content, "parts", None) or [])
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        if not calls:
            return []
        ids = [fc.id or f"gemini_call_{uuid.uuid4().hex[:16]}" for fc in calls]
        turn = {"content": _dump(content), "ids": ids, "issued": {i: bool(fc.id) for i, fc in zip(ids, calls)}}
        turn["content"]["role"] = "model"
        for i in ids:
            self._tool_turns[i] = turn
        while len(self._tool_turns) > TOOL_TURNS_KEPT:
            self._tool_turns.pop(next(iter(self._tool_turns)))
        return [{"id": i, "name": fc.name, "input": dict(fc.args or {})} for i, fc in zip(ids, calls)]

    def call(self, messages: Any, tools: Any = None, callbacks: Any = None, available_functions: Any = None,
             from_task: Any = None, from_agent: Any = None, response_model: Any = None) -> Any:
        system, contents = self.to_gemini(messages, self._tool_turns)
        config: dict[str, Any] = {"max_output_tokens": self.max_tokens, "safety_settings": SAFETY_SETTINGS}
        if system:
            config["system_instruction"] = system
        declarations = function_declarations(tools)
        if declarations:
            # CrewAI runs the tools and feeds the results back; the SDK must never call anything itself.
            config["tools"] = [{"function_declarations": declarations}]
            config["automatic_function_calling"] = {"disable": True}
        if response_model is not None:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = response_schema(response_model)

        # The model in use first, then the other one, unless its daily quota is known to be spent. The primary is
        # tried again once the backup runs out: seen live, the backup's quota ran out hours after the primary's
        # overload had passed.
        order = [m for m in dict.fromkeys((self.model_in_use, self.fallback_model if self.model_in_use == self.model
                                           else self.model)) if m and m not in self._spent] or [self.model_in_use]
        tried: list[str] = []
        for i, active in enumerate(order):
            more = i + 1 < len(order)
            try:
                # Quota errors are per model: with another model to go to, waiting out a 429 gains nothing (seen
                # live: a 43-second wait before switching), so hand over at once. Overload (5xx) gets a quick retry.
                response = self._generate(active, contents, config,
                                          self.retries_before_fallback if more else self.max_retries,
                                          fail_fast=frozenset({429}) if more else frozenset())
                break
            except GeminiUnavailable as exc:
                tried.append(active)
                if more:
                    # Overloaded or out of quota: switch for the rest of the run rather than paying the retry wait
                    # again on every call.
                    log.warning("%s unavailable (%s); switching to %s for the rest of this run", active, exc,
                                order[i + 1])
                    continue
                # Every model is unavailable. One left only for a short rate limit, not a spent daily quota, gets its
                # full retries before the call gives up.
                waited = next((m for m in tried[:-1] if m not in self._spent), None)
                if waited is None:
                    raise
                log.warning("%s unavailable (%s); back to %s with its full retries", active, exc, waited)
                active = waited
                response = self._generate(active, contents, config, self.max_retries)
        self._active_model = None if active == self.model else active
        backup = next((m for m in (self.model, self.fallback_model) if m and m != active and m not in self._spent),
                      None)

        reason = self._blocked(response)
        if reason and backup:
            log.warning("%s blocked (%s); retrying once on %s", active, reason, backup)
            response = self._generate(backup, contents, config, self.max_retries)
            reason = self._blocked(response)
        if reason:
            raise GeminiBlocked(f"blocked ({reason}) on {active}{' and ' + backup if backup else ''} "
                                f"(response_id={self._last_response_id})")
        if (finish := _name(response.candidates[0].finish_reason)) in MALFORMED_FINISH:
            # A function call the model failed to form is a slip, not a verdict on the request: ask once more.
            log.warning("%s returned %s; asking once more", active, finish)
            response = self._generate(active, contents, config, self.max_retries)
            if self._blocked(response) or _name(response.candidates[0].finish_reason) in MALFORMED_FINISH:
                raise GeminiMalformed(f"{finish} twice on {active} (response_id={self._last_response_id})")
        if _name(response.candidates[0].finish_reason) == "MAX_TOKENS":
            raise GeminiTruncated(f"hit max_output_tokens={self.max_tokens} (response_id={self._last_response_id})")

        if declarations and (calls := self._function_calls(response)):
            return calls                                         # CrewAI executes them and calls again
        text = response.text or ""
        if response_model is not None:
            return response_model.model_validate_json(text)      # invalid output raises -> degraded
        return text

    # ── transport: pacing, retries ────────────────────────────────────────

    def _pace(self) -> None:
        if self.max_rpm <= 0:
            return
        gap = 60.0 / self.max_rpm
        with self._lock:
            now = self._clock()
            if self._last_call is not None and now - self._last_call < gap:
                self._sleep(gap - (now - self._last_call))
                now = self._clock()
            self._last_call = now

    def _generate(self, model: str, contents: list[dict], config: dict, retries: int,
                  fail_fast: frozenset[int] = frozenset()) -> Any:
        from google.genai import errors
        for attempt in range(retries + 1):
            self._pace()
            try:
                response = self._sdk().models.generate_content(model=model, contents=contents, config=config)
                self._last_response_id = getattr(response, "response_id", None)
                return response
            except errors.APIError as exc:
                if exc.code not in RETRYABLE:
                    raise
                wait = _retry_delay(exc)
                if exc.code == 429 and (daily := _daily_quota(exc)):
                    self._spent.add(model)
                    raise GeminiUnavailable(f"{model}: daily free-tier quota spent ({daily}); it resets at midnight "
                                            f"Pacific time") from exc
                if wait is not None and wait > MAX_WAIT_S:
                    self._spent.add(model)
                    raise GeminiUnavailable(f"{model}: {exc.code} {exc.status}; server asks to wait {wait:.0f}s "
                                            f"- quota likely spent") from exc
                if exc.code in fail_fast:
                    raise GeminiUnavailable(f"{model}: {exc.code} {exc.status}; switching without waiting") from exc
                if attempt == retries:
                    raise GeminiUnavailable(f"{model}: {exc.code} {exc.status} after {attempt + 1} attempts") from exc
                wait = wait if wait is not None else min(60.0, 2.0 * 2 ** attempt)
                log.warning("%s: %s %s; retry %d/%d in %.0fs", model, exc.code, exc.status, attempt + 1,
                            retries, wait)
                self._sleep(wait)
        raise AssertionError("unreachable")

    @staticmethod
    def _blocked(response: Any) -> str | None:
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            return f"prompt {_name(feedback.block_reason)}"
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return "no candidates"
        finish = _name(getattr(candidates[0], "finish_reason", None))
        return f"output {finish}" if finish in BLOCKED_FINISH else None
