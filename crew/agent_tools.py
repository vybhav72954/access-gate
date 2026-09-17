"""The tools the six agents call (docs/02-LLD.md §6a).

Evidence tools fetch what the case file alone does not hold: the beneficiary's other admissions, documents filed byte
for byte on other claims, the surgeon's other claims that day, and any related document in full, compared side by side.
The guidebook's own desk checks need them: trigger 7 asks the desk to "cross check similarities in supporting documents
across all claims of the same member", trigger 6 to "verify the set of claims where the same document was used". The
Desk Investigator has all of them; the Medical Auditor, the Field Evidence Analyst and the Audit Reviewer have the ones
their questions need. Each agent decides what to fetch and what the evidence means.

The decision tools read the decision the enforcement policy made (rules/policy.py) and what it would do to access. The
Enforcement Officer executes the decision through action tools, each of which executes only its own action and only
when that is the policy's decision; the Committee Liaison files the brief the State Empanelment Committee receives with
an escalation or a de-listing referral. Neither can change a decision.

Three rules hold for every tool:
* it reads only this case: the claim, and claims a tool has shown to be related to it. Asking for any other claim is
  refused, so an agent cannot browse the corpus;
* no evidence tool reveals network adequacy: whether a hospital committed fraud must not depend on who would lose
  access if it did. No tool reveals another hospital's identifier either (EC-1);
* every call, answered or refused, joins the case's trail, which the decision log and the artefact carry.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, ClassVar

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from crew import guardrails
from crew.schemas import (Action, Adequacy, AgentFinding, Claim, CommitteeBrief, CommitteeOption, Hospital, Package,
                          ToolCall, TriggerHit)
from crew.tools import Tools
from rules.policy import CONFIDENCE_FLOOR, Verdict
from rules.triggers import ClaimStore

ROUTER = "case_router"
BILLING = "billing_analyst"
ADVOCATE = "provider_advocate"
INVESTIGATOR = "desk_investigator"
MEDICAL = "medical_auditor"
FIELD = "field_evidence_analyst"
REVIEWER = "audit_reviewer"
LIAISON = "committee_liaison"
OFFICER = "enforcement_officer"
DOC_CHARS = 8_000          # a document as a tool returns it; a runaway upload must not exhaust the model's budget
COMPARE_WORDS = 2_000      # words compared by compare_documents (quadratic in the worst case)
MAX_LISTED = 20            # claims one tool lists
MAX_DRAFTS = 3             # rejected drafts before the officer's action tool stops accepting them

ACTION_TOOL = {
    Action.RELEASE_CLAIM: "release_claim",
    Action.SHOW_CAUSE: "issue_show_cause_notice",
    Action.FIELD_AUDIT: "order_field_audit",
    Action.SUSPEND: "suspend_hospital",
    Action.ESCALATE_SEC: "escalate_to_state_committee",
    Action.DELIST_SPECIALTY: "refer_specialty_for_delisting",
    Action.NO_ACTION: "refer_for_human_review",
}
ACCESS_ACTIONS = frozenset({Action.SUSPEND, Action.ESCALATE_SEC, Action.DELIST_SPECIALTY})


class ToolRefusal(Exception):
    """A tool declining a call. CrewAI hands the message back to the agent, which can act on it; the trail records
    `summary` when given -- a rejected draft's own words must not reach the artefact through its trail."""

    def __init__(self, message: str, summary: str | None = None):
        super().__init__(message)
        self.summary = summary


@dataclass(frozen=True)
class Commitment:
    """The action the officer executed, with the text it wrote for it."""
    action: Action
    explanation: str
    field_questions: tuple[str, ...] = ()
    documents_requested: tuple[str, ...] = ()


@dataclass
class CaseFile:
    """One case, as the tools see it, and what the crew establishes about it."""
    claim: Claim
    hit: TriggerHit
    hospital: Hospital
    package: Package
    store: ClaimStore
    tools: Tools
    adequacy: Adequacy | None = None
    network: list[Adequacy] = field(default_factory=list)
    trail: list[ToolCall] = field(default_factory=list)
    related: dict[str, str] = field(default_factory=dict)     # claim id -> how a tool found it
    listed: dict[str, set[str]] = field(default_factory=dict)  # agent -> the claim ids its own tool calls listed
    report: Any = None                                        # the Desk Investigator's report as returned
    medical: Any = None                                       # the Medical Auditor's report (T2, T3, T4, T7)
    field_review: Any = None                                  # the Field Evidence Analyst's (field reports on file)
    review: Any = None                                        # the Audit Reviewer's
    findings: list[AgentFinding] | None = None                # the reports after the evidence rules and disputes
    disputed: list[str] = field(default_factory=list)         # the findings the reviewer disputed
    disputes_refused: list[str] = field(default_factory=list)  # disputes refused: the reading rests on a measurement
    plan: Any = None                                          # the Case Router's plan: which optional tracks open
    opened: list[str] = field(default_factory=list)           # tracks the router opened that were not mandatory
    plan_reasons: list[str] = field(default_factory=list)     # why it opened each of them, for the artefact
    billing: Any = None                                       # the Billing & Tariff Analyst's reading
    defence: Any = None                                       # the Provider Advocate's report
    verdict: Verdict | None = None                            # the policy's decision on those findings
    brief: CommitteeBrief | None = None                       # the brief the Committee Liaison filed
    committed: Commitment | None = None                       # the action the officer executed
    agents: list[str] = field(default_factory=list)           # the agents that worked on the case, in order
    rejected_drafts: int = 0
    rejected_briefs: int = 0
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def record(self, agent: str, tool: str, arguments: dict, outcome: str, refused: bool = False) -> None:
        ref = self.claim.hospital_ref
        args = {k: _short(guardrails.scrub_identifiers(_text(v), ref), 160) for k, v in arguments.items()}
        with self._lock:          # CrewAI runs parallel tool calls on threads
            self.trail.append(ToolCall(agent=agent, tool=tool, arguments=args,
                                       outcome=_short(guardrails.scrub_identifiers(outcome, ref), 300), refused=refused))

    def reveal(self, claim_id: str, how: str, agent: str) -> None:
        """A tool listed `claim_id` as related: any agent on the case may now read it, and `agent` has seen it."""
        with self._lock:
            self.related.setdefault(claim_id, how)
            self.listed.setdefault(agent, set()).add(claim_id)

    def called(self, agent: str) -> set[str]:
        """The tools `agent` has called and been answered by."""
        return {t.tool for t in self.trail if t.agent == agent and not t.refused}

    def accessible(self, claim_id: str) -> Claim:
        wanted = str(claim_id).strip()
        if wanted.upper() == self.claim.claim_id.upper():
            return self.claim
        for cid in self.related:
            if cid.upper() == wanted.upper():
                return self.store.claims[cid]
        raise ToolRefusal(f"{wanted} is neither this claim ({self.claim.claim_id}) nor a claim a tool has shown to be "
                          "related to it. Related claims are listed by beneficiary_claim_history, "
                          "documents_shared_with_other_claims and surgeon_same_day_claims, for the agents that have "
                          "them.")


def _text(value: Any) -> str:
    return "; ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _day(ts: datetime) -> str:
    return f"{ts:%d %b %Y}"


def _clip(text: str, limit: int = DOC_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f" [... {len(text) - limit:,} more characters]"


def _doc_type(value: str) -> str:
    return re.sub(r"[\s-]+", "_", str(value).strip().lower())


# ── the tool base ────────────────────────────────────────────────────────────

class NoArguments(BaseModel):
    """This tool takes no arguments."""


class CaseTool(BaseTool):
    """A tool bound to one case. Subclasses answer with (text for the agent, one line for the trail)."""
    case: Any = Field(default=None, exclude=True, repr=False)
    args_schema: type[BaseModel] = NoArguments
    acting_agent: str = Field(default=INVESTIGATOR, exclude=True)     # whose kit this instance is in: the trail's agent

    def _run(self, **kwargs: Any) -> str:
        shown = self.trail_arguments(kwargs)
        try:
            text, summary = self.answer(**kwargs)
        except ToolRefusal as exc:            # recorded, and handed back to the agent to act on
            self.case.record(self.acting_agent, self.name, shown, f"refused: {exc.summary or exc}", refused=True)
            raise
        except Exception as exc:              # a fault in the tool itself: the same, so the trail shows it
            self.case.record(self.acting_agent, self.name, shown, f"failed ({type(exc).__name__}): {exc}", refused=True)
            raise
        self.case.record(self.acting_agent, self.name, shown, summary)
        return text

    def _validate_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # Arguments that do not fit the schema are rejected before _run: without this, an officer that kept sending a
        # malformed action left no trace, and a live case showed only the decision it read (benchmark BCH-R2-04).
        try:
            return super()._validate_kwargs(kwargs)
        except Exception as exc:
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            self.case.record(self.acting_agent, self.name, self.trail_arguments(kwargs),
                             f"refused: the arguments do not fit the tool ({reason})", refused=True)
            raise

    def answer(self, **kwargs: Any) -> tuple[str, str]:
        raise NotImplementedError

    def trail_arguments(self, kwargs: dict) -> dict:
        return kwargs


def _where(case: CaseFile, other: Claim) -> str:
    if other.hospital_ref == case.claim.hospital_ref:
        return "at this hospital"
    h = case.tools.registry_lookup(other.hospital_ref)
    return f"at another hospital in {case.tools.district_label(h.district_code)}" if h else "at another hospital"


def _package(case: CaseFile, code: str) -> str:
    p = case.tools.hbp_lookup(code)
    return f"{code} - {guardrails.inline(p.package_name)}" if p else f"{code} (not in the package master)"


def _documents(claim: Claim) -> str:
    return ", ".join(d.doc_type for d in claim.documents) or "none"


# ── the evidence tools ───────────────────────────────────────────────────────

class BeneficiaryClaimHistory(CaseTool):
    name: str = "beneficiary_claim_history"
    description: str = ("The beneficiary's other claims on file: admission and discharge dates, how many days before "
                        "this admission, the package, whether at this hospital, and the documents filed on each. Use it "
                        "for repeat admissions (T7), then read or compare their documents.")

    def answer(self) -> tuple[str, str]:
        c = self.case
        others = sorted((o for o in c.store.same_beneficiary(c.claim) if o.claim_id != c.claim.claim_id),
                        key=lambda o: (o.admission_ts, o.claim_id))
        if not others:
            return "No other claim is on file for this beneficiary.", "no other claims"
        shown = others[-MAX_LISTED:]
        lines = [f"Other claims on file for beneficiary {c.claim.beneficiary_ref} (this claim, {c.claim.claim_id}, was "
                 f"admitted {_day(c.claim.admission_ts)}):"]
        if len(others) > len(shown):
            lines.append(f"({len(others) - len(shown)} earlier claims not listed)")
        for o in shown:
            c.reveal(o.claim_id, "same beneficiary", self.acting_agent)
            gap = (c.claim.admission_ts.date() - o.admission_ts.date()).days
            when = f"{abs(gap)} days {'before' if gap >= 0 else 'after'} this admission"
            stay = (f"discharged {_day(o.discharge_ts)} ({o.los_days} days in hospital)" if o.discharge_ts
                    else "no discharge recorded")
            lines.append(f"- {o.claim_id}: admitted {_day(o.admission_ts)} ({when}), {stay}; package "
                         f"{_package(c, o.package_code)}; {_where(c, o)}; ICU flag {o.icu_flag}; documents: "
                         f"{_documents(o)}")
        lines.append("Read any of these documents with read_document, or set two side by side with compare_documents.")
        return "\n".join(lines), f"{len(others)} other claim(s): {', '.join(o.claim_id for o in shown)}"


class DocumentsSharedWithOtherClaims(CaseTool):
    name: str = "documents_shared_with_other_claims"
    description: str = ("Documents on this claim that are byte-identical to a document filed on any other claim, for "
                        "this beneficiary or another: which claim, whose, when and where. Use it for a reused "
                        "document (T6) and for records copied across admissions (T7).")

    def answer(self) -> tuple[str, str]:
        c = self.case
        seen, lines = set(), []
        for doc, other in c.store.identical(c.claim):
            if (doc.sha256, other.claim_id) in seen or len(seen) >= MAX_LISTED:
                continue
            seen.add((doc.sha256, other.claim_id))
            c.reveal(other.claim_id, "shares a document", self.acting_agent)
            same = other.beneficiary_ref == c.claim.beneficiary_ref
            # The text, not the stored hash: a claim built without validation can carry a stale one (F-36).
            filed_as = sorted({d.doc_type for d in other.documents if d.text == doc.text}) or [doc.doc_type]
            lines.append(f"- {doc.doc_type} (sha256 {doc.sha256[:12]}) is also on {other.claim_id}: "
                         f"{'the same beneficiary' if same else 'a different beneficiary, ' + other.beneficiary_ref}, "
                         f"admitted {_day(other.admission_ts)}, {_where(c, other)}, filed there as "
                         f"{', '.join(filed_as)}")
        if not lines:
            return ("No document on this claim is byte-identical to a document on any other claim.",
                    "no shared documents")
        head = "Documents on this claim filed byte for byte on other claims:"
        return "\n".join([head, *lines]), f"{len(lines)} shared document(s): " + ", ".join(sorted({o for _, o in seen}))


class SurgeonSameDayClaims(CaseTool):
    name: str = "surgeon_same_day_claims"
    description: str = ("Other claims recorded for this claim's surgeon with an admission on the same day, with the "
                        "time, the district and the straight-line distance from this hospital's district. Use it for "
                        "the impossible-surgeon trigger (T5).")

    def answer(self) -> tuple[str, str]:
        c = self.case
        if not c.claim.surgeon_reg_no:
            return "No surgeon registration number is recorded on this claim.", "no surgeon recorded"
        day = c.claim.admission_ts.date()
        others = sorted((o for o in c.store.same_surgeon(c.claim)
                         if o.claim_id != c.claim.claim_id and o.admission_ts.date() == day),
                        key=lambda o: (o.admission_ts, o.claim_id))[:MAX_LISTED]
        if not others:
            return (f"Surgeon {c.claim.surgeon_reg_no} has no other claim with an admission on {_day(c.claim.admission_ts)}.",
                    "no other same-day claims")
        lines = [f"Surgeon {c.claim.surgeon_reg_no}: this claim admitted {c.claim.admission_ts:%d %b %Y %H:%M} in "
                 f"{c.tools.district_label(c.hospital.district_code)}. Other claims admitted the same day:"]
        for o in others:
            c.reveal(o.claim_id, "same surgeon, same day", self.acting_agent)
            h = c.tools.registry_lookup(o.hospital_ref)
            km = c.tools.district_km(c.hospital.district_code, h.district_code) if h else None
            distance = "distance unknown" if km is None else f"{km:.0f} km from this hospital's district"
            lines.append(f"- {o.claim_id}: admitted {o.admission_ts:%H:%M}, {_where(c, o)} ({distance}); package "
                         f"{_package(c, o.package_code)}; documents: {_documents(o)}")
        return "\n".join(lines), f"{len(others)} same-day claim(s): {', '.join(o.claim_id for o in others)}"


class ClaimTariff(CaseTool):
    name: str = "claim_tariff"
    description: str = ("The money on this claim, computed: the published package rate, the amount claimed, the "
                        "excess over the rate, and every document on file that could account for it (invoices, "
                        "bills, implant records) with its type. It does NOT read the amounts inside those documents "
                        "-- read_document does that. Use it for the above-rate trigger (R2).")

    def answer(self) -> tuple[str, str]:
        c = self.case
        rate, claimed = c.package.amount_rs, c.claim.amount_claimed
        financial = [d for d in c.claim.documents if any(k in d.doc_type for k in FINANCIAL_DOCS)]
        if rate <= 0:
            head = (f"Package {c.package.package_code} fixes no rate in the master (a per-day package), so there is "
                    f"no published rate to exceed. Amount claimed Rs {claimed:,}.")
            excess = None
        else:
            excess = claimed - rate
            head = (f"Package {c.package.package_code}: published rate Rs {rate:,}; amount claimed Rs {claimed:,}; "
                    f"excess over the rate Rs {excess:,}"
                    + (f" ({excess / rate:.0%} above the rate)." if excess > 0 else " (at or under the rate)."))
        if financial:
            lines = [head, "", "Documents on file that could account for the amount (read each to see what it "
                             "actually covers):"]
            lines += [f"- [{c.claim.claim_id}/{d.doc_type}]" for d in financial]
        else:
            lines = [head, "", "No invoice, bill or implant record is on file for this claim."]
        brief = (f"rate Rs {rate:,}, claimed Rs {claimed:,}"
                 + (f", excess Rs {excess:,}" if excess is not None else ", no fixed rate")
                 + f"; {len(financial)} financial document(s)")
        return "\n".join(lines), brief


FINANCIAL_DOCS = ("invoice", "bill", "implant", "receipt", "estimate")


class DocumentArguments(BaseModel):
    claim_id: str = Field(description="This claim's id, or the id of a claim another tool listed")
    doc_type: str = Field(description="The document type exactly as listed, e.g. discharge_summary")


class ReadDocument(CaseTool):
    name: str = "read_document"
    description: str = ("The full text of one document on this claim or on a claim another tool listed. A claim no "
                        "tool has listed is refused.")
    args_schema: type[BaseModel] = DocumentArguments

    def answer(self, claim_id: str, doc_type: str) -> tuple[str, str]:
        claim = self.case.accessible(claim_id)
        docs = _find(claim, doc_type)
        parts = [f"[{claim.claim_id} / {d.doc_type}{f' {i} of {len(docs)}' if len(docs) > 1 else ''}, sha256 "
                 f"{d.sha256[:12]}]\n{_clip(d.text)}" for i, d in enumerate(docs, start=1)]
        return "\n\n".join(parts), f"read {claim.claim_id}/{docs[0].doc_type} ({sum(len(d.text) for d in docs):,} characters)"


def _find(claim: Claim, doc_type: str):
    docs = [d for d in claim.documents if d.doc_type == _doc_type(doc_type)]
    if not docs:
        raise ToolRefusal(f"{claim.claim_id} has no {doc_type}; its documents are: {_documents(claim)}")
    return docs


class CompareArguments(BaseModel):
    claim_a: str = Field(description="The first claim's id")
    doc_type_a: str = Field(description="The document type on the first claim")
    claim_b: str = Field(description="The second claim's id")
    doc_type_b: str = Field(description="The document type on the second claim")


NUMBER = re.compile(r"\d+(?:[.:/]\d+)*%?")
SENTENCE_END = re.compile(r"(?<=[.;!?])\s+|\n+")


class CompareDocuments(CaseTool):
    name: str = "compare_documents"
    description: str = ("Two documents side by side, from this claim or claims another tool listed: whether they are "
                        "byte-identical, how similar the text is with and without its numbers, the sentences they "
                        "share, and the numbers (vitals, doses, bed numbers, dates) found in both or only one. It "
                        "measures similarity; what similarity means is for you to judge.")
    args_schema: type[BaseModel] = CompareArguments

    def answer(self, claim_a: str, doc_type_a: str, claim_b: str, doc_type_b: str) -> tuple[str, str]:
        ca, cb = self.case.accessible(claim_a), self.case.accessible(claim_b)
        a = "\n".join(d.text for d in _find(ca, doc_type_a))
        b = "\n".join(d.text for d in _find(cb, doc_type_b))
        label = f"{ca.claim_id}/{_doc_type(doc_type_a)} vs {cb.claim_id}/{_doc_type(doc_type_b)}"

        def norm(t: str) -> str:
            return " ".join(t.lower().split())

        def words(t: str) -> list[str]:
            # Word-level, and capped: character-level matching took five seconds a ratio on two 8,000-character
            # documents, and a tool runs inside the agent's loop.
            return norm(t).split()[:COMPARE_WORDS]

        ratio = SequenceMatcher(None, words(a), words(b), autojunk=False).ratio()
        masked = SequenceMatcher(None, words(NUMBER.sub("#", a)), words(NUMBER.sub("#", b)), autojunk=False).ratio()
        sa = {s for s in (norm(x).strip(" .;") for x in SENTENCE_END.split(a)) if len(s) > 3}
        sb = {s for s in (norm(x).strip(" .;") for x in SENTENCE_END.split(b)) if len(s) > 3}
        shared = sa & sb
        na, nb = set(NUMBER.findall(a)), set(NUMBER.findall(b))

        def listed(values: set[str]) -> str:
            return ", ".join(sorted(values)[:25]) or "none"

        lines = [label,
                 f"byte-identical: {'yes' if a == b else 'no'}",
                 f"text similarity: {ratio:.2f} (1.00 = the same words in the same order, ignoring case and spacing)",
                 f"text similarity with every number masked: {masked:.2f}",
                 f"sentences in common: {len(shared)} of {len(sa)} in the first, {len(shared)} of {len(sb)} in the second",
                 f"numbers in both: {listed(na & nb)}",
                 f"numbers only in the first: {listed(na - nb)}",
                 f"numbers only in the second: {listed(nb - na)}"]
        if shared:
            lines.append("shared sentences (up to 5): " + " | ".join(sorted(shared, key=len, reverse=True)[:5]))
        return "\n".join(lines), f"{label}: similarity {ratio:.2f} ({masked:.2f} numbers masked), {len(shared)} shared sentence(s)"


def never_cache(_arguments: Any, _result: Any) -> bool:
    """Every call runs, and so joins the trail. A cached answer skipped the action tools' checks and the one-action
    rule, and hid an agent calling the same tool over and over."""
    return False


# ── the decision tools: the Committee Liaison's and the Enforcement Officer's ─

class OfficerTool(CaseTool):
    """A tool that reads, or acts on, the policy's decision."""

    def verdict(self) -> Verdict:
        if self.case.verdict is None:
            raise ToolRefusal("The enforcement policy has not decided this case: there is nothing to read or execute.")
        return self.case.verdict


class EnforcementDecision(OfficerTool):
    name: str = "enforcement_decision"
    description: str = ("The enforcement policy's decision on this case: the action, what it does and does not do, the "
                        "reason codes, the findings it rests on, and the one action tool that executes it. Read it "
                        "before anything else.")

    def answer(self) -> tuple[str, str]:
        c, v = self.case, self.verdict()
        stance = {True: "supports fraud", False: "opposes fraud", None: "inconclusive"}
        lines = [f"Decision (enforcement policy, rules/policy.py): {v.action.value}",
                 f"What it does, and does not do: {guardrails.ACTION_IN_WORDS.get(v.action.value, v.action.value)}",
                 f"Execute it with: {ACTION_TOOL[v.action]}. Every other action tool refuses.",
                 f"Reason codes: {', '.join(v.reason_codes)}",
                 f"Aggregate confidence: {v.confidence:.2f} (the policy acts on {CONFIDENCE_FLOOR:.2f} or more)"]
        if v.field_channels:
            lines.append(f"Field channels ordered: {', '.join(ch.value for ch in v.field_channels)}")
        if v.conflicts:
            lines.append(f"Conflicts: {'; '.join(v.conflicts)}")
        lines.append(f"Trigger {c.hit.trigger_id} - {c.hit.name}: {guardrails.inline(c.hit.evidence)}")
        lines.append("Findings the decision rests on:")
        for f in c.findings or []:
            where = f" / {f.channel.value}" if f.channel and f.channel.value != f.agent else ""
            lines.append(f"- {f.agent}{where}: {stance[f.supports_fraud]} ({f.confidence:.2f}): "
                         f"{guardrails.inline(f.conclusion)} [{guardrails.inline(f.citation)}]")
        if v.action in ACCESS_ACTIONS:
            lines.append("This action turns on access in the district: read access_impact before drafting.")
        if v.action in (Action.ESCALATE_SEC, Action.DELIST_SPECIALTY):
            lines.append("The Committee Liaison files the brief the State Empanelment Committee receives "
                         "(file_committee_brief); the Enforcement Officer executes the referral and notifies the "
                         "hospital.")
        return "\n".join(lines), f"{v.action.value} ({', '.join(v.reason_codes[:3])})"


class AccessImpact(OfficerTool):
    name: str = "access_impact"
    description: str = ("What a suspension of this hospital would do to access in its district: the district, the "
                        "specialties that would lose their only real provider, and the billed specialty's providers. "
                        "Needed to explain a suspension, an escalation or a de-listing referral.")

    def answer(self) -> tuple[str, str]:
        c, v = self.case, self.verdict()
        facts = guardrails.access_facts(c.adequacy, v.gate, v.at_stake)
        return facts, f"gate {v.gate or 'not reached'}; at stake: {', '.join(a.specialty for a in v.at_stake) or 'none'}"


class Explanation(BaseModel):
    explanation: str = Field(description="The explanation for the people this action affects: plain English, "
                                         "80-180 words, no headings")


class ShowCauseArguments(Explanation):
    documents_requested: list[str] = Field(description="The documents the hospital must produce to answer the "
                                                       "findings, one per item (1-8 items)")


class FieldAuditArguments(Explanation):
    questions_for_field_team: list[str] = Field(description="Questions specific to this case that the field team must "
                                                            "answer on the channels ordered, one per item (1-8 items)")


class ActionTool(OfficerTool):
    """Executes `action` if, and only if, it is the policy's decision, with a publishable explanation."""
    action: ClassVar[Action]
    args_schema: type[BaseModel] = Explanation

    def trail_arguments(self, kwargs: dict) -> dict:
        """Sizes, not text: what the officer wrote is published only once it passes the checks, in its own section."""
        return {k: f"{len(str(v).split())} words" if k == "explanation" else
                f"{len(v)} item(s)" if isinstance(v, (list, tuple)) else f"a {type(v).__name__}, not a list"
                for k, v in kwargs.items()}

    def answer(self, explanation: str, **lists: list[str]) -> tuple[str, str]:
        c, v = self.case, self.verdict()
        if c.committed is not None:
            raise ToolRefusal(f"Already executed: {ACTION_TOOL[c.committed.action]}. A case gets one action.")
        if self.action is not v.action:
            raise ToolRefusal(f"Refused: the enforcement policy decided {v.action.value}, and this tool executes only "
                              f"{self.action.value}. The decision is not yours to change; execute it with "
                              f"{ACTION_TOOL[v.action]}.")
        if c.rejected_drafts >= MAX_DRAFTS:
            raise ToolRefusal(f"Refused: {MAX_DRAFTS} drafts have been rejected on this case. The orchestrator will "
                              "execute the decision with its template explanation.")
        ref = c.claim.hospital_ref
        problems = guardrails.draft_problems(explanation, v.action.value, ref)
        for key, what in (("documents_requested", "document"), ("questions_for_field_team", "question")):
            if key in lists:
                problems += guardrails.item_problems(lists[key], what, ref)
        if problems:
            with c._lock:
                c.rejected_drafts += 1
            raise ToolRefusal(f"Not executed: the text cannot be published: {'; '.join(problems)}. Revise it and call "
                              f"{self.name} again.",
                              summary="draft rejected: " + "; ".join(dict.fromkeys(p.split(" (")[0] for p in problems)))
        scrub = lambda t: guardrails.scrub_identifiers(guardrails.inline(t), ref)  # noqa: E731
        c.committed = Commitment(
            action=v.action, explanation=guardrails.scrub_identifiers(str(explanation).strip(), ref),
            field_questions=tuple(scrub(q) for q in lists.get("questions_for_field_team", [])),
            documents_requested=tuple(scrub(d) for d in lists.get("documents_requested", [])))
        return (f"Executed: {guardrails.ACTION_IN_WORDS[v.action.value]} Your explanation is the one issued.",
                f"executed {v.action.value}")


GATED = " Executes only when it is the enforcement policy's decision; refused otherwise."


class ReleaseClaim(ActionTool):
    action: ClassVar[Action] = Action.RELEASE_CLAIM
    name: str = ACTION_TOOL[Action.RELEASE_CLAIM]
    description: str = "Release the withheld claim for payment." + GATED


class IssueShowCauseNotice(ActionTool):
    action: ClassVar[Action] = Action.SHOW_CAUSE
    name: str = ACTION_TOOL[Action.SHOW_CAUSE]
    description: str = ("Issue a show-cause notice: the hospital has five days to answer with the documents requested."
                        + GATED)
    args_schema: type[BaseModel] = ShowCauseArguments


class OrderFieldAudit(ActionTool):
    action: ClassVar[Action] = Action.FIELD_AUDIT
    name: str = ACTION_TOOL[Action.FIELD_AUDIT]
    description: str = ("Order a field audit on the channels the decision names, with the questions the field team "
                        "must answer." + GATED)
    args_schema: type[BaseModel] = FieldAuditArguments


class SuspendHospital(ActionTool):
    action: ClassVar[Action] = Action.SUSPEND
    name: str = ACTION_TOOL[Action.SUSPEND]
    description: str = "Suspend the hospital and refer it to the State Empanelment Committee." + GATED


class EscalateToStateCommittee(ActionTool):
    action: ClassVar[Action] = Action.ESCALATE_SEC
    name: str = ACTION_TOOL[Action.ESCALATE_SEC]
    description: str = "Escalate the case to the State Empanelment Committee without suspending the hospital." + GATED


class ReferSpecialtyForDelisting(ActionTool):
    action: ClassVar[Action] = Action.DELIST_SPECIALTY
    name: str = ACTION_TOOL[Action.DELIST_SPECIALTY]
    description: str = ("Refer the billed specialty for de-listing and flag the district as uncovered, for the "
                        "Committee to decide." + GATED)


class ReferForHumanReview(ActionTool):
    action: ClassVar[Action] = Action.NO_ACTION
    name: str = ACTION_TOOL[Action.NO_ACTION]
    description: str = "Take no enforcement action and refer the case to a human reviewer." + GATED


ACTION_TOOLS: tuple[type[ActionTool], ...] = (ReleaseClaim, IssueShowCauseNotice, OrderFieldAudit, SuspendHospital,
                                              EscalateToStateCommittee, ReferSpecialtyForDelisting, ReferForHumanReview)


class OptionArguments(BaseModel):
    option: str = Field(description="Something the Committee could decide, e.g. suspend with a transition window")
    consequence: str = Field(description="What that option would mean for the evidence, the hospital and access in "
                                         "the district")


class CommitteeBriefArguments(BaseModel):
    summary: str = Field(description="The evidence and the access consequence side by side, 40-250 words")
    options: list[OptionArguments] = Field(description="The options open to the Committee, 2-4 of them")
    recommendation: str = Field(description="The option you recommend and why, 15-150 words. The Committee decides")
    question: str = Field(description="The one question the Committee must answer")


REFERRED = frozenset({Action.ESCALATE_SEC, Action.DELIST_SPECIALTY})


class FileCommitteeBrief(OfficerTool):
    name: str = "file_committee_brief"
    description: str = ("File the brief the State Empanelment Committee receives with an escalation or a de-listing "
                        "referral: the evidence and the access consequence, the options open to the Committee, your "
                        "recommendation and the question it must answer. Refused unless the decision refers the case to "
                        "the Committee, and refused, with the reasons, if any part overstates the decision or the "
                        "access loss.")
    args_schema: type[BaseModel] = CommitteeBriefArguments

    def trail_arguments(self, kwargs: dict) -> dict:
        """Sizes, not text: the brief is published only once it passes the checks, in its own section."""
        options = kwargs.get("options")
        return {"summary": f"{len(str(kwargs.get('summary', '')).split())} words",
                "options": f"{len(options)} option(s)" if isinstance(options, (list, tuple)) else "not a list",
                "recommendation": f"{len(str(kwargs.get('recommendation', '')).split())} words"}

    def answer(self, summary: str, options: list[dict], recommendation: str, question: str) -> tuple[str, str]:
        c, v = self.case, self.verdict()
        if v.action not in REFERRED:
            raise ToolRefusal(f"Refused: the decision is {v.action.value}, which refers nothing to the Committee.")
        if c.brief is not None:
            raise ToolRefusal("Already filed: a case gets one brief.")
        if c.rejected_briefs >= MAX_DRAFTS:
            raise ToolRefusal(f"Refused: {MAX_DRAFTS} briefs have been rejected on this case. The Committee receives the "
                              "standard question instead.")
        opts = [CommitteeOption(**o) if isinstance(o, dict) else CommitteeOption(**o.model_dump()) for o in options]
        ref = c.claim.hospital_ref
        problems = guardrails.brief_problems(summary, opts, recommendation, question, v.action.value, ref)
        if problems:
            with c._lock:
                c.rejected_briefs += 1
            raise ToolRefusal(f"Not filed: the brief cannot be published: {'; '.join(problems)}. Revise it and call "
                              f"{self.name} again.",
                              summary="brief rejected: " + "; ".join(dict.fromkeys(p.split(" (")[0] for p in problems)))

        def clean(text: str) -> str:
            return guardrails.scrub_identifiers(str(text).strip(), ref)

        c.brief = CommitteeBrief(summary=clean(summary), recommendation=clean(recommendation),
                                 question=clean(guardrails.inline(question)),
                                 options=[CommitteeOption(option=clean(guardrails.inline(o.option)),
                                                          consequence=clean(guardrails.inline(o.consequence)))
                                          for o in opts])
        return ("Filed: the Committee receives your brief with the case. It recommends; the Committee decides.",
                f"filed a brief with {len(opts)} options")


# ── each agent's kit ─────────────────────────────────────────────────────────
# A kit is the tools one agent's question needs, and no more. No kit before the decision holds a decision tool, so no
# agent that reads the evidence can learn what a suspension would do to access.

def _kit(case: CaseFile, agent: str, tools: tuple) -> list[BaseTool]:
    return [t(case=case, acting_agent=agent, cache_function=never_cache) for t in tools]


EVIDENCE_TOOLS = (BeneficiaryClaimHistory, DocumentsSharedWithOtherClaims, SurgeonSameDayClaims, ReadDocument,
                  CompareDocuments)


def investigator_tools(case: CaseFile) -> list[BaseTool]:
    """The desk audit fetches and compares whatever the documents' integrity turns on."""
    return _kit(case, INVESTIGATOR, EVIDENCE_TOOLS)


def router_tools(case: CaseFile) -> list[BaseTool]:
    """The router decides which questions are live, and must not answer any of them: it gets no evidence tool.

    Giving it read_document would let it form a view of the case before the readers do, and a router that has already
    decided is a reader with a different name (F-54: the triage agent whose plan was never used)."""
    return []


def billing_tools(case: CaseFile) -> list[BaseTool]:
    """The money question: the tariff arithmetic, and the documents that would account for an excess."""
    return _kit(case, BILLING, (ClaimTariff, ReadDocument, BeneficiaryClaimHistory))


def advocate_tools(case: CaseFile) -> list[BaseTool]:
    """The defence reads what is on file; it establishes nothing new, so it gets no cross-claim listing tools."""
    return _kit(case, ADVOCATE, (ReadDocument, ClaimTariff))


def medical_tools(case: CaseFile) -> list[BaseTool]:
    """The clinical question needs the record, and on repeat admissions (T7) the member's other admissions."""
    return _kit(case, MEDICAL, (BeneficiaryClaimHistory, ReadDocument))


def field_tools(case: CaseFile) -> list[BaseTool]:
    """Field reports are weighed against the documents on file."""
    return _kit(case, FIELD, (ReadDocument,))


def reviewer_tools(case: CaseFile) -> list[BaseTool]:
    """Checking a finding means checking what it cites, and whether evidence it ignores is on file."""
    return _kit(case, REVIEWER, EVIDENCE_TOOLS)


def liaison_tools(case: CaseFile) -> list[BaseTool]:
    return _kit(case, LIAISON, (EnforcementDecision, AccessImpact, FileCommitteeBrief))


def officer_tools(case: CaseFile) -> list[BaseTool]:
    return _kit(case, OFFICER, (EnforcementDecision, AccessImpact, *ACTION_TOOLS))
