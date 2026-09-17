"""A scripted model that plays all six agents through the real adapters and CrewAI's real tool loop.

`Agents` decides, from the system prompt, which agent it is speaking as and, from the conversation, what it has
already called and what came back -- as a model would. The provider fakes below translate each request into that
view and its answer back into Gemini or Anthropic response objects, so a test exercises the adapter's tool protocol,
CrewAI's execution of the tools, the guardrails and the policy exactly as a live run does.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

from anthropic.types.beta import BetaTextBlock, BetaThinkingBlock, BetaToolUseBlock
from google.genai import types

GOOD_DRAFT = ("FAKE-OFFICER-DRAFT: the findings on file were weighed against the trigger, and the decision described "
              "above follows from them. This explanation sets out what the action does, what it does not do, and what "
              "happens next, so that the hospital and any reviewer can respond to the evidence directly.")
BILLING = {"supports_fraud": None, "confidence": 0.0,
           "conclusion": "The financial documents cannot settle whether the amount is accounted for.",
           "citation": "claim documents"}
# A defence that fails on the file: the default must not silently stop actions in every test written before it.
ADVOCACY = {"explanation": "The file offers no innocent account of what was flagged that the documents will bear.",
            "excluded": True, "citation": "claim documents"}
ROUTE_SUMMARY = "The rules have opened what this case needs."
REPORT = {"supports_fraud": True, "confidence": 0.93, "conclusion": "The documents support the suspicion.",
          "citation": "discharge_summary"}
MEDICAL = {"supports_fraud": None, "confidence": 0.0, "conclusion": "The clinical record cannot settle the question.",
           "citation": "discharge_summary"}
REVIEW_SUMMARY = "FAKE-REVIEWER: each finding follows from the documents it cites."
GOOD_BRIEF = {
    "summary": ("FAKE-LIAISON-BRIEF: the findings on file support the suspicion at the confidence the decision states, "
                "and the access facts show which specialties would lose their only real provider in the district if "
                "the hospital were taken out of the scheme. The Committee weighs the two together before deciding."),
    "options": [{"option": "Suspend with a transition window",
                 "consequence": "Enforcement proceeds while patients move to the nearest alternative provider."},
                {"option": "Recovery and penalty without suspension",
                 "consequence": "The district keeps its provider while the amount is recovered and the hospital "
                                "is monitored."}],
    "recommendation": ("Recovery and penalty without suspension, because the access facts show a specialty that would "
                       "lose its only real provider."),
    "question": "Should the Committee suspend the hospital or recover and penalise it without suspension?"}

# CrewAI's system prompt opens "You are <role>." (crewai/translations/en.json, role_playing).
ROLES = {"Desk Investigator": "investigator", "Medical Auditor": "medical", "Field Evidence Analyst": "field",
         "Audit Reviewer": "reviewer", "Committee Liaison": "liaison", "Enforcement Officer": "officer",
         "Case Router": "router", "Billing & Tariff Analyst": "billing", "Provider Advocate": "advocate"}
SPEAKER = re.compile(r"You are (" + "|".join(ROLES) + r")\b")


class Agents:
    """All nine agents, from a script.

    router: `opens` as its plan (default: nothing beyond what the rules mandate); once a guardrail has sent the
      plan back, `revised_opens` instead, when given.
    billing: batches of tool calls (`billing_calls`), then `billing` as its finding (default: inconclusive).
    advocate: batches of tool calls (`advocate_calls`), then `advocacy` as its answer (default: a defence the file
      excludes, so no action is stopped). A defence that is NOT excluded must have read the file first (F-63).

    investigator: batches of tool calls, one batch per turn, then `report` as the final answer. With `lazy_first`, the
      first attempt answers at once without calling anything (to exercise the task guardrail).
    medical: batches of tool calls (`medical_calls`), then `medical` as its finding (default: inconclusive).
    field: `assessments` as its answer (default: none).
    reviewer: batches of tool calls (`review_calls`), then `disputes` as its answer (default: none); once a guardrail
      has sent the review back, `revised_disputes` instead, when given.
    liaison: "comply" reads the decision and the access, then files `briefs` in turn; "silent" never calls a tool.
    officer: "comply" reads the decision, reads access when told to, and executes with `drafts` in turn; "silent" never
      calls a tool. `wrong_tool` is tried once before the right one.
    """

    def __init__(self, report=None, investigator=(), officer="comply", drafts=(GOOD_DRAFT,), wrong_tool=None,
                 questions=("Did the beneficiary attend the hospital on each of the admission dates?",),
                 documents=("The indoor case papers for the admission",), lazy_first=False, medical=None,
                 medical_calls=(), assessments=(), disputes=(), revised_disputes=None, review_calls=(),
                 liaison="comply", briefs=(GOOD_BRIEF,), opens=(), billing=None, billing_calls=(), advocacy=None,
                 revised_opens=None, review_summary=None, advocate_calls=()):
        self.report = {**REPORT, **(report or {})}
        self.medical = {**MEDICAL, **(medical or {})}
        self.investigator, self.officer = [list(b) for b in investigator], officer
        self.medical_calls, self.review_calls = [list(b) for b in medical_calls], [list(b) for b in review_calls]
        self.assessments, self.disputes, self.revised_disputes = list(assessments), list(disputes), revised_disputes
        self.drafts, self.wrong_tool = list(drafts), wrong_tool
        self.liaison, self.briefs = liaison, list(briefs)
        self.questions, self.documents, self.lazy_first = list(questions), list(documents), lazy_first
        self.opens, self.revised_opens = list(opens), revised_opens
        self.review_summary = review_summary or REVIEW_SUMMARY
        self.billing = {**BILLING, **(billing or {})}
        self.billing_calls = [list(b) for b in billing_calls]
        self.advocacy = {**ADVOCACY, **(advocacy or {})}
        self.advocate_calls = [list(b) for b in advocate_calls]
        self.requests: list[dict] = []

    def respond(self, system: str, tools: list[str], history: list[list[tuple[str, str]]], prompt: str):
        """history: one list per tool-call turn of (tool name, result text). Returns ('calls', [(name, args)]) or
        ('text', answer)."""
        m = SPEAKER.search(system)
        agent = ROLES[m.group(1)] if m else "other"
        self.requests.append({"agent": agent, "tools": tools, "history": history, "prompt": prompt})
        turn = len(history)
        if agent == "investigator":
            if self.lazy_first and "without examining" not in prompt:
                return "text", json.dumps(self.report)
            if tools and turn < len(self.investigator):
                return "calls", self.investigator[turn]
            return "text", json.dumps(self.report)
        if agent == "medical":
            if tools and turn < len(self.medical_calls):
                return "calls", self.medical_calls[turn]
            return "text", json.dumps(self.medical)
        if agent == "field":
            return "text", json.dumps({"assessments": self.assessments})
        if agent == "router":
            opens = (self.revised_opens if self.revised_opens is not None and "Revise your plan" in prompt
                     else self.opens)
            return "text", json.dumps({"open": opens, "summary": ROUTE_SUMMARY})
        if agent == "billing":
            if tools and turn < len(self.billing_calls):
                return "calls", self.billing_calls[turn]
            return "text", json.dumps(self.billing)
        if agent == "advocate":
            if tools and turn < len(self.advocate_calls):
                return "calls", self.advocate_calls[turn]
            return "text", json.dumps(self.advocacy)
        if agent == "reviewer":
            if tools and turn < len(self.review_calls):
                return "calls", self.review_calls[turn]
            disputes = (self.revised_disputes if self.revised_disputes is not None and "Revise your review" in prompt
                        else self.disputes)
            return "text", json.dumps({"disputes": disputes, "summary": self.review_summary})
        if agent == "liaison":
            return self._liaison(tools, [r for batch in history for r in batch])
        if agent == "officer":
            return self._officer(tools, [r for batch in history for r in batch])
        return "text", json.dumps(self.report)            # a converter call

    def _liaison(self, tools, results):
        if not tools or self.liaison == "silent":
            return "text", "I have read the case."
        called = [n for n, _ in results]
        for reading in ("enforcement_decision", "access_impact"):       # one at a time, so the trail's order is fixed
            if reading not in called:
                return "calls", [(reading, {})]
        last = results[-1][1]
        if "Filed:" in last or "briefs have been rejected" in last or "refers nothing" in last:
            return "text", "The brief is filed."
        attempt = called.count("file_committee_brief")
        return "calls", [("file_committee_brief", self.briefs[min(attempt, len(self.briefs) - 1)])]

    def _officer(self, tools, results):
        if not tools or self.officer == "silent":
            return "text", "I have read the case."
        if not results:
            return "calls", [("enforcement_decision", {})]
        decision = next((t for n, t in results if n == "enforcement_decision"), "")
        right = m.group(1) if (m := re.search(r"Execute it with: (\w+)", decision)) else None
        name, last = results[-1]
        if "Executed:" in last or "drafts have been rejected" in last or right is None:
            return "text", f"Done: {name}."
        called = [n for n, _ in results]
        if self.wrong_tool and self.wrong_tool not in called:
            return "calls", [(self.wrong_tool, self.arguments(self.wrong_tool, self.drafts[0]))]
        if "read access_impact" in decision and "access_impact" not in called:
            return "calls", [("access_impact", {})]
        attempt = called.count(right)
        return "calls", [(right, self.arguments(right, self.drafts[min(attempt, len(self.drafts) - 1)]))]

    def arguments(self, tool, draft):
        args = {"explanation": draft}
        if tool == "order_field_audit":
            args["questions_for_field_team"] = self.questions
        if tool == "issue_show_cause_notice":
            args["documents_requested"] = self.documents
        return args


# ── Gemini ───────────────────────────────────────────────────────────────────

def _part(p):
    return p if isinstance(p, dict) else p.model_dump(exclude_none=True)


def gemini_view(contents, config):
    system = str(config.get("system_instruction") or "")
    tools = [d["name"] for t in config.get("tools") or [] for d in t["function_declarations"]]
    history, prompt = [], []
    for c in contents:
        role = c["role"] if isinstance(c, dict) else c.role
        parts = [_part(p) for p in (c["parts"] if isinstance(c, dict) else c.parts)]
        if role == "model" and any("function_call" in p for p in parts):
            history.append([])
        for p in parts:
            if "function_response" in p:
                r = p["function_response"]["response"]
                history[-1].append((p["function_response"]["name"], str(r.get("output", r.get("error", "")))))
            elif role == "user" and "text" in p:
                prompt.append(p["text"])
    return system, tools, history, "\n".join(prompt)


def gemini_answer(agents: Agents, contents, config, n: int):
    kind, value = agents.respond(*gemini_view(contents, config))
    if kind == "calls":
        parts = [types.Part(function_call=types.FunctionCall(name=name, args=args),
                            thought_signature=f"sig-{n}".encode() if i == 0 else None)
                 for i, (name, args) in enumerate(value)]
        return SimpleNamespace(text=None, prompt_feedback=None, response_id=f"resp_{n}", candidates=[SimpleNamespace(
            finish_reason=types.FinishReason.STOP, content=types.Content(role="model", parts=parts))])
    return SimpleNamespace(text=value, prompt_feedback=None, response_id=f"resp_{n}", candidates=[SimpleNamespace(
        finish_reason=types.FinishReason.STOP, content=types.Content(role="model", parts=[types.Part(text=value)]))])


# ── Claude ───────────────────────────────────────────────────────────────────

def claude_view(kw):
    system = str(kw.get("system") or "")
    tools = [t["name"] for t in kw.get("tools") or []]
    history, prompt, names = [], [], {}
    for m in kw["messages"]:
        content = m["content"]
        if isinstance(content, str):
            if m["role"] == "user":
                prompt.append(content)
            continue
        blocks = [b if isinstance(b, dict) else b.model_dump() for b in content]
        if m["role"] == "assistant" and any(b.get("type") == "tool_use" for b in blocks):
            names.update({b["id"]: b["name"] for b in blocks if b.get("type") == "tool_use"})
            history.append([])
        for b in blocks:
            if b.get("type") == "tool_result":
                history[-1].append((names.get(b["tool_use_id"], "?"), str(b.get("content"))))
            elif m["role"] == "user" and b.get("type") == "text":
                prompt.append(b["text"])
    return system, tools, history, "\n".join(prompt)


def claude_answer(agents: Agents, kw, n: int):
    kind, value = agents.respond(*claude_view(kw))
    if kind == "calls":
        content = [BetaThinkingBlock(type="thinking", thinking=f"weighing step {n}", signature=f"thinking-sig-{n}")]
        content += [BetaToolUseBlock(type="tool_use", id=f"toolu_{n}_{i}", name=name, input=args)
                    for i, (name, args) in enumerate(value)]
        return SimpleNamespace(stop_reason="tool_use", stop_details=None, _request_id=f"req_{n}", content=content)
    return SimpleNamespace(stop_reason="end_turn", stop_details=None, _request_id=f"req_{n}",
                           content=[BetaTextBlock(type="text", text=value)])
