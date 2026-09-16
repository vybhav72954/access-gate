"""Claude, through the official Anthropic SDK, as a CrewAI LLM.

Why not CrewAI's built-in Anthropic provider: inspected at crewai 1.15.20, it
(a) forces `tool_choice` whenever an agent has exactly one tool, which conflicts
with Claude Opus 5's default-on adaptive thinking, and (b) requests structured
output through the deprecated `output_format` beta. This adapter keeps CrewAI
for agents, tasks and orchestration, and owns the request itself:

* model `claude-opus-5`, adaptive thinking by omission (on by default for Opus 5)
* no `temperature` / `top_p` -- rejected with a 400 on Opus 5
* structured output via `client.beta.messages.parse(output_format=<Pydantic model>)`
* server-side refusal fallbacks ON: `fallbacks="default"` with the
  `server-side-fallback-2026-07-01` beta, routed by refusal category
* refusals and truncation raise, so the runner degrades to the rules path
* native tool calling, CrewAI's protocol: `tool_use` blocks are returned to CrewAI as a
  list of calls and CrewAI runs the tools. With thinking on, an assistant turn that
  used tools must go back with its thinking blocks, which CrewAI's rebuilt message
  lacks, so each such turn is kept as Claude returned it and re-sent verbatim
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic
from crewai import BaseLLM
from pydantic import PrivateAttr

MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
TOOL_TURNS_KEPT = 2_000
TOOL_ERROR = "Error executing tool:"      # how CrewAI reports a tool that raised

log = logging.getLogger("access_gate.llm")


class ClaudeRefusal(RuntimeError):
    """The request and its fallback chain declined."""


class ClaudeTruncated(RuntimeError):
    """The response hit max_tokens before finishing."""


def tool_definitions(tools: Any) -> list[dict]:
    """CrewAI's OpenAI-style tool schemas -> Anthropic tool definitions."""
    out = []
    for tool in tools or []:
        fn = tool.get("function", tool) if isinstance(tool, dict) else {}
        if not fn.get("name"):
            continue
        out.append({"name": fn["name"], "description": fn.get("description") or "",
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class ClaudeLLM(BaseLLM):
    llm_type: str = "anthropic-sdk"
    model: str = MODEL
    max_tokens: int = 16_000

    _client: Any = PrivateAttr(default=None)
    _last_request_id: str | None = PrivateAttr(default=None)
    # tool_use id -> {"content": the assistant turn's blocks as returned, "ids": its tool_use ids in order}
    _tool_turns: dict[str, dict] = PrivateAttr(default_factory=dict)

    def __init__(self, **data: Any):
        # CrewAI's BaseLLM validates the raw input before field defaults apply.
        data.setdefault("model", MODEL)
        super().__init__(**data)

    def with_client(self, client: Any) -> "ClaudeLLM":
        """Inject a client (tests use a fake; production resolves credentials from the environment)."""
        self._client = client
        return self

    def _sdk(self) -> Any:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    @property
    def model_in_use(self) -> str:
        return self.model

    # CrewAI capability probes. Function calling = True is what makes CrewAI pass
    # `response_model` through to call() for tasks with output_pydantic, and tools natively.
    def supports_function_calling(self) -> bool:
        return True

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 1_000_000

    @staticmethod
    def to_anthropic(messages: Any, tool_turns: dict[str, dict] | None = None) -> tuple[str | None, list[dict]]:
        """CrewAI's message list -> (system, alternating user/assistant turns).

        Tool use arrives in CrewAI's OpenAI shape: an assistant message with `tool_calls`, then one `tool` message per
        call executed. The first becomes the assistant turn Claude returned (thinking included); the results become
        `tool_result` blocks at the head of the next user turn, where the API requires them.
        """
        if isinstance(messages, str):
            return None, [{"role": "user", "content": messages}]
        system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system") or None
        turns: list[dict] = []
        for m in messages:
            role, content = m.get("role"), m.get("content") or ""
            if role == "assistant" and m.get("tool_calls"):
                turns.append({"role": "assistant", "content": ClaudeLLM._call_blocks(m, tool_turns or {})})
                continue
            if role == "tool":
                text = content if isinstance(content, str) else json.dumps(content, default=str)
                block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "", "content": text}
                if text.startswith(TOOL_ERROR):
                    block["is_error"] = True
                last = turns[-1] if turns else None
                if last and last["role"] == "user" and isinstance(last["content"], list) \
                        and all(b.get("type") == "tool_result" for b in last["content"]):
                    last["content"].append(block)
                else:
                    turns.append({"role": "user", "content": [block]})
                continue
            if role not in ("user", "assistant"):
                continue
            content = content if isinstance(content, str) else str(content)
            last = turns[-1] if turns else None
            if last and last["role"] == role:
                if isinstance(last["content"], str):
                    last["content"] += "\n\n" + content
                    continue
                if role == "user":                   # text after the tool results, as the API requires
                    last["content"].append({"type": "text", "text": content})
                    continue
            turns.append({"role": role, "content": content})
        if not turns or turns[0]["role"] != "user":
            turns.insert(0, {"role": "user", "content": "Begin."})
        if turns[-1]["role"] == "assistant":
            if isinstance(turns[-1]["content"], list) and any(b.get("type") == "tool_use" for b in turns[-1]["content"]):
                raise ValueError("the conversation ends on a tool call with no tool result")
            # A trailing assistant turn is a prefill, which Opus 5 rejects.
            turns.append({"role": "user", "content": "Continue."})
        return system, turns

    @staticmethod
    def _call_blocks(message: dict, tool_turns: dict[str, dict]) -> list[dict]:
        """The assistant turn for the calls CrewAI executed: Claude's own blocks when this adapter issued them."""
        calls = message["tool_calls"]
        ids = [str(tc.get("id") or "") for tc in calls]
        turn = tool_turns.get(ids[0]) if ids else None
        if turn is not None and set(ids) <= set(turn["ids"]):
            keep = set(ids)
            # A call CrewAI did not run must go: every tool_use needs a tool_result in the next turn.
            return [b for b in turn["content"] if b.get("type") != "tool_use" or b.get("id") in keep]
        blocks: list[dict] = [{"type": "text", "text": message["content"]}] if message.get("content") else []
        return blocks + [{"type": "tool_use", "id": i, "name": (tc.get("function") or {}).get("name") or "",
                          "input": _arguments((tc.get("function") or {}).get("arguments"))}
                         for i, tc in zip(ids, calls)]

    def call(self, messages: Any, tools: Any = None, callbacks: Any = None, available_functions: Any = None,
             from_task: Any = None, from_agent: Any = None, response_model: Any = None) -> Any:
        system, turns = self.to_anthropic(messages, self._tool_turns)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": turns,
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if system:
            params["system"] = system
        definitions = tool_definitions(tools)
        if definitions:
            params["tools"] = definitions

        sdk = self._sdk()
        if response_model is not None and not definitions:
            response = sdk.beta.messages.parse(output_format=response_model, **params)
            self._check(response)
            return response.parsed_output
        response = sdk.beta.messages.create(**params)
        self._check(response)
        uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if definitions and uses:
            blocks = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(vars(b))
                      for b in response.content]
            turn = {"content": blocks, "ids": [b.id for b in uses]}
            for b in uses:
                self._tool_turns[b.id] = turn
            while len(self._tool_turns) > TOOL_TURNS_KEPT:
                self._tool_turns.pop(next(iter(self._tool_turns)))
            return [{"id": b.id, "name": b.name, "input": dict(b.input or {})} for b in uses]
        return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")

    def _check(self, response: Any) -> None:
        self._last_request_id = getattr(response, "_request_id", None)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise ClaudeRefusal(f"declined (category={getattr(details, 'category', None)}, "
                                f"request_id={self._last_request_id})")
        if response.stop_reason == "max_tokens":
            raise ClaudeTruncated(f"hit max_tokens={self.max_tokens} (request_id={self._last_request_id})")
