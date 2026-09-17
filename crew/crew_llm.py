"""The CrewAI crew -- LLM mode (docs/01-HLD.md §4, docs/02-LLD.md §6a).

One crew, six agents, six tasks, run in order (crew/config/agents.yaml, tasks.yaml). Each agent owns one question no
rule can answer, and holds the tools that question needs (crew/agent_tools.py):

1. Desk Investigator: are the documents present, consistent, genuine, and not copied from other claims? Its tools
   fetch the beneficiary's other admissions, documents filed on other claims and the surgeon's other claims that day,
   and set documents side by side.
2. Medical Auditor, on the triggers that turn on a clinical question (T2, T3, T4, T7): was the care billed clinically
   needed?
3. Field Evidence Analyst, when field reports are on file: how much weight does each deserve?
4. Audit Reviewer: does each reading hold against the evidence it cites? It may dispute the desk or the medical
   reading, and a disputed reading weighs nothing; what the case then does depends on the readings left standing.
   A dispute of a reading that repeats a comparison the store can make itself is refused (B-44a). It can neither
   convict nor clear.
   When it reports, the enforcement policy decides (task callback): the evidence rules turn the reports into findings
   (crew/guardrails.py) and rules/policy.py decides the action. No agent decides an action.
5. Committee Liaison, when the decision refers the case to the State Empanelment Committee: the brief the Committee
   receives, with the options open to it and a recommendation. The Committee decides.
6. Enforcement Officer: executes the decision through the one action tool that accepts it, and explains it.

Guardrails that are structural, not instructions:
* no agent that reads the evidence can learn what a suspension would do to access: no evidence tool reveals it;
* the three readers work independently (their tasks take no context), and the reviewer receives their reports as the
  hand-off;
* a stance on a trigger about other claims needs those claims examined (CrewAI task guardrails);
* each reading meets the rules desk's evidentiary preconditions, field reports keep their recorded stance, registry
  facts are a lookup, a disputed reading weighs nothing, and a dispute may not set aside a measurement;
* the officer's actions are gated to the policy's decision, and its text and the liaison's brief to the publication
  checks.
"""
from __future__ import annotations

import json
import re
import warnings

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.tasks.conditional_task import ConditionalTask
from crewai.tasks.task_output import TaskOutput
from pydantic import BaseModel

from crew import guardrails
from crew.agent_tools import (ADVOCATE, BILLING, FIELD, INVESTIGATOR, LIAISON, MEDICAL, OFFICER, REFERRED,
                              REVIEWER, ROUTER, CaseFile, advocate_tools, billing_tools, field_tools,
                              investigator_tools, liaison_tools, medical_tools, officer_tools, reviewer_tools,
                              router_tools)
from crew.schemas import AGENT_TITLES, Channel
from rules import policy
from rules.triggers import guidance

# Tool rounds before CrewAI asks an agent for its final answer.
STEPS = {
    ROUTER: 2,            # it calls nothing: one turn to plan, one in hand
    BILLING: 8,           # the tariff, then the invoices and bills that would account for an excess
    ADVOCATE: 6,          # the tariff and the documents the defence rests on
    INVESTIGATOR: 12,     # a T7 audit lists the admissions and reads or compares about four documents
    MEDICAL: 10,          # on T7, the admissions and their clinical records
    FIELD: 6,
    REVIEWER: 10,
    LIAISON: 8,           # decision, access, a brief rejected twice, filed
    OFFICER: 8,           # decision, access, a draft rejected twice, executed
}
GUARDRAIL_RETRIES = 1     # an agent that ignores a guardrail twice stops the crew, and the case degrades to the rules
CLINICAL = frozenset({"T2", "T3", "T4", "T7"})
DISPUTE_WORDS = 8         # a reason in at least one full sentence
TRACKS = ("medical", "field", "billing", "advocate")   # the optional work the Case Router may open (B-48)
TRACK_AGENT = {"medical": MEDICAL, "field": FIELD, "billing": BILLING, "advocate": ADVOCATE}


def mandatory_tracks(case: CaseFile) -> set[str]:
    """The tracks a rule opens whatever the router thinks. The router may add to this set and may never take from it:
    a model deciding what NOT to investigate is a model deciding the case (B-48)."""
    tracks = set()
    if case.hit.trigger_id in CLINICAL:
        tracks.add("medical")
    if case.claim.field_reports:
        tracks.add("field")
    if case.hit.trigger_id == "R2" or (case.package.amount_rs > 0
                                       and case.claim.amount_claimed > case.package.amount_rs):
        tracks.add("billing")
    return tracks


# ── what the agents return ───────────────────────────────────────────────────

class InvestigationReport(BaseModel):
    """The Desk Investigator's finding."""
    supports_fraud: bool | None
    confidence: float
    conclusion: str
    citation: str


class MedicalReport(InvestigationReport):
    """The Medical Auditor's finding: the same shape, on the clinical question."""


class FieldAssessment(BaseModel):
    channel: Channel
    supports_fraud: bool | None
    confidence: float
    conclusion: str


class FieldReview(BaseModel):
    """The Field Evidence Analyst's weighing of the field reports on file."""
    assessments: list[FieldAssessment]


class BillingReport(InvestigationReport):
    """The Billing & Tariff Analyst's finding: the same shape, on the money question."""


class AdvocacyReport(BaseModel):
    """The Provider Advocate's answer: the best innocent explanation the file supports, and whether it survives."""
    explanation: str
    excluded: bool        # does the evidence ON FILE exclude it? True means the defence fails on the documents
    citation: str


class OpenTrack(BaseModel):
    track: str            # medical | field | billing | advocate
    reason: str


class RoutePlan(BaseModel):
    """The Case Router's plan: the optional work it wants opened, beyond whatever the rules already mandate."""
    open: list[OpenTrack]
    summary: str


class Dispute(BaseModel):
    finding: str          # desk_audit | medical_audit | billing_audit | advocacy
    reason: str


class AuditReview(BaseModel):
    """The Audit Reviewer's review: the findings it disputes, and why."""
    disputes: list[Dispute]
    summary: str


# How a reviewer may name a finding it disputes.
FINDING_NAMES = {"desk_audit": "desk_audit", "desk": "desk_audit", "desk_investigator": "desk_audit",
                 "medical_audit": "medical_audit", "medical": "medical_audit", "medical_auditor": "medical_audit",
                 "billing_audit": "billing_audit", "billing": "billing_audit", "billing_analyst": "billing_audit",
                 "advocacy": "advocacy", "defence": "advocacy", "defense": "advocacy",
                 "provider_advocate": "advocacy"}


def finding_name(text: str) -> str | None:
    return FINDING_NAMES.get(re.sub(r"[\s-]+", "_", str(text).strip().lower()))


# "the desk audit is disputed", "I dispute the medical audit" -- but not "nothing is disputed", "I do not dispute".
_DISPUTE_CLAIM = re.compile(r"\b(?:is|are|was|were)\s+disputed\b|\bI\s+dispute\b|\bdisputes?\s+the\b", re.I)
_NOT_DISPUTED = re.compile(r"\b(?:no|none|nothing|neither|not|never|n't|without)\b[^.]{0,40}?\bdisput", re.I)


def _asserts_a_dispute(summary: str) -> bool:
    """Whether a review summary claims a dispute. Negations ("no finding is disputed") are not claims."""
    return bool(_DISPUTE_CLAIM.search(summary)) and not _NOT_DISPUTED.search(summary)


# ── the case files ───────────────────────────────────────────────────────────
# One per reader, each the only placeholder in its task. The desk and the medical auditor read the documents; the
# field reports are the Field Evidence Analyst's to weigh, so a report never counts twice, once as itself and once
# inside a reading. The reviewer sees everything the readers saw.

DOC_CHARS = 6_000       # per document in a case file; read_document returns more, and the rules desk reads it all

# Triggers about other claims: for each reader, the tool that lists them and what it lists.
CROSS_CLAIM = {
    INVESTIGATOR: {"T5": ("surgeon_same_day_claims", "the surgeon's other claims that day"),
                   "T6": ("documents_shared_with_other_claims", "the claims the document was also filed on"),
                   "T7": ("beneficiary_claim_history", "the beneficiary's other admissions")},
    MEDICAL: {"T7": ("beneficiary_claim_history", "the beneficiary's other admissions")},
}
# A reading whose question is answered by a tool may not be given without it: the billing analyst's excess is
# arithmetic, and arithmetic nobody did is not evidence. (The cross-claim rule below is the same idea, per trigger.)
REQUIRED_TOOL = {BILLING: ("claim_tariff",
                           "compute the rate, the amount claimed and the excess with claim_tariff, and read the "
                           "documents that could account for it")}
CITATION_WORDS = 1        # a stance must name something; one token is a document type, which is a citation

# How each reader examines what the listing tool lists.
EXAMINING = {
    INVESTIGATOR: (frozenset({"read_document", "compare_documents"}),
                   "read or compare their documents with read_document or compare_documents"),
    MEDICAL: (frozenset({"read_document"}), "read their clinical records with read_document"),
}


def _clip(text: str) -> str:
    """A document as a case file quotes it. A runaway upload must not blow the model's budget and degrade the case."""
    return text if len(text) <= DOC_CHARS else text[:DOC_CHARS] + f" [... {len(text) - DOC_CHARS:,} more characters]"


def rate(package) -> str:
    """Per-day packages have no fixed rate in the master; 'Rs 0' would read as a claim above a zero rate."""
    return (f"Published rate Rs {package.amount_rs:,}" if package.amount_rs > 0
            else "Published rate: none fixed in the package master (a per-day package)")


def _stance(supports: bool | None) -> str:
    return {True: "supports the suspicion", False: "opposes the suspicion", None: "neither supports nor opposes it"}[
        supports]


def case_file(case: CaseFile, reader: str) -> str:
    """The case as `reader` receives it in its task."""
    claim, hit, package = case.claim, case.hit, case.package
    g = guidance(hit.trigger_id)
    docs = "\n".join(f"- [{claim.claim_id}/{d.doc_type}] sha256={d.sha256[:12]}: {_clip(d.text)}"
                     for d in claim.documents) or "- none"
    reports = "\n".join(f"- [{r.channel.value}] recorded finding: {_stance(r.supports_fraud)}, confidence "
                        f"{r.confidence:.2f}. {r.summary}" for r in claim.field_reports) or "- none on file"
    reserved = ", ".join(s for s, flag in package.reserved_under.items() if flag) or "none"
    desk_checks = "\n".join(f"- {c}" for c in g["desk_checklist"]) or "- (see source)"
    field_checks = "\n".join(f"- {c}" for c in g["field_checklist"][:8]) or "- none"
    discharged = f"{claim.discharge_ts:%Y-%m-%d %H:%M}" if claim.discharge_ts else "not recorded"

    head = f"""CASE FILE (simulated; provider identity pseudonymised)

Claim {claim.claim_id} | hospital {claim.hospital_ref} | beneficiary {claim.beneficiary_ref}
Admitted {claim.admission_ts:%Y-%m-%d %H:%M} | discharged {discharged} | length of stay {claim.los_days} days | ICU flag {claim.icu_flag}
Surgeon registration: {claim.surgeon_reg_no or 'not recorded'}
Registered death of beneficiary: {claim.beneficiary_death_ts.date() if claim.beneficiary_death_ts else 'none'}

PACKAGE {package.package_code} - {guardrails.inline(package.package_name)}
{rate(package)} | claimed Rs {claim.amount_claimed:,} | day-care package: {'yes' if package.daycare_candidate else 'no'}
Listed under: {', '.join(package.specialties) or 'no registry specialty'} | government-reserved under: {reserved}
Private hospitals may bill it on a referral from a government facility: {'yes' if package.referral_allowed else 'no'}
Hospital type: {case.hospital.hospital_type or 'not recorded'}
Required pre-procedure evidence: {package.pre_investigations}
Required post-procedure evidence: {package.post_investigations}

TRIGGER {hit.trigger_id} - {hit.name} (severity {hit.severity.value})
What fired: {hit.evidence}
Source: {hit.source}
"""
    settle = f"""FIELD CHECKS (guidebook, hospital-visit and beneficiary columns) - what a field team checks ONLY if the desk cannot
settle the question. They are not prerequisites: if the documents settle it, decide.
{field_checks}
Field channels available if the desk cannot settle it: {', '.join(g['field_channels']) or 'none'}
"""
    documents = f"\nDOCUMENTS ON THIS CLAIM\n{docs}\n"
    on_file = f"\nFIELD REPORTS ON FILE\n{reports}\n"

    if reader == ROUTER:
        # The router plans; it must not read the case, or it arrives at the readers' question before they do.
        present = ", ".join(sorted({d.doc_type for d in claim.documents})) or "none"
        excess = (claim.amount_claimed - package.amount_rs) if package.amount_rs > 0 else None
        opened = mandatory_tracks(case)
        return head + f"""WHAT IS ALREADY OPEN, BY RULE (you cannot close any of these): {', '.join(sorted(opened)) or 'none'}

THE TRACKS YOU MAY OPEN
- medical  - a Medical Auditor asks whether the care was clinically needed. Worth opening when the trigger turns on
  the clinical story rather than on the paperwork.
- field    - a Field Evidence Analyst weighs the field reports on file. There is nothing to weigh unless reports are
  on file, and you may not open it when there are none.
- billing  - a Billing & Tariff Analyst asks whether the money adds up against the published rate and the invoices.
- advocate - a Provider Advocate puts the hospital's side before it is acted against. It opens by itself whenever a
  reader supports the suspicion; open it here when you think the file will be read too harshly without it.

WHAT IS ON FILE (types only: you are not reading the case)
Documents: {present}
Field reports on file: {len(claim.field_reports)}
Money: {rate(package).lower()}, claimed Rs {claim.amount_claimed:,}""" + (
            f", {'over' if excess and excess > 0 else 'within'} the published rate"
            f"{f' by Rs {excess:,}' if excess and excess > 0 else ''}." if excess is not None else ".")

    if reader == BILLING:
        checks = f"DESK AUDIT CHECKS (guidebook, desk-audit column):\n{desk_checks}\n"
        money = f"""THE MONEY QUESTION
{rate(package)} | claimed Rs {claim.amount_claimed:,}
Your claim_tariff tool computes the excess and lists the documents that could account for it; read each with
read_document and judge what it actually covers. An invoice on file is not by itself an explanation: an amount that
accounts for a fraction of the excess leaves the rest unexplained.
"""
        return head + money + checks + documents

    if reader == ADVOCATE:
        return head + """YOUR TASK IS THE HOSPITAL'S SIDE
The readers' findings reach you as the context below. Put the best innocent explanation this file will actually
bear, and say whether the evidence ON FILE excludes it. You are not weighing whether fraud is more likely: you are
establishing whether an honest explanation has been ruled out.

An explanation the evidence does not exclude stops an action no human has reviewed; it never releases a claim. So
it must be an explanation the documents support, cited. Where the file does exclude the innocent reading, say so:
a defence that cannot be made is worth more to the hospital than one that will not survive review.
""" + documents

    if reader == FIELD:
        return (head + "FIELD CHECKS (guidebook, hospital-visit and beneficiary columns) - what the field team was sent "
                       f"to establish:\n{field_checks}\n" + documents + on_file)

    cross = CROSS_CLAIM.get(reader, {}).get(hit.trigger_id)
    if reader == REVIEWER:
        checks = f"DESK AUDIT CHECKS (guidebook, desk-audit column) - what the desk had to settle:\n{desk_checks}\n"
        hint = ("Your tools reach what the findings cite: read_document and compare_documents on this claim or a related "
                "one; beneficiary_claim_history, documents_shared_with_other_claims and surgeon_same_day_claims list "
                "the related claims.")
        return head + checks + settle + hint + "\n" + documents + on_file
    if reader == MEDICAL:
        checks = f"CLINICAL CHECKS (guidebook, desk-audit column) - what the record must show:\n{desk_checks}\n"
        hint = (f"This trigger is about other claims: list {cross[1]} with {cross[0]}, then read their clinical "
                "records with read_document, before taking a position." if cross else
                "The documents below are this claim's whole file.")
    else:
        checks = f"DESK AUDIT CHECKS (guidebook, desk-audit column) - what the desk must settle:\n{desk_checks}\n"
        hint = (f"This trigger is about other claims: list {cross[1]} with {cross[0]}, then read_document and "
                "compare_documents on what it lists, before taking a position." if cross else
                "The documents below are this claim's whole file; your tools reach other claims, should the evidence "
                "point to them.")
    return head + checks + settle + hint + "\n" + documents


# ── the crew ─────────────────────────────────────────────────────────────────

def _quietly(factory, **kwargs):
    """Build a CrewAI object whose callbacks are bound to this case. CrewAI warns that such a callback cannot be
    checkpointed; nothing here is checkpointed: a case runs start to finish in one process() call."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*prevent checkpointing.*", category=UserWarning)
        return factory(**kwargs)


@CrewBase
class AccessGateCrew:
    """The nine agents on one case."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, case: CaseFile, llm):
        self.case, self.llm = case, llm

    def _agent(self, key: str, kit) -> Agent:
        return Agent(config=self.agents_config[key], llm=self.llm, tools=kit(self.case), allow_delegation=False,
                     max_iter=STEPS[key], verbose=False)

    @agent
    def case_router(self) -> Agent:
        return self._agent(ROUTER, router_tools)

    @agent
    def desk_investigator(self) -> Agent:
        return self._agent(INVESTIGATOR, investigator_tools)

    @agent
    def billing_analyst(self) -> Agent:
        return self._agent(BILLING, billing_tools)

    @agent
    def provider_advocate(self) -> Agent:
        return self._agent(ADVOCATE, advocate_tools)

    @agent
    def medical_auditor(self) -> Agent:
        return self._agent(MEDICAL, medical_tools)

    @agent
    def field_evidence_analyst(self) -> Agent:
        return self._agent(FIELD, field_tools)

    @agent
    def audit_reviewer(self) -> Agent:
        return self._agent(REVIEWER, reviewer_tools)

    @agent
    def committee_liaison(self) -> Agent:
        return self._agent(LIAISON, liaison_tools)

    @agent
    def enforcement_officer(self) -> Agent:
        return self._agent(OFFICER, officer_tools)

    @task
    def route(self) -> Task:
        return _quietly(Task, config=self.tasks_config["route"], output_pydantic=RoutePlan,
                        guardrail=self.route_holds, guardrail_max_retries=GUARDRAIL_RETRIES, callback=self.routed)

    @task
    def investigate(self) -> Task:
        return _quietly(Task, config=self.tasks_config["investigate"], output_pydantic=InvestigationReport,
                        guardrail=self.desk_reading, guardrail_max_retries=GUARDRAIL_RETRIES,
                        callback=self.desk_reported)

    @task
    def medical_audit(self) -> Task:
        return _quietly(ConditionalTask, config=self.tasks_config["medical_audit"], condition=self.clinical_question,
                        output_pydantic=MedicalReport, guardrail=self.medical_reading,
                        guardrail_max_retries=GUARDRAIL_RETRIES, callback=self.medical_reported)

    @task
    def field_review(self) -> Task:
        return _quietly(ConditionalTask, config=self.tasks_config["field_review"], condition=self.field_reports_on_file,
                        output_pydantic=FieldReview, guardrail=self.field_reading,
                        guardrail_max_retries=GUARDRAIL_RETRIES, callback=self.field_reported)

    @task
    def billing_audit(self) -> Task:
        return _quietly(ConditionalTask, config=self.tasks_config["billing_audit"], condition=self.money_question,
                        output_pydantic=BillingReport, guardrail=self.billing_reading,
                        guardrail_max_retries=GUARDRAIL_RETRIES, callback=self.billing_reported)

    @task
    def advocacy(self) -> Task:
        return _quietly(ConditionalTask, config=self.tasks_config["advocacy"], condition=self.defence_called_for,
                        output_pydantic=AdvocacyReport, guardrail=self.advocacy_holds,
                        guardrail_max_retries=GUARDRAIL_RETRIES, callback=self.advocacy_reported)

    @task
    def review(self) -> Task:
        return _quietly(Task, config=self.tasks_config["review"], output_pydantic=AuditReview,
                        guardrail=self.review_holds, guardrail_max_retries=GUARDRAIL_RETRIES, callback=self.decide)

    @task
    def committee_brief(self) -> Task:
        return _quietly(ConditionalTask, config=self.tasks_config["committee_brief"], condition=self.referred)

    @task
    def enforce(self) -> Task:
        return Task(config=self.tasks_config["enforce"])

    @crew
    def crew(self) -> Crew:
        return _quietly(Crew, agents=self.agents, tasks=self.tasks, process=Process.sequential,
                        task_callback=self.completed, verbose=False)

    # ── which tasks run ─────────────────────────────────────────────────────
    # ConditionalTask passes the previous task's output; these conditions turn on the case, not on that output.

    def _open(self, track: str) -> bool:
        """A track runs when a rule mandates it, or when the router opened it. Never the router alone deciding not to."""
        return track in mandatory_tracks(self.case) or track in self._router_opened()

    def _router_opened(self) -> set[str]:
        plan = self.case.plan
        return {t.track for t in plan.open} if plan is not None else set()

    def clinical_question(self, _previous: TaskOutput) -> bool:
        return self._open("medical")

    def money_question(self, _previous: TaskOutput) -> bool:
        return self._open("billing")

    def defence_called_for(self, _previous: TaskOutput) -> bool:
        """The advocate is never mandated by the trigger: it is mandated by what the readers found. No hospital is
        acted against on a reading that supports fraud without its own side put first (B-49)."""
        readings = [r for r in (self.case.report, self.case.medical, self.case.billing) if r is not None]
        return any(r.supports_fraud is True for r in readings) or "advocate" in self._router_opened()

    def field_reports_on_file(self, _previous: TaskOutput) -> bool:
        return self._open("field") and bool(self.case.claim.field_reports)

    def referred(self, _previous: TaskOutput) -> bool:
        return self.case.verdict is not None and self.case.verdict.action in REFERRED

    # ── task guardrails ─────────────────────────────────────────────────────
    # Each returns (passed, JSON or feedback). Without a pass the agent goes back to work with the feedback. On a pass
    # the JSON becomes the task's output (after a guardrail CrewAI converts only what the guardrail returns), labelled
    # with the finding it is, because the reviewer receives the readers' outputs side by side. CrewAI rejects a
    # postponed return annotation on a guardrail, so none has one.

    def route_holds(self, output: TaskOutput):
        """A plan names tracks that exist, says why, and does not open work there is no evidence for."""
        try:
            plan = _parse(output, RoutePlan)
        except ValueError as exc:
            return False, f"Your final answer must be the JSON object alone, with open and summary ({exc})."
        problems, keep = [], {}
        for t in plan.open:
            name = str(t.track).strip().lower()
            if name not in TRACKS:
                problems.append(f"'{t.track}' is not a track: choose from {', '.join(TRACKS)}")
            elif name == "field" and not self.case.claim.field_reports:
                problems.append("no field report is on file, so there is nothing for the field track to weigh")
            elif len(t.reason.split()) < DISPUTE_WORDS:
                problems.append(f"your reason for opening {name} needs at least one full sentence")
            else:
                keep.setdefault(name, guardrails.inline(t.reason))
        if problems:
            return False, f"Revise your plan: {'; '.join(problems)}."
        clean = RoutePlan(open=[OpenTrack(track=k, reason=v) for k, v in keep.items()],
                          summary=guardrails.inline(plan.summary))
        return True, clean.model_dump_json()

    def routed(self, output: TaskOutput) -> None:
        c = self.case
        c.plan = _parse(output, RoutePlan)
        mandated = mandatory_tracks(c)
        c.opened = sorted({t.track for t in c.plan.open} - mandated)
        c.plan_reasons = [f"{t.track}: {t.reason}" for t in c.plan.open if t.track in c.opened]

    def billing_reported(self, output: TaskOutput) -> None:
        self.case.billing = _parse(output, BillingReport)

    def advocacy_reported(self, output: TaskOutput) -> None:
        self.case.defence = _parse(output, AdvocacyReport)

    def billing_reading(self, output: TaskOutput):
        return self._reading(output, BillingReport, BILLING, "billing_audit")

    def advocacy_holds(self, output: TaskOutput):
        """A defence that would stop an action has to rest on something: an explanation in a full sentence, and a
        document on file it reads that way. 'It might be innocent' is not a defence."""
        try:
            report = _parse(output, AdvocacyReport)
        except ValueError as exc:
            return False, ("Your final answer must be the JSON object alone, with explanation, excluded and citation "
                           f"({exc}).")
        if len(report.explanation.split()) < DISPUTE_WORDS:
            return False, "State the innocent explanation in at least one full sentence, or say the file offers none."
        if not report.excluded and len(report.citation.split()) < 3:
            return False, ("An explanation the evidence does not exclude must cite what on this claim's file supports "
                           "it. Cite the document, or set excluded=true.")
        if not report.excluded and not self.case.called(ADVOCATE):
            # This is the answer that stops an action no human has reviewed. It may not rest on the summary alone.
            return False, ("An explanation the evidence does not exclude stops an action being taken, so it must rest "
                           "on the file itself. Read the document you are relying on with read_document (or check the "
                           "figures with claim_tariff) before answering excluded=false.")
        clean = AdvocacyReport(explanation=guardrails.inline(report.explanation), excluded=report.excluded,
                               citation=guardrails.inline(report.citation))
        return True, _handoff("advocacy", ADVOCATE, clean)

    def desk_reading(self, output: TaskOutput):
        return self._reading(output, InvestigationReport, INVESTIGATOR, "desk_audit")

    def medical_reading(self, output: TaskOutput):
        return self._reading(output, MedicalReport, MEDICAL, "medical_audit")

    def _reading(self, output: TaskOutput, model: type[BaseModel], reader: str, finding: str):
        """A stance on a trigger about other claims needs those claims examined, as the guidebook's desk check does;
        an honest null passes."""
        try:
            report = _parse(output, model)
        except ValueError as exc:
            return False, f"Your final answer must be the JSON object alone, with the fields asked for ({exc})."
        if report.supports_fraud is not None and len(str(report.citation).split()) < CITATION_WORDS:
            return False, ("A stance has to say what it rests on. Put the claim and document you read in citation, "
                           "or answer supports_fraud=null.")
        required = REQUIRED_TOOL.get(reader)
        if required is not None and report.supports_fraud is not None and required[0] not in self.case.called(reader):
            return False, (f"You took a position without using {required[0]}, so the figures you give are not ones "
                           f"this case file establishes. First {required[1]}, before concluding, or answer "
                           "supports_fraud=null if the documents cannot settle it.")
        cross = CROSS_CLAIM.get(reader, {}).get(self.case.hit.trigger_id)
        if cross is not None and report.supports_fraud is not None:
            called = self.case.called(reader)
            examining, how = EXAMINING[reader]
            missing = []
            if cross[0] not in called:
                missing.append(f"list {cross[1]} with {cross[0]}")
            if self.case.listed.get(reader) and not called & examining:
                missing.append(how)
            if missing:
                return False, (f"Trigger {self.case.hit.trigger_id} concerns other claims, and you took a position "
                               f"without examining them. First {'; then '.join(missing)}, before concluding, or answer "
                               "supports_fraud=null if they cannot settle it.")
        return True, _handoff(finding, reader, report)

    def field_reading(self, output: TaskOutput):
        try:
            review = _parse(output, FieldReview)
        except ValueError as exc:
            channels = ", ".join(r.channel.value for r in self.case.claim.field_reports)
            return False, ("Your final answer must be the JSON object alone, with a list named assessments, each naming "
                           f"its channel exactly as the report does ({channels}) ({exc}).")
        return True, _handoff("field_reports", FIELD, review)

    def review_holds(self, output: TaskOutput):
        """A dispute names a reading on this case and says why. The review goes on as the reviewer wrote it, with each
        finding named canonically and disputed once."""
        try:
            review = _parse(output, AuditReview)
        except ValueError as exc:
            return False, f"Your final answer must be the JSON object alone, with disputes and summary ({exc})."
        on_case = (["desk_audit"] + (["medical_audit"] if self.case.medical is not None else [])
                   + (["billing_audit"] if self.case.billing is not None else [])
                   + (["advocacy"] if self.case.defence is not None else []))
        problems, reasons = [], {}
        for d in review.disputes:
            name = finding_name(d.finding)
            if name not in on_case:
                problems.append(f"'{d.finding}' is not a finding you can dispute on this case: name "
                                f"{' or '.join(on_case)}")
            elif len(d.reason.split()) < DISPUTE_WORDS:
                problems.append(f"your dispute of {name} needs its reason in at least one full sentence")
            else:
                reasons.setdefault(name, guardrails.inline(d.reason))
        if problems:
            return False, f"Revise your review: {'; '.join(problems)}."
        summary = guardrails.inline(review.summary)
        if not reasons and _asserts_a_dispute(summary):
            # Seen live on S8: the summary read "the desk audit is disputed because ...", and disputes was empty. The
            # artefact then says "No finding disputed" beside a summary saying the opposite, and nothing was set aside.
            return False, ("Your summary says you disputed a finding, but your disputes list is empty. Either list the "
                           "dispute, naming the finding and the reason, or say in the summary that every finding "
                           "holds.")
        clean = AuditReview(disputes=[Dispute(finding=k, reason=v) for k, v in reasons.items()], summary=summary)
        return True, clean.model_dump_json()

    # ── task callbacks: the work joins the case file ────────────────────────

    def desk_reported(self, output: TaskOutput) -> None:
        self.case.report = _parse(output, InvestigationReport)

    def medical_reported(self, output: TaskOutput) -> None:
        self.case.medical = _parse(output, MedicalReport)

    def field_reported(self, output: TaskOutput) -> None:
        self.case.field_review = _parse(output, FieldReview)

    def decide(self, output: TaskOutput) -> None:
        """On the review, before anyone acts: the evidence rules and the disputes, then the enforcement policy."""
        c = self.case
        c.review = _parse(output, AuditReview)
        c.findings = guardrails.guarded_findings(c, c.report, c.medical, c.field_review, c.review, c.billing)
        disputed = {finding_name(d.finding) for d in c.review.disputes}
        # The defence reaches the policy only when it stands: unexcluded on the evidence, and not disputed away.
        defence = (c.defence.explanation
                   if c.defence is not None and not c.defence.excluded and "advocacy" not in disputed else None)
        c.verdict = policy.decide([], c.hit, c.findings, c.adequacy,
                                  frozenset(r.channel for r in c.claim.field_reports), network=c.network,
                                  defence=defence)

    def completed(self, output: TaskOutput) -> None:
        """Crew task callback, on every task that ran to completion: a skipped task never calls back."""
        self.case.agents.append(TASK_AGENT[output.name])


TASK_AGENT = {"route": ROUTER, "investigate": INVESTIGATOR, "medical_audit": MEDICAL, "field_review": FIELD,
              "billing_audit": BILLING, "advocacy": ADVOCATE, "review": REVIEWER, "committee_brief": LIAISON,
              "enforce": OFFICER}


def _handoff(finding: str, reader: str, report: BaseModel) -> str:
    return json.dumps({"finding": finding, "agent": AGENT_TITLES[reader], **report.model_dump(mode="json")},
                      ensure_ascii=False)


def _parse(output: TaskOutput, model: type[BaseModel]):
    """An agent's answer as `model`: CrewAI's structured output, else the JSON object in the answer (a model may wrap it
    in a code fence or a sentence). Raises ValueError when there is none."""
    if isinstance(output.pydantic, model):
        return output.pydantic
    raw = str(output.raw or "")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    return model.model_validate_json(raw[start:end + 1])


def run_case(case: CaseFile, llm) -> None:
    """Run the crew on one case. What it establishes lands on `case`: the readings, the review, the findings and the
    verdict once the reviewer has reported, the brief and the committed action once filed and executed. Raises whatever
    stopped it."""
    AccessGateCrew(case, llm).crew().kickoff(inputs={
        "desk_file": case_file(case, INVESTIGATOR), "medical_file": case_file(case, MEDICAL),
        "field_file": case_file(case, FIELD), "review_file": case_file(case, REVIEWER),
        "route_file": case_file(case, ROUTER), "billing_file": case_file(case, BILLING),
        "advocate_file": case_file(case, ADVOCATE),
        "case_id": f"CASE-{case.claim.claim_id}", "hospital_ref": case.claim.hospital_ref})
