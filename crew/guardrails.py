"""Deterministic checks on everything an agent produces (docs/02-LLD.md §6).

Agents propose; these decide what may count and what may be published:

* evidence rules: an agent's desk finding meets the same preconditions as the rules desk; a field team's recorded
  finding may be discounted by an agent but never flipped, inflated or dropped; registry facts are a lookup;
* publication rules: drafted text may not overstate what the action does or what access is lost, and names no
  hospital but the case's own pseudonym.

Nothing here calls a model, so every rule is unit-tested offline.
"""
from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Sequence

from crew import actions
from crew import investigate as rules
from crew.schemas import Adequacy, AgentFinding, Channel

if TYPE_CHECKING:
    from crew.agent_tools import CaseFile


def clamp(x: float) -> float:
    """A model's confidence into [0, 1]. A non-finite number is no confidence: min/max would have turned NaN, and
    infinity, into 1.0."""
    x = float(x)
    return 0.0 if not math.isfinite(x) else max(0.0, min(1.0, x))


OTHER_HOSPITAL = re.compile(r"\bHOSP-\d{5}\b|\bHOSP\d\w*", re.I)


def scrub_identifiers(text: str, hospital_ref: str) -> str:
    """Replace every hospital identifier but this case's own pseudonym. The tools never show another hospital's
    identifier, so any other one an agent writes is invented -- and an NHA-style id could be a real one (EC-1)."""
    return OTHER_HOSPITAL.sub(lambda m: m.group(0) if m.group(0).upper() == hospital_ref else "[another hospital]", text)


def inline(text: str) -> str:
    return " ".join(str(text).split())


# ── evidence rules ───────────────────────────────────────────────────────────

DISPUTABLE = ("desk_audit", "medical_audit", "billing_audit")   # agent readings; field evidence and lookups are not


def measured_reuse(case: "CaseFile") -> tuple[bool, str]:
    """Whether the store itself finds a document on this claim byte-identical to one on another claim, and where.

    This is a comparison the orchestrator can repeat, not a reading: `ClaimStore.identical` hashes the documents.
    `reused()` is the narrower T6 question (a different beneficiary's document); a beneficiary's own admissions
    sharing byte-identical paperwork is the T7 question, and is what the desk's comparison tools measure.
    """
    same = case.store.identical(case.claim)
    if not same:
        return False, ""
    doc, other = same[0]
    return True, f"the {doc.doc_type} is byte-identical to one filed on {other.claim_id}"


def _reading(agent: str, report, case: "CaseFile") -> AgentFinding:
    """An agent's reading of the documents as a finding, under the rules desk's evidentiary preconditions."""
    finding = AgentFinding(agent=agent, channel=Channel.DESK_AUDIT, supports_fraud=report.supports_fraud,
                           confidence=0.0 if report.supports_fraud is None else clamp(report.confidence),
                           conclusion=report.conclusion, citation=report.citation)
    return rules.desk_preconditions(finding, case.claim, case.hit, case.package)


def guarded_findings(case: "CaseFile", report, medical=None, field_review=None, review=None,
                     billing=None) -> list[AgentFinding]:
    """The agents' reports as the findings the policy weighs.

    `report` is the Desk Investigator's report, `medical` the Medical Auditor's (on T2, T3, T4 and T7), `field_review`
    the Field Evidence Analyst's (when field reports are on file) and `review` the Audit Reviewer's (crew_llm models).
    Each reading meets the rules desk's evidentiary preconditions; the field reports on file stay the field team's
    recorded findings; the registry finding is the lookup's. A reading the reviewer disputes weighs nothing.

    A dispute is **refused** where the reading it would set aside rests on a measurement rather than an inference
    (B-44a, F-61): when the store itself finds a document byte-identical to another claim's, a reading that supports
    fraud is repeating a comparison, and a dispute of it would let the reviewer erase a fact. Refusing is recorded on
    the case, so the artefact shows the reviewer tried. The reviewer may still dispute the other reading.
    """
    claim, hit, package = case.claim, case.hit, case.package
    findings = [_reading("desk_audit", report, case)]
    if medical is not None:
        findings.append(_reading("medical_audit", medical, case))
    if billing is not None:
        findings.append(_reading("billing_audit", billing, case))

    # A field team's recorded finding is evidence, not an opinion to overrule. The analyst may DISCOUNT a report it
    # finds weak, but may not flip its stance, inflate its confidence, or drop it; an assessment of a report that is
    # not on file is ignored (F-09).
    assessed = {fa.channel: fa for fa in (field_review.assessments if field_review is not None else [])}
    for r in claim.field_reports:
        fa = assessed.get(r.channel)
        if fa is not None and fa.supports_fraud == r.supports_fraud:
            findings.append(AgentFinding(agent="field_review", channel=r.channel, supports_fraud=r.supports_fraud,
                                         confidence=0.0 if r.supports_fraud is None else
                                         min(clamp(fa.confidence), r.confidence),
                                         conclusion=fa.conclusion, citation=f"field report: {r.channel.value}"))
        else:
            why = "omitted it" if fa is None else "contradicted it"
            findings.append(AgentFinding(agent="field_review", channel=r.channel, supports_fraud=r.supports_fraud,
                                         confidence=r.confidence,
                                         conclusion=f"{r.summary} (field team's recorded finding kept; the agent's "
                                                    f"reading {why})",
                                         citation=f"field report: {r.channel.value}"))

    # The registry can only say "not empanelled" (supports R1) or "consistent" (neutral): a lookup, not a reading. An
    # agent once read "consistent" as opposes-fraud at 1.00 and outweighed confirmed document reuse (F-09, F-26).
    findings.append(rules.registry_verification(claim, hit, case.hospital, package))

    disputes = {d.finding: d.reason for d in (review.disputes if review is not None else []) if d.finding in DISPUTABLE}
    case.disputed, case.disputes_refused = [], []
    measured, where = measured_reuse(case)
    for i, f in enumerate(findings):
        if f.agent in disputes and f.supports_fraud is not None:
            if measured and f.supports_fraud is True:
                # The reading repeats a comparison the store can make itself: a dispute cannot set aside a fact.
                case.disputes_refused.append(f.agent)
                findings[i] = f.model_copy(update={
                    "conclusion": f"{f.conclusion} The Audit Reviewer disputed this reading and the dispute was "
                                  f"refused: {where}, which is a comparison on file, not a reading of it.",
                    "citation": f"{f.citation}; audit review (dispute refused)"})
                continue
            case.disputed.append(f.agent)
            findings[i] = f.model_copy(update={
                "supports_fraud": None, "confidence": 0.0,
                "conclusion": f"{f.conclusion} Disputed by the Audit Reviewer: {inline(disputes[f.agent])}",
                "citation": f"{f.citation}; audit review"})
    return [f.model_copy(update={"conclusion": scrub_identifiers(f.conclusion, claim.hospital_ref),
                                 "citation": scrub_identifiers(f.citation, claim.hospital_ref)}) for f in findings]


# ── publication rules ────────────────────────────────────────────────────────

def access_facts(a: Adequacy | None, gate: str | None = None, at_stake: Sequence[Adequacy] = ()) -> str:
    """The access consequence in words, naming the specialties. Reason codes alone led a live model to write
    that a district would lose its 'only healthcare facility' when it would lose one specialty (F-08).

    `a` is the billed specialty; `at_stake` every specialty a suspension would strip of its only real provider.
    A suspension removes the hospital from ALL its specialties, so 'only the billed one is affected' would be
    false for a suspension -- the facts say which specialties lose their only provider, and that no others do.
    """
    ref = a or (at_stake[0] if at_stake else None)
    if ref is None:
        return "Network adequacy could not be computed for this case."
    lines = [f"District: {ref.district}, {ref.state}; population {ref.population:,}; NITI aspirational: "
             f"{'yes' if ref.aspirational else 'no'}"]
    if gate == "protect":
        lines.append("Specialties at stake if the hospital were suspended - it is "
                     + "; ".join(actions.stake_phrase(x) for x in at_stake)
                     + ". ONLY these specialties are at stake: the district's other hospitals are unaffected, and "
                       "every other specialty keeps a provider in the district or within reach")
    elif gate == "clear":
        lines.append("Specialties at stake if the hospital were suspended: none - every specialty it is empanelled for "
                     "keeps a provider in the district or within reach")
    elif gate == "phantom" and a is not None:
        lines.append(f"Specialty referred for de-listing: {a.specialty_name} ONLY - the referral affects nothing else "
                     "about the hospital or the district")
        if at_stake:
            lines.append("For the Committee, should it consider suspension instead: suspension would take the hospital "
                         "out of every specialty, and it is " + "; ".join(actions.stake_phrase(x) for x in at_stake))
    if a is not None:
        # One count, stated once, with what it includes: given "101 providers" and "100 other providers" side by
        # side, a live model wrote "101 other providers" (F-32).
        others = a.n_providers - (1 if a.hospital_listed else 0)
        count = (f"{others} other empanelled provider{'s' if others != 1 else ''} of it in the district"
                 if a.hospital_listed else f"{a.n_providers} empanelled provider{'s' if a.n_providers != 1 else ''} "
                                           f"of it in the district, none of them this hospital")
        where = "" if others > 0 else f"; nearest other provider: {actions.alternative(a)}"
        lines += [f"Billed specialty: {a.specialty_name}; this hospital is {'' if a.hospital_listed else 'NOT '}"
                  f"empanelled for it; {count}{where}",
                  f"Capability flag for the billed specialty: {'plausible' if a.capability_ok else 'IMPLAUSIBLE'} - "
                  f"{a.capability_reason}"]
    return "\n".join(lines)


# What each action does and does not do, so drafted prose cannot overstate it. A live model wrote that a
# hospital "has been delisted" when the decision was a referral for the Committee to decide (F-08).
ACTION_IN_WORDS = {
    "release_claim": "The withheld claim is released.",
    "show_cause_notice": "A show-cause notice is issued; the hospital has five days to respond. Nothing else is decided.",
    "order_field_audit": "A field audit is ordered on the channels named. Nothing is decided until it reports.",
    "suspend_hospital": "The hospital is suspended and referred to the State Empanelment Committee.",
    "escalate_to_sec": "The case is escalated to the State Empanelment Committee. The hospital is NOT suspended; "
                       "the Committee decides.",
    "delist_specialty": "The specialty is REFERRED for de-listing and the district flagged as uncovered. Nothing has "
                        "been de-listed; the Committee decides after confirming the site findings.",
    "no_action_review": "No enforcement action is taken; the case goes to a human reviewer.",
}

# Deterministic check on drafted prose: instructions reduce overstatement, this makes it impossible to publish.
OVERSTATED_ACCESS = re.compile(
    r"\bonly (?:hospital|health ?care (?:facility|provider)|health facility|medical facility)\b"
    r"|\bwithout (?:any )?(?:health ?care|medical care|hospital care|inpatient care|access to care)\b"
    # losing ALL care is an overstatement; "all healthcare claims" is not (live false positive on S7)
    r"|\b(?:lose|loses|losing|loss of|lost|deprived of|cut off from) (?:all|any) "
    r"(?:health ?care|medical care|inpatient care|hospital care|access)\b", re.I)
# "will be suspended" asserts the outcome as surely as "has been"; de-empanelment is NHA's word for de-listing.
SAYS_SUSPENDED = re.compile(r"\b(?:has been|have been|is|was|stands|hereby|will be|shall be|is being) (?:hereby )?"
                            r"suspended\b", re.I)
SAYS_DELISTED = re.compile(r"\b(?:has been|have been|is|was|hereby|will be|shall be|is being) (?:hereby )?"
                           r"(?:de-?listed|de-?empanell?ed|dis-?empanell?ed)\b", re.I)
NEGATED = re.compile(r"\b(?:not|no|nothing|never|none|neither|nor)\b|n't\b", re.I)


def _asserted(pattern: re.Pattern, text: str) -> re.Match | None:
    """The first match the text states outright. A negation earlier in the same sentence withdraws it:
    'nothing has been de-listed' and 'will not lose all healthcare ... or its only hospital' claim neither.
    A line break ends a sentence too: in '- Not suspended' over '- HOSP-26104 has been de-listed', the second
    bullet asserts what the first does not negate (F-28)."""
    for m in pattern.finditer(text):
        before = text[max(0, m.start() - 160):m.start()]
        clause = before[max(before.rfind(ch) for ch in ".;:!?\n") + 1:]
        if not NEGATED.search(clause):
            return m
    return None


SAYS_RELEASED = re.compile(r"\b(?:claim|payment)s?\b[^.;:!?\n]{0,40}?\b(?:has been|have been|is|was|will be|shall be|"
                           r"is being)\s+(?:released|paid|approved|settled|cleared)\b", re.I)
DRAFT_WORDS = (25, 400)      # a sentence or two at least; well past the 180 asked for at most


def draft_problems(text: str, action: str, hospital_ref: str, words: tuple[int, int] = DRAFT_WORDS,
                   what: str = "an explanation") -> list[str]:
    """Why a drafted text may not be published; empty when it may."""
    text = str(text)
    problems = []
    count = len(text.split())
    if not words[0] <= count <= words[1]:
        # An empty draft shipped as an empty explanation (F-42); a runaway one is not the requested summary.
        problems.append(f"is {count} words long; {what} needs {words[0]}-{words[1]}")
    if re.search(r"^\s{0,3}#", text, re.M):
        problems.append("contains a Markdown heading, which would read as a section of the artefact")
    if action != "release_claim" and _asserted(SAYS_RELEASED, text):
        # Every other action keeps the claim withheld.
        problems.append(f"says the claim is released or paid, but the action is {action}")
    if m := _asserted(OVERSTATED_ACCESS, text):
        context = " ".join(text[max(0, m.start() - 50):m.end() + 50].split())
        problems.append(f"overstates the access loss ('{m.group(0)}' in \"...{context}...\")")
    if action != "suspend_hospital" and _asserted(SAYS_SUSPENDED, text):
        problems.append(f"says the hospital is suspended, but the action is {action}")
    if _asserted(SAYS_DELISTED, text):
        problems.append(f"says a specialty has been de-listed, but the action is {action}")
    problems += identifier_problems(text, hospital_ref)
    return problems


def identifier_problems(text: str, hospital_ref: str) -> list[str]:
    others = sorted(set(re.findall(r"HOSP-\d{5}", text)) - {hospital_ref})
    if others or re.search(r"HOSP\d", text):
        return [f"names another hospital identifier ({', '.join(others) or 'NHA-style id'})"]
    return []


LIST_ITEMS = (1, 8)
ITEM_CHARS = (5, 300)


def item_problems(items: Sequence[str], what: str, hospital_ref: str) -> list[str]:
    """Why a list the officer wrote (field questions, documents requested) may not be published."""
    items = [inline(i) for i in items]
    problems = []
    if not LIST_ITEMS[0] <= len(items) <= LIST_ITEMS[1]:
        problems.append(f"lists {len(items)} {what}; give {LIST_ITEMS[0]}-{LIST_ITEMS[1]}")
    for i, item in enumerate(items, start=1):
        if not ITEM_CHARS[0] <= len(item) <= ITEM_CHARS[1]:
            problems.append(f"{what} {i} is {len(item)} characters; each needs {ITEM_CHARS[0]}-{ITEM_CHARS[1]}")
        problems += [f"{what} {i} {p}" for p in identifier_problems(item, hospital_ref)]
    return problems


BRIEF_OPTIONS = (2, 4)


def brief_problems(summary: str, options: Sequence, recommendation: str, question: str, action: str,
                   hospital_ref: str) -> list[str]:
    """Why a Committee brief may not be filed. Every part meets the publication rules: an option may say what
    suspending would do, never that the hospital is suspended, and access is stated as the facts state it."""
    problems = [f"the summary {p}" for p in draft_problems(summary, action, hospital_ref, (40, 250), "a summary")]
    problems += [f"the recommendation {p}"
                 for p in draft_problems(recommendation, action, hospital_ref, (15, 150), "a recommendation")]
    problems += [f"the question {p}" for p in draft_problems(question, action, hospital_ref, (4, 80), "a question")]
    if not BRIEF_OPTIONS[0] <= len(options) <= BRIEF_OPTIONS[1]:
        problems.append(f"lists {len(options)} options; give {BRIEF_OPTIONS[0]}-{BRIEF_OPTIONS[1]}")
    for i, o in enumerate(options, start=1):
        problems += [f"option {i} {p}" for p in draft_problems(o.option, action, hospital_ref, (2, 60), "an option")]
        problems += [f"option {i}'s consequence {p}"
                     for p in draft_problems(o.consequence, action, hospital_ref, (6, 120), "a consequence")]
    return problems
