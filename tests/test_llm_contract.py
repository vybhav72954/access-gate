"""The Claude crew, offline: a fake Anthropic client stands in for the API.

Verifies what no deterministic test can:
1. the request contract for claude-opus-5 (no sampling params, fallbacks on);
2. the tool-calling protocol: tool_use blocks returned to CrewAI, and each assistant turn sent back with its thinking
   blocks, as adaptive thinking requires; tool results first in the next user turn;
3. that CrewAI runs the two agents through the adapter to a NON-degraded decision the officer executed.
"""
import json
from types import SimpleNamespace

import pytest

from crew import crew_llm
from crew.llm import FALLBACK_BETA, MODEL, ClaudeLLM, ClaudeRefusal, ClaudeTruncated
from crew.run import process
from crew.schemas import Action
from tests.fakes import GOOD_DRAFT, Agents, claude_answer


class FakeMessages:
    def __init__(self, stop_reason=None, agents=None):
        self.calls, self.stop_reason, self.agents = [], stop_reason, agents or Agents()

    def parse(self, **kw):
        self.calls.append(("parse", kw))
        model = kw["output_format"]
        text = claude_answer(self.agents, kw, len(self.calls)).content[-1].text
        return SimpleNamespace(stop_reason=self.stop_reason or "end_turn", stop_details=SimpleNamespace(category="test"),
                               parsed_output=model.model_validate_json(text), content=[], _request_id="req_fake")

    def create(self, **kw):
        self.calls.append(("create", kw))
        if self.stop_reason:
            return SimpleNamespace(stop_reason=self.stop_reason, stop_details=None, _request_id="req_fake",
                                   content=[SimpleNamespace(type="text", text="plain text")])
        if not kw.get("system") and not kw.get("tools"):
            return SimpleNamespace(stop_reason="end_turn", stop_details=None, _request_id="req_fake",
                                   content=[SimpleNamespace(type="text", text="plain text")])
        return claude_answer(self.agents, kw, len(self.calls))


class Replies:
    def __init__(self, *replies):
        self.replies = list(replies)

    def respond(self, system, tools, history, prompt):
        return self.replies.pop(0)


def llm(stop_reason=None, agents=None):
    messages = FakeMessages(stop_reason, agents)
    return ClaudeLLM().with_client(SimpleNamespace(beta=SimpleNamespace(messages=messages))), messages


# ── request contract ───────────────────────────────────────────────────────

def test_request_contract_for_opus_5():
    model, fake = llm()
    model.call([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
               response_model=crew_llm.InvestigationReport)
    kind, kw = fake.calls[0]
    assert kind == "parse" and kw["model"] == MODEL == "claude-opus-5"
    assert kw["betas"] == [FALLBACK_BETA] == ["server-side-fallback-2026-07-01"]
    assert kw["fallbacks"] == "default"
    assert kw["system"] == "sys" and kw["output_format"] is crew_llm.InvestigationReport
    for rejected in ("temperature", "top_p", "top_k", "budget_tokens", "tools"):
        assert rejected not in kw, f"{rejected} is rejected with a 400 on Opus 5, or not asked for"


def test_plain_text_call_uses_create():
    model, fake = llm()
    assert model.call("hello") == "plain text"
    assert fake.calls[0][0] == "create" and fake.calls[0][1]["fallbacks"] == "default"


def test_refusal_and_truncation_raise():
    with pytest.raises(ClaudeRefusal):
        llm(stop_reason="refusal")[0].call("x")
    with pytest.raises(ClaudeTruncated):
        llm(stop_reason="max_tokens")[0].call("x")


def test_messages_never_end_in_a_prefill():
    system, turns = ClaudeLLM.to_anthropic([{"role": "assistant", "content": "a"}])
    assert system is None and turns[0]["role"] == "user" and turns[-1]["role"] == "user"


# ── tool-calling protocol ──────────────────────────────────────────────────

LOOKUP = [{"type": "function", "function": {
    "name": "lookup", "description": "Look a code up.",
    "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"],
                   "additionalProperties": False}}}]
START = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]


def crewai_turn(calls, run=None, results=None):
    run = calls if run is None else run
    results = results or ["result"] * len(run)
    return [{"role": "assistant", "content": None, "tool_calls": [
        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["input"])}}
        for c in run]}] + [{"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": r}
                           for c, r in zip(run, results)]


def test_tool_use_goes_back_to_crewai_with_its_thinking_kept_for_the_next_turn():
    model, fake = llm(agents=Replies(("calls", [("lookup", {"code": "A"}), ("lookup", {"code": "B"})]),
                                     ("text", "done")))
    calls = model.call(START, tools=LOOKUP)
    assert [(c["name"], c["input"]) for c in calls] == [("lookup", {"code": "A"}), ("lookup", {"code": "B"})]
    kind, kw = fake.calls[0]
    assert kind == "create" and kw["tools"] == [{"name": "lookup", "description": "Look a code up.",
                                                 "input_schema": LOOKUP[0]["function"]["parameters"]}]
    messages = START + crewai_turn(calls, results=["rate 100", "Error executing tool: no such code"]) + \
        [{"role": "user", "content": "Analyze the tool result."}]
    assert model.call(messages, tools=LOOKUP) == "done"
    sent = fake.calls[1][1]["messages"]
    assert [t["role"] for t in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"][0] == {"type": "thinking", "thinking": "weighing step 1", "signature": "thinking-sig-1"}
    assert [b["id"] for b in sent[1]["content"][1:]] == [c["id"] for c in calls]
    results = sent[2]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result", "text"]       # results first, then text
    assert results[1]["is_error"] is True and "is_error" not in results[0]


def test_a_tool_use_crewai_did_not_run_is_dropped():
    model, fake = llm(agents=Replies(("calls", [("lookup", {"code": "A"}), ("lookup", {"code": "B"})]),
                                     ("text", "done")))
    calls = model.call(START, tools=LOOKUP)
    model.call(START + crewai_turn(calls, run=calls[:1]), tools=LOOKUP)
    blocks = fake.calls[1][1]["messages"][1]["content"]
    assert [b["type"] for b in blocks] == ["thinking", "tool_use"] and blocks[1]["input"] == {"code": "A"}


def test_calls_this_adapter_did_not_issue_are_sent_as_plain_tool_use():
    foreign = [{"id": "toolu_x", "name": "lookup", "input": {"code": "Z"}}]
    _, turns = ClaudeLLM.to_anthropic(START + crewai_turn(foreign), {})
    assert turns[1] == {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_x", "name": "lookup",
                                                          "input": {"code": "Z"}}]}


# ── the crew, end to end, offline ──────────────────────────────────────────

def test_crew_path_produces_a_non_degraded_decision(world, tools, tmp_path):
    model, fake = llm()
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)

    assert not d.degraded, "the crew path fell back to rules - CrewAI did not complete through the adapter"
    assert d.action is Action.ESCALATE_SEC and d.acted_by == "enforcement_officer"
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert desk.supports_fraud is True and desk.confidence == pytest.approx(0.93)
    # The router plans, the desk reads, the advocate answers a reading that supports the suspicion, the reviewer
    # checks, and the liaison briefs the Committee on the escalation (B-48, B-49).
    assert d.agents == ["case_router", "desk_investigator", "provider_advocate", "audit_reviewer",
                        "committee_liaison", "enforcement_officer"]
    assert [t.tool for t in d.trail if t.agent == "enforcement_officer"] == ["enforcement_decision", "access_impact",
                                                                            "escalate_to_state_committee"]
    assert [t.tool for t in d.trail if t.agent == "committee_liaison"] == ["enforcement_decision", "access_impact",
                                                                          "file_committee_brief"]
    text = (tmp_path / d.artefact_path).read_text(encoding="utf-8")
    assert GOOD_DRAFT in text and "FAKE-LIAISON-BRIEF" in text
    # every assistant turn that used a tool went back with its thinking block
    for _, kw in fake.calls[1:]:
        for m in kw["messages"]:
            if m["role"] == "assistant" and isinstance(m["content"], list) and \
                    any(b.get("type") == "tool_use" for b in m["content"]):
                assert m["content"][0]["type"] == "thinking"


def test_the_investigator_uses_its_tools_through_claude(world, tools, tmp_path):
    compare = {"claim_a": "CLM-S07P1", "doc_type_a": "discharge_summary", "claim_b": "CLM-S07",
               "doc_type_b": "discharge_summary"}
    model, fake = llm(agents=Agents(investigator=[[("beneficiary_claim_history", {})], [("compare_documents", compare)]],
                                    report={"supports_fraud": None, "confidence": 0.0}))
    scenarios, store = world
    d = process(scenarios["S7"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert not d.degraded and d.action is Action.FIELD_AUDIT and d.acted_by == "enforcement_officer"
    assert [t.tool for t in d.trail if t.agent == "desk_investigator"] == ["beneficiary_claim_history",
                                                                          "compare_documents"]


def test_crew_refusal_degrades_to_rules(world, tools, tmp_path):
    model, _ = llm(stop_reason="refusal")
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.degraded and d.action is Action.ESCALATE_SEC
