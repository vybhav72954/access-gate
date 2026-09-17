"""The Gemini crew, offline: a fake google-genai client stands in for the API.

Three layers, each through the real code above it:
1. the adapter's request contract and free-tier safeguards (pacing, retries, the backup model);
2. the adapter's tool-calling protocol: function declarations, calls returned to CrewAI, and each call turn sent back
   with its thought signature, as Gemini 3 requires;
3. the crew end to end: a scripted model (tests/fakes.py) plays all six agents through CrewAI's own tool loop, so
   the tools, the task guardrails, the hand-off to the reviewer and its disputes, the policy between the agents, the
   liaison's brief and the officer's gated actions all run for real.
"""
import json
from types import SimpleNamespace

import pytest
from crewai.utilities.pydantic_schema_utils import generate_model_description
from google.genai import errors, types

from crew import agent_tools, crew_llm, guardrails, providers
from rules import triggers
from crew.gemini import (FALLBACK_MODEL, MODEL, SAFETY_SETTINGS, GeminiBlocked, GeminiLLM, GeminiMalformed,
                         GeminiTruncated, GeminiUnavailable, response_schema)
from crew.run import OutageClient, process
from crew.schemas import Action, Channel
from tests.fakes import GOOD_BRIEF, GOOD_DRAFT, REPORT, Agents, gemini_answer

INVESTIGATOR_TOOLS = {"beneficiary_claim_history", "documents_shared_with_other_claims", "surgeon_same_day_claims",
                      "read_document", "compare_documents"}
OFFICER_TOOLS = {"enforcement_decision", "access_impact", "release_claim", "issue_show_cause_notice",
                 "order_field_audit", "suspend_hospital", "escalate_to_state_committee",
                 "refer_specialty_for_delisting", "refer_for_human_review"}
KITS = {"investigator": INVESTIGATOR_TOOLS, "medical": {"beneficiary_claim_history", "read_document"},
        "field": {"read_document"}, "reviewer": INVESTIGATOR_TOOLS,
        "billing": {"claim_tariff", "read_document", "beneficiary_claim_history"},
        "advocate": {"claim_tariff", "read_document"},
        "liaison": {"enforcement_decision", "access_impact", "file_committee_brief"}, "officer": OFFICER_TOOLS}


@pytest.fixture(autouse=True)
def _defaults_only(monkeypatch):
    """A teammate's shell overrides must not change what these tests assert."""
    for var in ("GEMINI_MODEL", "GEMINI_FALLBACK_MODEL", "GEMINI_MAX_RPM"):
        monkeypatch.delenv(var, raising=False)


class FakeTime:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


class Replies:
    """A model that answers from a fixed list, for the protocol tests."""

    def __init__(self, *replies):
        self.replies, self.views = list(replies), []

    def respond(self, system, tools, history, prompt):
        self.views.append({"tools": tools, "history": history, "prompt": prompt})
        return self.replies.pop(0)


class FakeModels:
    """Replays a script of outcomes ('ok', 'SAFETY', 'prompt-block', 'MAX_TOKENS', 'MALFORMED', or an exception to
    raise); an 'ok' answers as `agents` would."""

    def __init__(self, script=(), agents=None):
        self.calls, self.script, self.agents = [], list(script), agents or Agents()

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        if step == "ok" and (config.get("system_instruction") or config.get("tools")):
            return gemini_answer(self.agents, contents, config, len(self.calls))
        text = json.dumps(REPORT) if config.get("response_json_schema") else "plain text"
        finish, feedback, candidates = types.FinishReason.STOP, None, None
        if step == "SAFETY":
            finish, text = types.FinishReason.SAFETY, ""
        elif step == "MAX_TOKENS":
            finish = types.FinishReason.MAX_TOKENS
        elif step == "MALFORMED":
            finish, text = types.FinishReason.MALFORMED_FUNCTION_CALL, ""
        elif step == "prompt-block":
            feedback, candidates, text = SimpleNamespace(block_reason=types.BlockedReason.SAFETY), [], None
        if candidates is None:
            candidates = [SimpleNamespace(finish_reason=finish)]
        return SimpleNamespace(text=text, candidates=candidates, prompt_feedback=feedback, response_id="resp_fake")


def llm(script=(), max_rpm=10, llm_kw=None, agents=None):
    fake, clock = FakeModels(script, agents), FakeTime()
    model = GeminiLLM(max_rpm=max_rpm, **(llm_kw or {})).with_client(SimpleNamespace(models=fake),
                                                                     sleep=clock.sleep, clock=clock.clock)
    return model, fake, clock


def rate_limited(code=429, delay=None):
    body = {"error": {"code": code, "status": "RESOURCE_EXHAUSTED", "message": "quota"}}
    if delay:
        body["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]
    return errors.ClientError(code, body) if code < 500 else errors.ServerError(code, body)


# ── request contract ───────────────────────────────────────────────────────

def test_request_contract():
    model, fake, _ = llm()
    model.call([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
               response_model=crew_llm.InvestigationReport)
    call = fake.calls[0]
    cfg = call["config"]
    assert call["model"] == MODEL == "gemini-3.8-flash"
    assert call["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert cfg["system_instruction"] == "sys" and cfg["response_mime_type"] == "application/json"
    assert cfg["response_json_schema"] == response_schema(crew_llm.InvestigationReport)
    assert cfg["safety_settings"] == SAFETY_SETTINGS
    assert {s["threshold"] for s in SAFETY_SETTINGS} == {"BLOCK_ONLY_HIGH"}
    for unset in ("temperature", "top_p", "top_k", "tools"):
        assert unset not in cfg
    types.GenerateContentConfig.model_validate(cfg)          # the real SDK accepts the request as built


def test_inconclusive_survives_the_schema():
    """The reason this adapter exists: CrewAI's own Gemini path strips null, so 'inconclusive' is unanswerable."""
    ours = response_schema(crew_llm.InvestigationReport)["properties"]["supports_fraud"]
    crewai_default = generate_model_description(crew_llm.InvestigationReport)["json_schema"]["schema"]
    assert ours["type"] == ["boolean", "null"]
    assert crewai_default["properties"]["supports_fraud"]["type"] == "boolean"


def test_plain_text_call_has_no_schema():
    model, fake, _ = llm()
    assert model.call("hello") == "plain text"
    assert "response_json_schema" not in fake.calls[0]["config"]


def test_assistant_turns_become_model_turns_and_never_end_the_request():
    system, turns = GeminiLLM.to_gemini([{"role": "system", "content": "s"}, {"role": "user", "content": "a"},
                                         {"role": "assistant", "content": "b"}])
    assert system == "s" and [t["role"] for t in turns] == ["user", "model", "user"]


# ── safeguards ─────────────────────────────────────────────────────────────

def test_a_safety_block_falls_back_once_to_the_backup_model():
    model, fake, _ = llm(script=["SAFETY", "ok"])
    out = model.call("x", response_model=crew_llm.InvestigationReport)
    assert isinstance(out, crew_llm.InvestigationReport)
    assert [c["model"] for c in fake.calls] == [MODEL, FALLBACK_MODEL]


@pytest.mark.parametrize("script", [["SAFETY", "SAFETY"], ["prompt-block", "prompt-block"]])
def test_blocked_on_both_models_raises(script):
    model, fake, _ = llm(script=script)
    with pytest.raises(GeminiBlocked):
        model.call("x")
    assert len(fake.calls) == 2


def test_truncation_raises():
    with pytest.raises(GeminiTruncated):
        llm(script=["MAX_TOKENS"])[0].call("x")


def test_rate_limit_is_retried_on_the_servers_schedule_when_there_is_no_backup():
    model, fake, clock = llm(script=[rate_limited(delay="7s"), "ok"], max_rpm=0, llm_kw={"fallback_model": None})
    assert model.call("x") == "plain text"
    assert clock.sleeps == [7.0] and len(fake.calls) == 2


def test_with_no_backup_errors_back_off_then_give_up():
    model, fake, clock = llm(script=[rate_limited(), rate_limited(503)] * 3, max_rpm=0,
                             llm_kw={"fallback_model": None})
    with pytest.raises(GeminiUnavailable):
        model.call("x")
    assert len(fake.calls) == model.max_retries + 1
    assert clock.sleeps == [2.0, 4.0, 8.0, 16.0]


def test_an_overloaded_model_hands_over_to_the_backup_for_the_rest_of_the_run():
    """Seen live on 2026-09-16: gemini-3.8-flash returned 503 'high demand' while the backup answered."""
    model, fake, clock = llm(script=[rate_limited(503), rate_limited(503), "ok", "ok"], max_rpm=0)
    assert model.call("first") == "plain text"
    assert model.call("second") == "plain text"
    assert [c["model"] for c in fake.calls] == [MODEL, MODEL, FALLBACK_MODEL, FALLBACK_MODEL]
    assert clock.sleeps == [2.0] and model.model_in_use == FALLBACK_MODEL


def test_a_rate_limited_primary_hands_over_without_waiting():
    """Seen live: a 43-second retryDelay before switching. With a backup, a 429 switches at once."""
    model, fake, clock = llm(script=[rate_limited(delay="43s"), "ok"], max_rpm=0)
    assert model.call("x") == "plain text"
    assert [c["model"] for c in fake.calls] == [MODEL, FALLBACK_MODEL] and clock.sleeps == []


def test_a_spent_daily_quota_switches_to_the_backup_at_once():
    model, fake, clock = llm(script=[rate_limited(delay="3600s"), "ok"], max_rpm=0)
    assert model.call("x") == "plain text"
    assert [c["model"] for c in fake.calls] == [MODEL, FALLBACK_MODEL] and clock.sleeps == []


def test_a_spent_quota_everywhere_degrades_immediately():
    model, fake, clock = llm(script=[rate_limited(delay="3600s")] * 2, max_rpm=0)
    with pytest.raises(GeminiUnavailable, match="quota"):
        model.call("x")
    assert clock.sleeps == [] and len(fake.calls) == 2


def daily_quota_spent(limit="500"):
    """As the free tier answered live on 17 September: a 429 naming a per-day quota, with a retryDelay under a minute."""
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
             "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": limit}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "53s"}]}})


def test_a_spent_daily_quota_is_not_waited_on():
    """F-57: the 53-second retryDelay had the adapter wait four times a call for a quota that resets at midnight."""
    model, fake, clock = llm(script=[daily_quota_spent()], max_rpm=0, llm_kw={"fallback_model": None})
    with pytest.raises(GeminiUnavailable, match="daily free-tier quota spent"):
        model.call("x")
    assert clock.sleeps == [] and len(fake.calls) == 1


def test_when_the_backups_daily_quota_runs_out_the_primary_is_tried_again():
    model, fake, clock = llm(script=[rate_limited(503), rate_limited(503), "ok", daily_quota_spent(), "ok"], max_rpm=0)
    assert model.call("first") == "plain text" and model.model_in_use == FALLBACK_MODEL
    assert model.call("second") == "plain text" and model.model_in_use == MODEL
    assert [c["model"] for c in fake.calls] == [MODEL, MODEL, FALLBACK_MODEL, FALLBACK_MODEL, MODEL]


def test_with_one_model_spent_the_other_is_waited_on_for_a_short_rate_limit():
    model, fake, clock = llm(script=[daily_quota_spent("20"), "ok", rate_limited(delay="7s"), "ok"], max_rpm=0)
    assert model.call("first") == "plain text"
    assert model.call("second") == "plain text"
    assert [c["model"] for c in fake.calls] == [MODEL, FALLBACK_MODEL, FALLBACK_MODEL, FALLBACK_MODEL]
    assert clock.sleeps == [7.0]


def test_a_bad_request_is_not_retried():
    model, fake, _ = llm(script=[errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})])
    with pytest.raises(errors.ClientError):
        model.call("x")
    assert len(fake.calls) == 1


def test_calls_are_paced_to_the_free_tier_rpm():
    model, _, clock = llm(max_rpm=10)
    model.call("a")
    model.call("b")
    assert clock.sleeps == [6.0]


def test_provider_switch():
    assert isinstance(providers.make_llm("gemini"), GeminiLLM)
    with pytest.raises(ValueError):
        providers.make_llm("groq")


def test_missing_key_is_reported(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert providers.missing_key("gemini") == "GEMINI_API_KEY"
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert providers.missing_key("gemini") is None


# ── tool-calling protocol ──────────────────────────────────────────────────

LOOKUP = [{"type": "function", "function": {
    "name": "lookup", "description": "Look a code up.",
    "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"],
                   "additionalProperties": False}}}]


def crewai_turn(calls, run=None, results=None):
    """The messages CrewAI appends after executing `run` (default: every call) of `calls`."""
    run = calls if run is None else run
    results = results or ["result"] * len(run)
    return [{"role": "assistant", "content": None, "tool_calls": [
        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["input"])}}
        for c in run]}] + [{"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": r}
                           for c, r in zip(run, results)]


START = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]


def test_function_calls_go_back_to_crewai_and_the_sdk_never_runs_a_tool():
    replies = Replies(("calls", [("lookup", {"code": "A"}), ("lookup", {"code": "B"})]), ("text", "done"))
    model, fake, _ = llm(agents=replies)
    calls = model.call(START, tools=LOOKUP)
    assert [(c["name"], c["input"]) for c in calls] == [("lookup", {"code": "A"}), ("lookup", {"code": "B"})]
    assert len({c["id"] for c in calls}) == 2
    cfg = fake.calls[0]["config"]
    assert cfg["tools"] == [{"function_declarations": [{"name": "lookup", "description": "Look a code up.",
                                                        "parameters_json_schema": {
                                                            "type": "object", "properties": {"code": {"type": "string"}},
                                                            "required": ["code"], "additionalProperties": False,
                                                            "propertyOrdering": ["code"]}}]}]
    assert cfg["automatic_function_calling"] == {"disable": True}
    types.GenerateContentConfig.model_validate(cfg)


def test_a_function_call_turn_goes_back_with_its_thought_signature():
    """Gemini 3 rejects a function-call turn without its signature; CrewAI's rebuilt message has none."""
    replies = Replies(("calls", [("lookup", {"code": "A"}), ("lookup", {"code": "B"})]), ("text", "done"))
    model, fake, _ = llm(agents=replies)
    calls = model.call(START, tools=LOOKUP)
    messages = START + crewai_turn(calls, results=["rate 100", "Error executing tool: no such code"])
    assert model.call(messages, tools=LOOKUP) == "done"
    sent = fake.calls[1]["contents"]
    assert [t["role"] for t in sent] == ["user", "model", "user"]
    assert sent[1]["parts"][0]["thought_signature"] == b"sig-1"
    assert [p["function_call"]["name"] for p in sent[1]["parts"]] == ["lookup", "lookup"]
    assert [p["function_response"]["response"] for p in sent[2]["parts"]] == [
        {"output": "rate 100"}, {"error": "Error executing tool: no such code"}]
    for turn in sent:
        types.Content.model_validate(turn)                   # the real SDK accepts every turn as built


def test_a_call_crewai_did_not_run_is_dropped_and_its_signature_kept():
    replies = Replies(("calls", [("lookup", {"code": "A"}), ("lookup", {"code": "B"})]), ("text", "done"))
    model, fake, _ = llm(agents=replies)
    calls = model.call(START, tools=LOOKUP)
    model.call(START + crewai_turn(calls, run=calls[1:]), tools=LOOKUP)
    turn = fake.calls[1]["contents"][1]
    assert [p["function_call"]["args"] for p in turn["parts"]] == [{"code": "B"}]
    assert turn["parts"][0]["thought_signature"] == b"sig-1"


def test_calls_this_adapter_did_not_issue_are_sent_plainly():
    foreign = [{"id": "elsewhere-1", "name": "lookup", "input": {"code": "Z"}}]
    system, turns = GeminiLLM.to_gemini(START + crewai_turn(foreign), {})
    assert turns[1] == {"role": "model", "parts": [{"function_call": {"name": "lookup", "args": {"code": "Z"}}}]}


def test_a_conversation_cannot_end_on_an_unanswered_call():
    calls = [{"id": "c1", "name": "lookup", "input": {}}]
    with pytest.raises(ValueError, match="no function response"):
        GeminiLLM.to_gemini(START + crewai_turn(calls)[:1], {})


def test_a_malformed_function_call_is_asked_for_once_more():
    model, fake, _ = llm(script=["MALFORMED", "ok"], agents=Replies(("text", "fine")))
    assert model.call(START, tools=LOOKUP) == "fine" and len(fake.calls) == 2
    model, fake, _ = llm(script=["MALFORMED", "MALFORMED"])
    with pytest.raises(GeminiMalformed):
        model.call(START, tools=LOOKUP)


# ── the crew, end to end, offline ──────────────────────────────────────────

def crew(**kw):
    agents = Agents(**kw)
    model, fake, _ = llm(agents=agents)
    return model, fake, agents


def _case(scenario, tools, store):
    """The case file as process() builds it, for a test that only needs an agent's tool kit."""
    from crew.agent_tools import CaseFile
    from crew.run import intake
    from rules import triggers
    _problems, hospital, package = intake(scenario.claim, tools)
    hit = triggers.primary(triggers.evaluate(scenario.claim, hospital, package, store, tools))
    return CaseFile(claim=scenario.claim, hit=hit, hospital=hospital, package=package, store=store, tools=tools)


def artefact(d, tmp_path):
    return (tmp_path / d.artefact_path).read_text(encoding="utf-8")


def test_crew_path_produces_a_non_degraded_decision(world, tools, tmp_path):
    model, fake, agents = crew()
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert not d.degraded and d.action is Action.ESCALATE_SEC and d.model == MODEL
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert desk.supports_fraud is True and desk.confidence == pytest.approx(0.93)
    assert d.acted_by == "enforcement_officer" and d.explanation == GOOD_DRAFT
    # The router plans, the desk reads, the advocate answers a reading that supports the suspicion, the reviewer
    # checks, and the liaison briefs the Committee on the escalation (B-48, B-49).
    assert d.agents == ["case_router", "desk_investigator", "provider_advocate", "audit_reviewer",
                        "committee_liaison", "enforcement_officer"]
    assert [t.tool for t in d.trail if t.agent == "enforcement_officer"] == ["enforcement_decision", "access_impact",
                                                                            "escalate_to_state_committee"]
    text = artefact(d, tmp_path)
    assert "FAKE-OFFICER-DRAFT" in text and f"crew on {MODEL}, executed by the Enforcement Officer" in text
    assert "## Investigation trail" in text and "`escalate_to_state_committee`" in text


def test_each_agent_has_only_its_own_tools(world, tools, tmp_path):
    """No agent that reads the evidence can see access or act; the liaison and the officer cannot investigate."""
    scenarios, store = world
    offered = {}
    s5 = [[("documents_shared_with_other_claims", {})],
          [("compare_documents", {"claim_a": "CLM-S05", "doc_type_a": "discharge_summary", "claim_b": "CLM-S05A",
                                  "doc_type_b": "discharge_summary"})]]
    money = [{"track": "billing", "reason": "The amount claimed should be checked against the published rate."}]
    for sid, kw in (("S5", {"investigator": s5, "opens": money}),
                    ("S8", {"report": {"supports_fraud": None, "confidence": 0.0}})):
        model, fake, agents = crew(**kw)
        d = process(scenarios[sid].claim, store, tools, llm=model, out_dir=tmp_path / sid)
        assert not d.degraded
        for r in agents.requests:
            if r["tools"]:
                assert offered.setdefault(r["agent"], set(r["tools"])) == set(r["tools"])
    assert offered == KITS


def test_the_officer_reads_the_decision_and_the_access_at_stake_before_drafting(world, tools, tmp_path):
    """F-08: given only reason codes, a live model wrote that Bahraich would lose its 'only healthcare facility'."""
    model, fake, agents = crew()
    scenarios, store = world
    process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    results = dict(r for req in agents.requests if req["agent"] == "officer" for batch in req["history"] for r in batch)
    assert "The hospital is NOT suspended" in results["enforcement_decision"]
    assert "Execute it with: escalate_to_state_committee" in results["enforcement_decision"]
    assert ("Specialties at stake if the hospital were suspended - it is the only real provider of Cardiology "
            "(nearest alternative 83 km away in Gonda). ONLY these specialties are at stake") in results["access_impact"]


def test_the_investigator_compares_related_claims_with_its_tools(world, tools, tmp_path):
    """T7's desk check across the member's claims: the agent lists them, compares, and the trail records it."""
    compare = {"claim_a": "CLM-S07P1", "doc_type_a": "discharge_summary", "claim_b": "CLM-S07",
               "doc_type_b": "discharge_summary"}
    model, fake, agents = crew(investigator=[[("beneficiary_claim_history", {})], [("compare_documents", compare)]],
                               report={"supports_fraud": None, "confidence": 0.0})
    scenarios, store = world
    d = process(scenarios["S7"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.action is Action.FIELD_AUDIT and d.field_channels_ordered == [Channel.BENEFICIARY_CALL] and not d.degraded
    investigator = [t for t in d.trail if t.agent == "desk_investigator"]
    assert [t.tool for t in investigator] == ["beneficiary_claim_history", "compare_documents"]
    assert "CLM-S07P1" in investigator[0].outcome and "similarity" in investigator[1].outcome
    seen = [r for r in agents.requests if r["agent"] == "investigator"][-1]["history"]
    assert "text similarity" in seen[1][0][1]                  # the comparison reached the model
    text = artefact(d, tmp_path)
    assert "## What the field team must establish in this case" in text
    assert "Did the beneficiary attend the hospital on each of the admission dates?" in text


def test_a_stance_on_repeat_admissions_needs_the_other_claims_examined(world, tools, tmp_path):
    """The task guardrail: a verdict on T7 without looking at the other admissions goes back to the agent."""
    compare = {"claim_a": "CLM-S07P2", "doc_type_a": "discharge_summary", "claim_b": "CLM-S07",
               "doc_type_b": "discharge_summary"}
    model, fake, agents = crew(lazy_first=True, report={"supports_fraud": True, "confidence": 0.9},
                               investigator=[[("beneficiary_claim_history", {})], [("compare_documents", compare)]])
    scenarios, store = world
    d = process(scenarios["S7"].claim, store, tools, llm=model, out_dir=tmp_path)
    prompts = [r["prompt"] for r in agents.requests if r["agent"] == "investigator"]
    assert "without examining" not in prompts[0] and any("took a position without examining" in p for p in prompts)
    assert [t.tool for t in d.trail if t.agent == "desk_investigator"] == ["beneficiary_claim_history",
                                                                          "compare_documents"]
    assert not d.degraded and d.action is Action.SHOW_CAUSE


def test_a_guardrail_ignored_twice_degrades_the_case(world, tools, tmp_path):
    model, fake, agents = crew(report={"supports_fraud": True, "confidence": 0.9})     # never examines anything
    scenarios, store = world
    d = process(scenarios["S7"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.degraded and d.action is Action.FIELD_AUDIT
    assert d.agents == [] and d.review is None      # the rules decided: nothing the agents did is presented as counting


def test_the_investigator_cannot_read_a_claim_no_tool_has_shown_it(world, tools, tmp_path):
    peek = {"claim_id": "CLM-S01", "doc_type": "death_certificate"}
    model, fake, agents = crew(investigator=[[("read_document", peek)]], report={"supports_fraud": None,
                                                                                "confidence": 0.0})
    scenarios, store = world
    d = process(scenarios["S7"].claim, store, tools, llm=model, out_dir=tmp_path)
    refused = d.trail[0]
    assert refused.tool == "read_document" and refused.refused and "neither this claim" in refused.outcome
    seen = [r for r in agents.requests if r["agent"] == "investigator"][-1]["history"][0][0][1]
    assert seen.startswith("Error executing tool") and "Certificate of death" not in seen


def test_the_officer_cannot_execute_any_action_but_the_policys(world, tools, tmp_path):
    model, fake, agents = crew(wrong_tool="suspend_hospital")
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    refused = next(t for t in d.trail if t.tool == "suspend_hospital")
    assert refused.refused and "decided escalate_to_sec" in refused.outcome
    assert d.action is Action.ESCALATE_SEC and d.acted_by == "enforcement_officer"
    assert "**refused**" in artefact(d, tmp_path)


def test_an_overstated_draft_is_sent_back_and_revised(world, tools, tmp_path):
    bad = ("HOSP-26104 has been suspended, leaving Bahraich without healthcare. " + GOOD_DRAFT)
    model, fake, agents = crew(drafts=[bad, GOOD_DRAFT])
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    attempts = [t for t in d.trail if t.tool == "escalate_to_state_committee"]
    assert [t.refused for t in attempts] == [True, False]
    assert "says the hospital is suspended" in attempts[0].outcome and "overstates the access loss" in attempts[0].outcome
    assert d.acted_by == "enforcement_officer" and "has been suspended" not in artefact(d, tmp_path)


def test_an_officer_that_never_writes_a_publishable_draft_leaves_the_template(world, tools, tmp_path):
    model, fake, agents = crew(drafts=["HOSP-26104 has been suspended. " + GOOD_DRAFT])
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert sum(t.tool == "escalate_to_state_committee" for t in d.trail) == agent_tools.MAX_DRAFTS + 1
    assert d.acted_by == "orchestrator" and not d.degraded and d.action is Action.ESCALATE_SEC
    text = artefact(d, tmp_path)
    assert "has been suspended" not in text and "Not suspended: escalated to the State Empanelment Committee" in text


def test_an_officer_that_never_acts_leaves_the_decision_to_the_orchestrator(world, tools, tmp_path):
    model, fake, agents = crew(officer="silent")
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.action is Action.ESCALATE_SEC and d.acted_by == "orchestrator" and not d.degraded
    assert "executed by the orchestrator: the officer did not act" in artefact(d, tmp_path)


def test_the_show_cause_notice_names_the_documents_the_officer_requests(world, tools, tmp_path):
    model, fake, agents = crew(report={"supports_fraud": None, "confidence": 0.0},
                               documents=["Empanelment certificate listing cardiology",
                                          "Cath lab register for the admission"])
    scenarios, store = world
    d = process(scenarios["S4"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.action is Action.SHOW_CAUSE and d.acted_by == "enforcement_officer"
    assert d.documents_requested == ["Empanelment certificate listing cardiology", "Cath lab register for the admission"]
    assert "- Cath lab register for the admission" in artefact(d, tmp_path)


def test_a_release_is_executed_by_the_officer_and_leaves_no_artefact(world, tools, tmp_path):
    model, fake, agents = crew(report={"supports_fraud": False, "confidence": 0.85})
    scenarios, store = world
    d = process(scenarios["S3"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.action is Action.RELEASE_CLAIM and d.acted_by == "enforcement_officer" and d.artefact_path is None
    assert "enforcement_officer:release_claim" in (tmp_path / "decision_log.csv").read_text(encoding="utf-8")


def test_the_registry_finding_is_the_lookup_on_the_crew_path(world, tools, tmp_path):
    scenarios, store = world
    rules_only = process(scenarios["S4"].claim, store, tools, out_dir=tmp_path / "rules")
    model, fake, agents = crew(report={"supports_fraud": None, "confidence": 0.0})
    d = process(scenarios["S4"].claim, store, tools, llm=model, out_dir=tmp_path / "crew")
    reg = next(f for f in d.findings if f.agent == "registry_verification")
    assert (reg.supports_fraud, reg.confidence) == (True, 0.85) and not d.degraded
    assert d.action is rules_only.action is Action.SHOW_CAUSE


def test_an_inconclusive_desk_reading_orders_a_field_audit(world, tools, tmp_path):
    """The null path, through CrewAI and the Gemini adapter: inconclusive buys evidence (S7)."""
    model, _, _ = crew(report={"supports_fraud": None, "confidence": 0.0})
    scenarios, store = world
    d = process(scenarios["S7"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert not d.degraded and d.action is Action.FIELD_AUDIT
    assert d.field_channels_ordered == [Channel.BENEFICIARY_CALL]


def test_a_nan_confidence_is_no_confidence():
    """F-29: min/max turned NaN (and infinity) into 1.0 -- certainty from a malformed number."""
    assert guardrails.clamp(float("nan")) == 0.0 and guardrails.clamp(float("inf")) == 0.0
    assert guardrails.clamp(1.7) == 1.0 and guardrails.clamp(-2) == 0.0


@pytest.mark.parametrize("sid, calls", [
    ("S1", []),
    ("S5", [[("documents_shared_with_other_claims", {})],
            [("compare_documents", {"claim_a": "CLM-S05", "doc_type_a": "discharge_summary",
                                    "claim_b": "CLM-S05A", "doc_type_b": "discharge_summary"})]]),
])
def test_the_crew_desk_cannot_clear_a_recorded_fact(world, tools, tmp_path, sid, calls):
    """F-44: a certificate dating death before admission (S1), a document filed for another beneficiary (S5)."""
    scenarios, store = world
    claim = scenarios[sid].claim.model_copy(update={"field_reports": []})
    model, _, _ = crew(investigator=calls, report={"supports_fraud": False, "confidence": 0.95})
    d = process(claim, store, tools, llm=model, out_dir=tmp_path)
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert desk.supports_fraud is None and not d.degraded
    assert d.action is Action.FIELD_AUDIT


def test_an_agent_cannot_flip_drop_or_inflate_field_evidence(world, tools, tmp_path):
    """S8 on file: beneficiary call supports at 0.60, hospital visit opposes at 0.55."""
    inflated_and_flipped = [
        {"channel": "beneficiary_call", "supports_fraud": True, "confidence": 0.99, "conclusion": "sure"},
        {"channel": "hospital_visit", "supports_fraud": True, "confidence": 0.90, "conclusion": "flipped"}]
    scenarios, store = world
    for assessments in (inflated_and_flipped, []):
        model, _, _ = crew(report={"supports_fraud": None, "confidence": 0.0}, assessments=assessments)
        d = process(scenarios["S8"].claim, store, tools, llm=model, out_dir=tmp_path)
        field = {f.channel: f for f in d.findings if f.agent == "field_review"}
        assert (field[Channel.BENEFICIARY_CALL].supports_fraud, field[Channel.BENEFICIARY_CALL].confidence) == (True, 0.60)
        assert (field[Channel.HOSPITAL_VISIT].supports_fraud, field[Channel.HOSPITAL_VISIT].confidence) == (False, 0.55)
        assert "recorded finding kept" in field[Channel.HOSPITAL_VISIT].conclusion
        assert d.action is Action.NO_ACTION and not d.degraded


@pytest.mark.parametrize("text, action, bad", [
    ("HOSP-26104 is the only empanelled cardiology provider. This escalation does not suspend HOSP-26104.",
     "escalate_to_sec", False),
    ("Bahraich would lose its only healthcare facility.", "escalate_to_sec", True),
    ("Residents would be left without healthcare.", "escalate_to_sec", True),
    ("HOSP-26104 has been suspended pending review.", "escalate_to_sec", True),
    ("HOSP-26104 has been suspended and referred to the Committee.", "suspend_hospital", False),
    ("Radiation oncology at HOSP-21020 has been delisted.", "delist_specialty", True),
    ("HOSP-21020 has been referred for de-listing; nothing has been de-listed yet.", "delist_specialty", False),
    ("Compare HOSP-00311, which was suspended elsewhere.", "escalate_to_sec", True),
    ("No specialty has been de-listed; the Committee will decide.", "delist_specialty", False),
    ("HOSP-26104 has not been suspended.", "escalate_to_sec", False),
    ("This does not mean HOSP-26104 is suspended.", "escalate_to_sec", False),
    ("A beneficiary call will verify all healthcare claims made for this beneficiary.", "order_field_audit", False),
    ("Bahraich would lose all healthcare access if HOSP-26104 were suspended.", "escalate_to_sec", True),
    ("The district will not lose all healthcare, all inpatient care, or its only hospital.", "order_field_audit", False),
    ("It is not a phantom listing; the district would lose its only hospital.", "escalate_to_sec", True),
    # F-28: a line break ends the negated sentence; future tense and NHA's own word still assert the outcome
    ("- Not suspended by this decision\n- Radiation oncology at HOSP-21020 has been delisted.", "delist_specialty", True),
    ("HOSP-26104 will be suspended once the Committee meets.", "escalate_to_sec", True),
    ("HOSP-21020 has been de-empanelled for radiation oncology.", "delist_specialty", True),
    ("HOSP-21020 will not be de-empanelled by this referral.", "delist_specialty", False),
    # a release may say the claim is released; nothing else may
    ("The claim has been released for payment.", "release_claim", False),
    ("The claim has been released for payment.", "order_field_audit", True),
])
def test_draft_checks(text, action, bad):
    # Padded to a real explanation's length with a sentence that trips no check, so each case tests its own pattern.
    padded = text + " " + NEUTRAL
    assert bool(guardrails.draft_problems(padded, action, "HOSP-26104" if "26104" in text else "HOSP-21020")) is bad


NEUTRAL = ("The decision rests on the documents and field reports described above, and this explanation sets out "
           "what happens next for the hospital and for the case.")


@pytest.mark.parametrize("text, problem", [
    ("", "words long"),                                                         # F-42: an empty draft shipped
    ("Escalated.", "words long"),
    (" ".join(["word"] * 401), "words long"),
    ("## Question for the Committee\n" + NEUTRAL + " " + NEUTRAL, "Markdown heading"),
    ("The withheld claim has been released and will be paid to the hospital. " + NEUTRAL, "released or paid"),
])
def test_draft_shape_checks(text, problem):
    assert any(problem in p for p in guardrails.draft_problems(text, "escalate_to_sec", "HOSP-26104"))


def test_a_draft_saying_the_claim_is_not_released_passes():
    text = "The claim is not released; it stays withheld while the Committee decides. " + NEUTRAL
    assert guardrails.draft_problems(text, "escalate_to_sec", "HOSP-26104") == []


@pytest.mark.parametrize("items, problem", [
    ([], "lists 0"), (["x"] * 9, "lists 9"), (["ok?"], "characters"), (["Ask HOSP-00311 for its register"], "another"),
])
def test_the_officers_lists_are_checked_too(items, problem):
    assert any(problem in p for p in guardrails.item_problems(items, "question", "HOSP-26104"))


def test_agent_text_cannot_name_another_hospital():
    """EC-1 for findings: the tools never show another hospital's id, so any other id an agent writes is invented."""
    out = guardrails.scrub_identifiers("Compare HOSP-00311 and HOSP6P66487 with HOSP-26104.", "HOSP-26104")
    assert out == "Compare [another hospital] and [another hospital] with HOSP-26104."


def test_the_crew_desk_cannot_clear_a_claim_missing_mandatory_documents(world, tools, tmp_path):
    """The rules desk's evidentiary preconditions bind the agent too."""
    s3 = world[0]["S3"].claim
    claim = s3.model_copy(update={"claim_id": "CLM-S03-NOOP",
                                  "documents": [d for d in s3.documents if d.doc_type != "operative_note"]})
    model, _, _ = crew(report={"supports_fraud": False, "confidence": 0.95})
    d = process(claim, world[1], tools, llm=model, out_dir=tmp_path)
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert desk.supports_fraud is None and "mandatory documents are missing" in desk.conclusion
    assert d.action is Action.FIELD_AUDIT and not d.degraded


def test_the_crew_desk_cannot_convict_on_a_register_entry_alone(world, tools, tmp_path):
    s1 = world[0]["S1"].claim
    claim = s1.model_copy(update={"claim_id": "CLM-S01-NOCERT-LLM",
                                  "documents": [d for d in s1.documents if d.doc_type != "death_certificate"]})
    model, _, _ = crew(report={"supports_fraud": True, "confidence": 0.95})
    d = process(claim, world[1], tools, llm=model, out_dir=tmp_path)
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert desk.supports_fraud is None and "no death certificate" in desk.conclusion
    assert d.action is Action.FIELD_AUDIT and not d.degraded


def test_gemini_outage_degrades_to_rules(world, tools, tmp_path):
    clock = FakeTime()
    model = GeminiLLM().with_client(OutageClient(), sleep=clock.sleep, clock=clock.clock)
    scenarios, store = world
    d = process(scenarios["S9"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.degraded and d.action is Action.ESCALATE_SEC and d.acted_by == "orchestrator"
    assert d.agents == [] and d.committee_brief is None
    assert f"degraded: {MODEL} unavailable" in artefact(d, tmp_path)


def test_the_crew_desk_cannot_clear_a_package_with_no_referral_route(world, tools, tmp_path):
    """F-51 on the crew path: an agent reading a referral letter as a clearance, for a package it cannot clear."""
    from crew.schemas import Document
    from rules import triggers
    s4 = world[0]["S4"].claim
    p = sorted((p for p in tools.packages() if p.govt_reserved and not p.referral_allowed and p.amount_rs > 0
                and len(p.specialties) == 1), key=lambda p: p.package_code)[0]
    h = sorted((h for h in tools.hospitals() if p.specialty in h.specialties and h.hospital_type == "Private(For Profit)"),
               key=lambda h: h.hospital_ref)[0]
    from crew.investigate import DISCHARGE_SUMMARY, required_documents
    docs = s4.documents + [Document(doc_type="referral_letter", text="CLM-R3-LLM: referred by the district hospital.")]
    docs += [Document(doc_type=r.filed_as, text=f"CLM-R3-LLM: {r.label} on file.")     # every mandatory category
             for r in required_documents(p) if r is not DISCHARGE_SUMMARY]
    claim = s4.model_copy(update={"claim_id": "CLM-R3-LLM", "hospital_ref": h.hospital_ref, "district_code": h.district_code,
                                  "package_code": p.package_code, "amount_claimed": p.amount_rs, "documents": docs})
    model, _, _ = crew(report={"supports_fraud": False, "confidence": 0.95})
    d = process(claim, triggers.ClaimStore([claim]), tools, llm=model, out_dir=tmp_path)
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert d.triggers_fired == ["R3"] and desk.supports_fraud is None and "no referral route" in desk.conclusion
    assert d.action is not Action.RELEASE_CLAIM and not d.degraded


@pytest.mark.parametrize("case_id, missing", [("BCH-R2-07", "no invoice or bill"), ("BCH-R3-06", "no referral")])
def test_the_crew_cannot_clear_a_trigger_whose_explaining_document_is_missing(tools, tmp_path, case_id, missing):
    """F-58, found by the benchmark: the crew released an above-rate claim with no invoice, and a reserved package
    with no referral, reading each gap as an upload slip. The rules path never clears either."""
    from generate.benchmark import build_benchmark
    cases, store = build_benchmark(tools)
    claim = next(c for c in cases if c.case_id == case_id).claim
    rules_only = process(claim, store, tools, out_dir=tmp_path / "rules")
    model, _, _ = crew(report={"supports_fraud": False, "confidence": 1.0})
    d = process(claim, store, tools, llm=model, out_dir=tmp_path / "crew")
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert desk.supports_fraud is None and missing in desk.conclusion and not d.degraded
    assert d.action is Action.NO_ACTION and rules_only.action is Action.SHOW_CAUSE


# ── the six agents: who works a case, the hand-off, disputes, the Committee brief ─

DISPUTE = {"finding": "desk_audit", "reason": "The LAMA form it relies on is dated the day after the discharge summary, "
                                              "so it cannot explain a same-day discharge."}
CLEARED = {"supports_fraud": False, "confidence": 0.85}
NULL = {"supports_fraud": None, "confidence": 0.0}


def speakers(agents):
    return {r["agent"] for r in agents.requests}


def test_the_medical_auditor_works_only_on_a_clinical_trigger(world, tools, tmp_path):
    scenarios, store = world
    model, _, agents = crew(report=CLEARED)
    d = process(scenarios["S3"].claim, store, tools, llm=model, out_dir=tmp_path / "t2")        # T2: clinical
    assert "medical" in speakers(agents) and "medical_auditor" in d.agents
    assert any(f.agent == "medical_audit" for f in d.findings)
    model, _, agents = crew(report=NULL)
    d = process(scenarios["S4"].claim, store, tools, llm=model, out_dir=tmp_path / "r1")        # R1: a registry fact
    assert "medical" not in speakers(agents) and "medical_auditor" not in d.agents
    assert not any(f.agent == "medical_audit" for f in d.findings) and not d.degraded


def test_the_field_evidence_analyst_works_only_when_field_reports_are_on_file(world, tools, tmp_path):
    scenarios, store = world
    for sid, expected in (("S8", True), ("S7", False)):
        model, _, agents = crew(report=NULL)
        d = process(scenarios[sid].claim, store, tools, llm=model, out_dir=tmp_path / sid)
        assert ("field" in speakers(agents)) is expected and ("field_evidence_analyst" in d.agents) is expected


def test_the_readers_work_independently_and_the_reviewer_receives_all_three(world, tools, tmp_path):
    """Each reader gets the case file and never another reader's finding; the reviewer gets the three, labelled."""
    model, _, agents = crew(report={**NULL, "conclusion": "DESK-READING: the records cannot be told from copies."},
                            medical={"conclusion": "MEDICAL-READING: each admission documents its own work-up."},
                            assessments=[{"channel": "beneficiary_call", "supports_fraud": True, "confidence": 0.5,
                                          "conclusion": "FIELD-READING: specific, and it answers this trigger."}])
    scenarios, store = world
    d = process(scenarios["S8"].claim, store, tools, llm=model, out_dir=tmp_path)
    seen = {}
    for r in agents.requests:
        seen[r["agent"]] = seen.get(r["agent"], "") + r["prompt"]
    for reader in ("investigator", "medical", "field"):
        assert not any(mark in seen[reader] for mark in ("DESK-READING", "MEDICAL-READING", "FIELD-READING")), reader
    for mark in ('"finding": "desk_audit"', "DESK-READING", '"finding": "medical_audit"', "MEDICAL-READING",
                 '"finding": "field_reports"', "FIELD-READING"):
        assert mark in seen["reviewer"]
    # A field report is the analyst's to weigh: inside a reading it would count twice.
    report = scenarios["S8"].claim.field_reports[0].summary
    assert report not in seen["investigator"] and report not in seen["medical"]
    assert report in seen["field"] and report in seen["reviewer"]
    # The router plans first. No reading here supports the suspicion (the desk is null and the medical auditor
    # inconclusive), so the advocate does not run: there is no action against the hospital for it to answer (B-49).
    assert d.agents == ["case_router", "desk_investigator", "medical_auditor", "field_evidence_analyst",
                        "audit_reviewer", "enforcement_officer"]


def test_a_disputed_reading_weighs_nothing(world, tools, tmp_path):
    """Dispute only: the reviewer sets the desk's clearance aside, and the case buys field evidence instead of a release.
    It cannot put a finding of its own in its place."""
    scenarios, store = world
    released = process(scenarios["S3"].claim, store, tools, llm=crew(report=CLEARED)[0], out_dir=tmp_path / "kept")
    assert released.action is Action.RELEASE_CLAIM and released.disputed == []
    model, _, _ = crew(report=CLEARED, disputes=[DISPUTE])
    d = process(scenarios["S3"].claim, store, tools, llm=model, out_dir=tmp_path / "disputed")
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert d.disputed == ["desk_audit"] and (desk.supports_fraud, desk.confidence) == (None, 0.0)
    assert "Disputed by the Audit Reviewer: The LAMA form it relies on" in desk.conclusion
    assert d.action is Action.FIELD_AUDIT and not d.degraded
    text = artefact(d, tmp_path / "disputed")
    assert "## Audit review" in text and "Disputed, and so set aside: desk_audit" in text and "FAKE-REVIEWER" in text
    log = (tmp_path / "disputed" / "decision_log.csv").read_text(encoding="utf-8")
    assert "desk_investigator|medical_auditor|audit_reviewer|enforcement_officer" in log


class _Report:
    """The shape guardrails._reading reads off an agent's report."""

    def __init__(self, supports_fraud, confidence, conclusion, citation):
        self.supports_fraud, self.confidence = supports_fraud, confidence
        self.conclusion, self.citation = conclusion, citation


class _Review:
    def __init__(self, *names):
        self.disputes = [SimpleNamespace(finding=n, reason="the reading does not follow from what it cites")
                         for n in names]
        self.summary = "review"


MEASURED = {"finding": "desk_audit", "reason": "The comparison it relies on is a coincidence of templates, not "
                                              "evidence that the same document was filed twice."}
CONFIRMED = {"supports_fraud": True, "confidence": 0.90}
# S5's trigger is T6, so a stance there needs the other claims examined first (the cross-claim task guardrail).
S5_DESK = [[("documents_shared_with_other_claims", {})],
           [("compare_documents", {"claim_a": "CLM-S05", "doc_type_a": "discharge_summary",
                                   "claim_b": "CLM-S05A", "doc_type_b": "discharge_summary"})]]


OPEN_BILLING = [{"track": "billing", "reason": "The amount claimed should be checked against the published rate."}]
OPEN_FIELD = [{"track": "field", "reason": "A field report would settle what the documents cannot settle here."}]
STANDS = {"explanation": "The discharge against medical advice on file accounts for what the trigger flagged.",
          "excluded": False, "citation": "the LAMA form on this claim"}
# A defence that is not excluded stops an action, so it must have read the file it rests on (F-63).
READ_IT = [[("read_document", {"claim_id": "CLM-S04", "doc_type": "discharge_summary"})]]


def test_the_router_opens_work_no_rule_mandates(world, tools, tmp_path):
    """B-48. On S1 no rule opens the billing track; the router does, and the Billing & Tariff Analyst works the case."""
    scenarios, store = world
    model, _, agents = crew(opens=OPEN_BILLING)
    d = process(scenarios["S1"].claim, store, tools, llm=model, out_dir=tmp_path / "opened")
    assert d.opened_by_router == ["billing"]
    assert "billing_analyst" in d.agents and "case_router" in d.agents
    assert any(f.agent == "billing_audit" for f in d.findings)
    assert not d.degraded


def test_the_router_cannot_close_what_a_rule_opened(world, tools, tmp_path):
    """The router's plan can only add. S3 is clinical, so the Medical Auditor works it whatever the router says."""
    scenarios, store = world
    model, _, _ = crew(opens=[])                     # the router opens nothing at all
    d = process(scenarios["S3"].claim, store, tools, llm=model, out_dir=tmp_path / "mandated")
    assert d.opened_by_router == []
    assert "medical_auditor" in d.agents and not d.degraded


def test_the_router_cannot_open_a_track_there_is_no_evidence_for(world, tools, tmp_path):
    """Opening the field track with no field report on file gives the analyst nothing to weigh: sent back."""
    scenarios, store = world
    model, _, agents = crew(opens=OPEN_FIELD, revised_opens=[])     # it revises once told why
    d = process(scenarios["S1"].claim, store, tools, llm=model, out_dir=tmp_path / "nofield")
    prompts = [r["prompt"] for r in agents.requests if r["agent"] == "router"]
    assert any("no field report is on file" in p for p in prompts)
    assert "field_evidence_analyst" not in d.agents and d.opened_by_router == []
    assert not d.degraded


def test_the_router_is_given_no_evidence_tool(world, tools):
    """A router that has read the case has already reached the readers' question (F-54)."""
    from crew.agent_tools import router_tools
    scenarios, store = world
    assert router_tools(_case(scenarios["S1"], tools, store)) == []


def test_an_unexcluded_defence_stops_an_action_no_human_has_reviewed(world, tools, tmp_path):
    """B-49. The advocate's explanation stands on the file, so the claim is not acted against at the desk."""
    scenarios, store = world
    plain = process(scenarios["S4"].claim, store, tools, llm=crew()[0], out_dir=tmp_path / "plain")
    assert plain.action is Action.SHOW_CAUSE                      # what the case does without a defence
    d = process(scenarios["S4"].claim, store, tools, llm=crew(advocacy=STANDS, advocate_calls=READ_IT)[0],
                out_dir=tmp_path / "defended")
    assert d.action in (Action.FIELD_AUDIT, Action.NO_ACTION)
    assert "DEFENCE_UNEXCLUDED" in d.reason_codes
    assert d.defence_excluded is False and d.defence
    assert "provider_advocate" in d.agents and not d.degraded
    assert "## The hospital's side (Provider Advocate)" in artefact(d, tmp_path / "defended")


def test_a_defence_the_reviewer_disputes_stops_nothing(world, tools, tmp_path):
    """The advocate is checked like every other reading: a disputed defence never reaches the policy."""
    scenarios, store = world
    dispute = [{"finding": "advocacy", "reason": "No LAMA form is on this claim, so the explanation has nothing "
                                                 "on file to rest on."}]
    d = process(scenarios["S4"].claim, store, tools,
                llm=crew(advocacy=STANDS, disputes=dispute, advocate_calls=READ_IT)[0],
                out_dir=tmp_path / "disputed_defence")
    assert d.action is Action.SHOW_CAUSE and "DEFENCE_UNEXCLUDED" not in d.reason_codes
    assert not d.degraded


def test_a_defence_can_never_release_a_claim(world, tools, tmp_path):
    """It slows a case down; it does not decide that fraud did not happen."""
    scenarios, store = world
    for sid in ("S1", "S4"):
        reads = [[("read_document", {"claim_id": f"CLM-{sid[1:].zfill(3)}", "doc_type": "discharge_summary"})]]
        d = process(scenarios[sid].claim, store, tools, llm=crew(advocacy=STANDS, advocate_calls=reads)[0],
                    out_dir=tmp_path / f"nr{sid}")
        assert d.action is not Action.RELEASE_CLAIM, sid


def test_a_rules_run_records_no_router_plan_and_no_defence(world, tools, tmp_path):
    """A degraded decision must not look like the crew's (B-46)."""
    scenarios, store = world
    d = process(scenarios["S4"].claim, store, tools, llm=None, out_dir=tmp_path / "rules9")
    assert d.degraded and d.opened_by_router == [] and d.defence is None and d.defence_excluded is None


def test_a_stance_without_a_citation_goes_back_to_the_agent(world, tools, tmp_path):
    """F-63. A finding at 0.93 that cites nothing is not evidence, whatever its confidence says."""
    scenarios, store = world
    model, _, agents = crew(report={"citation": ""})
    d = process(scenarios["S1"].claim, store, tools, llm=model, out_dir=tmp_path / "nocite")
    prompts = [r["prompt"] for r in agents.requests if r["agent"] == "investigator"]
    assert any("has to say what it rests on" in p for p in prompts)
    assert d.degraded          # this fake never revises, so the guardrail stops the crew


def test_the_billing_analyst_cannot_state_an_excess_it_never_computed(world, tools, tmp_path):
    """F-63. The money question is arithmetic; a stance without claim_tariff is arithmetic nobody did."""
    scenarios, store = world
    money = [{"track": "billing", "reason": "The amount claimed should be checked against the published rate."}]
    model, _, agents = crew(opens=money, billing={"supports_fraud": True, "confidence": 0.9,
                                                  "conclusion": "The amount is far above the rate.",
                                                  "citation": "implant invoice"})
    process(scenarios["S1"].claim, store, tools, llm=model, out_dir=tmp_path / "nosum")
    prompts = [r["prompt"] for r in agents.requests if r["agent"] == "billing"]
    assert any("without using claim_tariff" in p for p in prompts)


def test_a_billing_stance_stands_once_the_tariff_is_computed(world, tools, tmp_path):
    """The same finding passes when the analyst actually used the tool."""
    scenarios, store = world
    money = [{"track": "billing", "reason": "The amount claimed should be checked against the published rate."}]
    model, _, _ = crew(opens=money, billing_calls=[[("claim_tariff", {})]],
                       billing={"supports_fraud": True, "confidence": 0.9,
                                "conclusion": "Rs 60,000 of the amount is unaccounted for.",
                                "citation": "implant invoice"})
    d = process(scenarios["S1"].claim, store, tools, llm=model, out_dir=tmp_path / "summed")
    billing = next(f for f in d.findings if f.agent == "billing_audit")
    assert (billing.supports_fraud, billing.confidence) == (True, 0.9) and not d.degraded
    assert "claim_tariff" in [t.tool for t in d.trail if t.agent == "billing_analyst"]


def test_a_defence_that_would_stop_an_action_must_have_read_the_file(world, tools, tmp_path):
    """F-63. excluded=false stops an action no human has reviewed, so it may not rest on the summary alone."""
    scenarios, store = world
    stands = {"explanation": "The referral on file permits a private hospital to bill this reserved package.",
              "excluded": False, "citation": "the referral letter on this claim"}
    model, _, agents = crew(advocacy=stands)            # the advocate calls no tool
    process(scenarios["S4"].claim, store, tools, llm=model, out_dir=tmp_path / "unread")
    prompts = [r["prompt"] for r in agents.requests if r["agent"] == "advocate"]
    assert any("must rest on the file itself" in p for p in prompts)


def test_the_reviewer_cannot_claim_a_dispute_its_list_does_not_contain(world, tools, tmp_path):
    """F-63, seen live on S8: a summary reading "the desk audit is disputed because ..." beside an empty disputes
    list, so the artefact said "No finding disputed" and nothing was set aside."""
    scenarios, store = world
    model, _, agents = crew(review_summary="The desk audit is disputed because it ignores the missing reports.")
    process(scenarios["S1"].claim, store, tools, llm=model, out_dir=tmp_path / "saidso")
    prompts = [r["prompt"] for r in agents.requests if r["agent"] == "reviewer"]
    assert any("summary says you disputed a finding" in p for p in prompts)


def test_a_review_that_disputes_nothing_and_says_so_passes(world, tools, tmp_path):
    """The check must not fire on an honest negative: "no finding is disputed" is not a claim of a dispute."""
    scenarios, store = world
    model, _, agents = crew(review_summary="No finding is disputed; each reading holds against what it cites.")
    d = process(scenarios["S1"].claim, store, tools, llm=model, out_dir=tmp_path / "clean")
    prompts = [r["prompt"] for r in agents.requests if r["agent"] == "reviewer"]
    assert not any("summary says you disputed" in p for p in prompts)
    assert not d.degraded and d.disputed == []


def test_the_artefact_says_what_the_router_added_and_why(world, tools, tmp_path):
    """B-48: the roster is auditable -- which agents worked the case, and on whose decision."""
    scenarios, store = world
    reason = "The amount claimed should be checked against the published package rate before acting."
    model, _, _ = crew(opens=[{"track": "billing", "reason": reason}],
                       billing_calls=[[("claim_tariff", {})]])
    d = process(scenarios["S1"].claim, store, tools, llm=model, out_dir=tmp_path / "plan")
    assert d.opened_by_router == ["billing"] and d.router_reasons == [f"billing: {reason}"]
    text = artefact(d, tmp_path / "plan")
    assert "## Work the Case Router opened" in text and reason in text


def test_a_dispute_cannot_set_aside_a_reading_that_rests_on_a_measurement(world, tools, tmp_path):
    """F-61. On S5 the claim store itself finds a document byte-identical to another claim's. A reading that supports
    fraud there repeats a comparison, so the reviewer's dispute is refused and recorded, and the reading keeps its
    weight: removing it would let the reviewer erase a fact and clear the case on the one reading left standing."""
    scenarios, store = world
    model, _, _ = crew(report=CONFIRMED, disputes=[MEASURED], investigator=S5_DESK)
    d = process(scenarios["S5"].claim, store, tools, llm=model, out_dir=tmp_path / "measured")
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert d.disputes_refused == ["desk_audit"] and d.disputed == []
    assert (desk.supports_fraud, desk.confidence) == (True, 0.90)      # the reading kept its weight
    assert "the dispute was refused" in desk.conclusion and "byte-identical" in desk.conclusion
    assert not d.degraded
    text = artefact(d, tmp_path / "measured")
    assert "Disputed, and the dispute refused: desk_audit" in text
    assert "never a measurement" in text


def test_the_refusal_guards_only_the_reading_the_measurement_backs(tools):
    """The refusal is narrow. On a case whose documents the store finds byte-identical, a dispute of the *medical*
    reading -- which rests on clinical judgement, not on that comparison -- still applies in full. Only the reading
    that repeats the measurement is protected."""
    from crew import guardrails
    from crew.agent_tools import CaseFile
    from crew.run import intake
    from generate.benchmark import build_benchmark
    from rules import triggers

    cases, store = build_benchmark(tools)
    case_t7 = next(c for c in cases if c.case_id == "BCH-T7-03")
    _problems, hospital, package = intake(case_t7.claim, tools)
    hit = triggers.primary(triggers.evaluate(case_t7.claim, hospital, package, store, tools))
    case = CaseFile(claim=case_t7.claim, hit=hit, hospital=hospital, package=package, store=store, tools=tools)
    assert store.identical(case_t7.claim), "the fixture must carry the measurement this test is about"

    desk = _Report(True, 0.90, "The progress notes are byte-identical to the previous admission's.", "claim documents")
    medical = _Report(False, 0.85, "Three distinct acute episodes, each clinically indicated.", "clinical record")
    findings = guardrails.guarded_findings(case, desk, medical, None, _Review("medical_audit"))
    assert case.disputed == ["medical_audit"] and case.disputes_refused == []
    med = next(f for f in findings if f.agent == "medical_audit")
    assert (med.supports_fraud, med.confidence) == (None, 0.0)


def test_refusing_a_dispute_leaves_exactly_what_no_dispute_would_have_left(world, tools, tmp_path):
    """The reviewer gains no power from a refused dispute and loses none beyond the measurement: the decision is the
    one the case would have reached had the reviewer said nothing."""
    scenarios, store = world
    quiet = process(scenarios["S5"].claim, store, tools, llm=crew(report=CONFIRMED, investigator=S5_DESK)[0], out_dir=tmp_path / "quiet")
    refused = process(scenarios["S5"].claim, store, tools, llm=crew(report=CONFIRMED, disputes=[MEASURED], investigator=S5_DESK)[0],
                      out_dir=tmp_path / "refused")
    assert quiet.disputed == [] and quiet.disputes_refused == []
    assert refused.disputes_refused == ["desk_audit"]
    assert (refused.action, refused.confidence) == (quiet.action, quiet.confidence)


def test_a_rules_run_records_no_refused_dispute(world, tools, tmp_path):
    """A degraded or rules-only decision must not look like the crew's (B-46)."""
    scenarios, store = world
    d = process(scenarios["S5"].claim, store, tools, llm=None, out_dir=tmp_path / "rules")
    assert d.degraded and d.disputed == [] and d.disputes_refused == []


def test_desk_and_medical_readings_that_disagree_buy_field_evidence(world, tools, tmp_path):
    model, _, _ = crew(report=CLEARED, medical={"supports_fraud": True, "confidence": 0.85,
                                                "conclusion": "A same-day discharge after this procedure is not "
                                                              "clinically plausible."})
    scenarios, store = world
    d = process(scenarios["S3"].claim, store, tools, llm=model, out_dir=tmp_path)
    medical = next(f for f in d.findings if f.agent == "medical_audit")
    assert (medical.supports_fraud, medical.confidence) == (True, 0.85)
    assert d.action is Action.FIELD_AUDIT and "BELOW_CONFIDENCE_FLOOR" in d.reason_codes and not d.degraded
    assert "medical_audit" in d.conflicts[0] and "desk_audit" in d.conflicts[0]


@pytest.mark.parametrize("sid, dispute, feedback", [
    ("S3", {**DISPUTE, "finding": "field_reports"}, "is not a finding you can dispute"),
    ("S3", {**DISPUTE, "finding": "registry_verification"}, "is not a finding you can dispute"),
    ("S4", {**DISPUTE, "finding": "medical_audit"}, "is not a finding you can dispute"),       # no medical audit on R1
    ("S3", {**DISPUTE, "reason": "Not convincing."}, "needs its reason in at least one full sentence"),
])
def test_the_reviewer_may_dispute_only_a_reading_on_the_case_and_must_say_why(world, tools, tmp_path, sid, dispute,
                                                                                feedback):
    model, _, agents = crew(report=CLEARED if sid == "S3" else NULL, disputes=[dispute], revised_disputes=[])
    scenarios, store = world
    d = process(scenarios[sid].claim, store, tools, llm=model, out_dir=tmp_path)
    prompts = [r["prompt"] for r in agents.requests if r["agent"] == "reviewer"]
    assert feedback not in prompts[0] and any(feedback in p for p in prompts)
    assert not d.degraded and d.disputed == []


def test_a_reviewer_that_keeps_disputing_what_it_cannot_degrades_the_case(world, tools, tmp_path):
    model, _, _ = crew(report=CLEARED, disputes=[{**DISPUTE, "finding": "registry_verification"}])
    scenarios, store = world
    d = process(scenarios["S3"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.degraded and d.agents == [] and d.disputed == []


def test_disputes_count_only_against_readings(world, tools):
    """The second line behind the review guardrail: recorded field evidence and registry lookups cannot be disputed
    away, and an inconclusive reading has nothing to set aside."""
    from types import SimpleNamespace
    from crew.agent_tools import CaseFile
    from crew.crew_llm import FieldReview, InvestigationReport, MedicalReport
    scenarios, store = world
    claim = scenarios["S8"].claim
    hospital, package = tools.registry_lookup(claim.hospital_ref), tools.hbp_lookup(claim.package_code)
    hit = triggers.primary(triggers.evaluate(claim, hospital, package, store, tools))
    case = CaseFile(claim=claim, hit=hit, hospital=hospital, package=package, store=store, tools=tools)
    desk = InvestigationReport(supports_fraud=None, confidence=0.0, conclusion="Cannot settle.", citation="x")
    medical = MedicalReport(supports_fraud=True, confidence=0.8, conclusion="Not indicated.", citation="x")
    undisputed = guardrails.guarded_findings(case, desk, medical, FieldReview(assessments=[]))
    reasons = "The finding does not follow from the documents it cites, as a reading of them shows."
    review = SimpleNamespace(disputes=[SimpleNamespace(finding=f, reason=reasons)
                                       for f in ("field_review", "registry_verification", "desk_audit")])
    disputed = guardrails.guarded_findings(case, desk, medical, FieldReview(assessments=[]), review)
    assert disputed == undisputed and case.disputed == []
    review.disputes.append(SimpleNamespace(finding="medical_audit", reason=reasons))
    disputed = guardrails.guarded_findings(case, desk, medical, FieldReview(assessments=[]), review)
    assert case.disputed == ["medical_audit"]
    assert [(f.agent, f.supports_fraud) for f in disputed] == [(f.agent, None if f.agent == "medical_audit" else
                                                                 f.supports_fraud) for f in undisputed]


def test_a_medical_stance_on_repeat_admissions_needs_the_admissions_read(world, tools, tmp_path):
    scenarios, store = world
    unexamined = crew(report=NULL, medical={"supports_fraud": True, "confidence": 0.9})[0]
    assert process(scenarios["S7"].claim, store, tools, llm=unexamined, out_dir=tmp_path / "a").degraded
    calls = [[("beneficiary_claim_history", {})], [("read_document", {"claim_id": "CLM-S07P1",
                                                                       "doc_type": "clinical_notes"})]]
    model, _, agents = crew(report=NULL, medical_calls=calls, medical={
        "supports_fraud": True, "confidence": 0.9,
        "conclusion": "Three admissions record the same fever with no investigation to support any of them."})
    d = process(scenarios["S7"].claim, store, tools, llm=model, out_dir=tmp_path / "b")
    assert [t.tool for t in d.trail if t.agent == "medical_auditor"] == ["beneficiary_claim_history", "read_document"]
    medical = next(f for f in d.findings if f.agent == "medical_audit")
    assert (medical.supports_fraud, medical.confidence) == (True, 0.9)
    assert not d.degraded and d.action is Action.SHOW_CAUSE


def test_the_medical_auditor_is_bound_by_the_desks_preconditions(world, tools, tmp_path):
    """S7's file lacks mandatory investigation reports and case papers: no reading may clear it, clinical or not."""
    calls = [[("beneficiary_claim_history", {})], [("read_document", {"claim_id": "CLM-S07P2",
                                                                       "doc_type": "clinical_notes"})]]
    model, _, _ = crew(report=NULL, medical_calls=calls, medical={"supports_fraud": False, "confidence": 0.95})
    scenarios, store = world
    d = process(scenarios["S7"].claim, store, tools, llm=model, out_dir=tmp_path)
    medical = next(f for f in d.findings if f.agent == "medical_audit")
    assert medical.supports_fraud is None and "mandatory documents are missing" in medical.conclusion
    assert d.action is Action.FIELD_AUDIT and not d.degraded


def test_the_liaison_files_the_committee_brief_on_a_referral(world, tools, tmp_path):
    model, _, agents = crew()
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert [(t.agent, t.tool) for t in d.trail][:3] == [("committee_liaison", "enforcement_decision"),
                                                        ("committee_liaison", "access_impact"),
                                                        ("committee_liaison", "file_committee_brief")]
    assert d.committee_brief.summary.startswith("FAKE-LIAISON-BRIEF") and len(d.committee_brief.options) == 2
    results = dict(r for req in agents.requests if req["agent"] == "liaison" for batch in req["history"] for r in batch)
    assert "The hospital is NOT suspended" in results["enforcement_decision"]
    assert "ONLY these specialties are at stake" in results["access_impact"]
    text = artefact(d, tmp_path)
    assert "## Brief for the State Empanelment Committee" in text and "| Suspend with a transition window |" in text
    assert "**Recommendation.** Recovery and penalty without suspension" in text
    assert "Should the Committee suspend and accept the access loss" not in text     # the brief replaces the template


def test_the_liaison_works_only_on_a_referral(world, tools, tmp_path):
    scenarios, store = world
    for sid, report in (("S3", CLEARED), ("S4", NULL)):          # a release, a show-cause notice
        model, _, agents = crew(report=report)
        d = process(scenarios[sid].claim, store, tools, llm=model, out_dir=tmp_path / sid)
        assert "liaison" not in speakers(agents) and "committee_liaison" not in d.agents and d.committee_brief is None


def test_an_overstated_brief_is_sent_back_and_revised(world, tools, tmp_path):
    bad = {**GOOD_BRIEF, "summary": "HOSP-26104 has been suspended, leaving Bahraich without healthcare. "
                                    + GOOD_BRIEF["summary"]}
    model, _, _ = crew(briefs=[bad, GOOD_BRIEF])
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    attempts = [t for t in d.trail if t.tool == "file_committee_brief"]
    assert [t.refused for t in attempts] == [True, False]
    assert "brief rejected: the summary overstates the access loss" in attempts[0].outcome
    assert "the summary says the hospital is suspended" in attempts[0].outcome
    assert "has been suspended" not in artefact(d, tmp_path) and d.committee_brief.summary == GOOD_BRIEF["summary"]


def test_a_liaison_that_files_nothing_leaves_the_standard_question(world, tools, tmp_path):
    model, _, _ = crew(liaison="silent")
    scenarios, store = world
    d = process(scenarios["S2"].claim, store, tools, llm=model, out_dir=tmp_path)
    assert d.committee_brief is None and not d.degraded and d.acted_by == "enforcement_officer"
    assert "Should the Committee suspend and accept the access loss" in artefact(d, tmp_path)


@pytest.mark.parametrize("change, problem", [
    ({"options": GOOD_BRIEF["options"][:1]}, "lists 1 options"),
    ({"recommendation": "Suspend. The hospital has been suspended already, which settles it for the district."},
     "the recommendation says the hospital is suspended"),
    ({"options": [GOOD_BRIEF["options"][0], {"option": "De-list cardiology",
                                              "consequence": "Cardiology at HOSP-26104 has been de-listed for good."}]},
     "option 2's consequence says a specialty has been de-listed"),
    ({"summary": "Too short."}, "the summary is 2 words long"),
    ({"question": "Should HOSP-00311 take the patients instead of this hospital?"}, "names another hospital"),
])
def test_brief_checks(change, problem):
    from crew.schemas import CommitteeOption
    b = {**GOOD_BRIEF, **change}
    options = [CommitteeOption(**o) for o in b["options"]]
    found = guardrails.brief_problems(b["summary"], options, b["recommendation"], b["question"], "escalate_to_sec",
                                      "HOSP-26104")
    assert any(problem in p for p in found), found


def test_the_good_brief_passes_its_checks():
    from crew.schemas import CommitteeOption
    options = [CommitteeOption(**o) for o in GOOD_BRIEF["options"]]
    assert guardrails.brief_problems(GOOD_BRIEF["summary"], options, GOOD_BRIEF["recommendation"],
                                     GOOD_BRIEF["question"], "escalate_to_sec", "HOSP-26104") == []
