"""Typed contracts at every boundary (docs/02-LLD.md §2).

Invalid agent output is caught here, not propagated. Two rules are enforced by
the types themselves rather than by convention:

* EC-1  provider identity is pseudonymous -- a claim naming anything other than
        HOSP-nnnnn cannot be constructed;
* EC-3  capability is a flag, never a conclusion -- Adequacy carries
        `capability_ok`, and nothing in the system asserts incapability.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PSEUDONYM = re.compile(r"^HOSP-\d{5}$")
# A claim id names files on disk (out/artefacts/<action>_CASE-<claim_id>.md). "..\..\x" wrote outside the output
# directory; ids are therefore plain tokens (F-37).
CLAIM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DOC_TYPE = re.compile(r"^[a-z0-9_]{1,40}$")
# PM-JAY runs on Indian time. Timestamps with an offset are converted to it and stored naive, so no comparison can
# mix aware and naive values (which raised TypeError) and "the date" of a death or an admission is the Indian date.
IST = timezone(timedelta(hours=5, minutes=30))


def _ist(v: datetime | None) -> datetime | None:
    return v.astimezone(IST).replace(tzinfo=None) if v is not None and v.tzinfo is not None else v


def _token(v: str | None) -> str | None:
    """Identifiers compared across claims: surrounding space, repeated space and case must not split one person or
    one surgeon into two, and a blank must not join everyone with a blank into one."""
    if v is None:
        return None
    v = " ".join(str(v).split()).upper()
    return v or None


class Channel(str, Enum):
    """NHA's four evidence channels (Anti-Fraud Guidebook, Annexure 2)."""
    DESK_AUDIT = "desk_audit"
    HOSPITAL_VISIT = "hospital_visit"
    BENEFICIARY_CALL = "beneficiary_call"
    BENEFICIARY_VISIT = "beneficiary_visit"


class Severity(int, Enum):
    ADMINISTRATIVE = 1   # documentation gaps
    SUBSTANTIVE = 2      # clinical or billing implausibility
    EGREGIOUS = 3        # service after death, document reuse, impossible surgeon


class Action(str, Enum):
    RELEASE_CLAIM = "release_claim"
    SHOW_CAUSE = "show_cause_notice"
    FIELD_AUDIT = "order_field_audit"
    SUSPEND = "suspend_hospital"
    DELIST_SPECIALTY = "delist_specialty"
    ESCALATE_SEC = "escalate_to_sec"
    NO_ACTION = "no_action_review"
    REFUSE = "refuse_malformed"


HUMAN_ACTIONS = frozenset({Action.ESCALATE_SEC, Action.DELIST_SPECIALTY, Action.NO_ACTION})


class Document(BaseModel):
    # Unknown fields are errors, not silently dropped: a misspelled field is a missing field (F-38).
    model_config = ConfigDict(extra="forbid")

    doc_type: str
    text: str
    sha256: str = ""

    @field_validator("doc_type", mode="before")
    @classmethod
    def _doc_type(cls, v: str) -> str:
        v = re.sub(r"[\s-]+", "_", str(v).strip().lower())
        if not DOC_TYPE.match(v):
            raise ValueError(f"document type {v!r} is not a plain name (letters, digits, underscores)")
        return v

    @model_validator(mode="after")
    def _hash(self) -> "Document":
        # Reuse (T6) is decided on this hash, so it must be the hash of this text. A supplied hash that did not
        # match made two different documents "byte-identical" (F-36).
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.sha256 and self.sha256.lower() != digest:
            raise ValueError("sha256 does not match the document text")
        self.sha256 = digest
        return self


class FieldReport(BaseModel):
    """Evidence returned by a SAFU field team when a case re-enters the pipeline."""
    model_config = ConfigDict(extra="forbid")

    channel: Channel
    summary: str
    supports_fraud: bool | None
    confidence: float = Field(ge=0.0, le=1.0)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    hospital_ref: str
    district_code: int | None
    district_name: str | None = None
    package_code: str
    beneficiary_ref: str
    admission_ts: datetime
    discharge_ts: datetime | None
    surgeon_reg_no: str | None = None
    amount_claimed: int = Field(ge=0)
    icu_flag: bool = False
    beneficiary_death_ts: datetime | None = None
    documents: list[Document] = []
    field_reports: list[FieldReport] = []
    submitted_ts: datetime

    @field_validator("hospital_ref")
    @classmethod
    def _pseudonymous(cls, v: str) -> str:
        if not PSEUDONYM.match(v):
            raise ValueError("EC-1: provider identity must be pseudonymous (HOSP-nnnnn)")
        return v

    @field_validator("claim_id")
    @classmethod
    def _claim_id(cls, v: str) -> str:
        if not CLAIM_ID.match(v):
            raise ValueError("claim_id must be 1-64 letters, digits, '.', '_' or '-', starting with a letter or digit")
        return v

    @field_validator("beneficiary_ref", "package_code", mode="before")
    @classmethod
    def _required_token(cls, v: str) -> str:
        token = _token(v)
        if token is None:
            raise ValueError("must not be blank")
        return token

    @field_validator("surgeon_reg_no", mode="before")
    @classmethod
    def _optional_token(cls, v: str | None) -> str | None:
        return _token(v)

    @field_validator("district_name", mode="before")
    @classmethod
    def _district_name(cls, v: str | None) -> str | None:
        return " ".join(str(v).split()) or None if v is not None else None

    @field_validator("admission_ts", "discharge_ts", "beneficiary_death_ts", "submitted_ts")
    @classmethod
    def _indian_time(cls, v: datetime | None) -> datetime | None:
        return _ist(v)

    @property
    def los_days(self) -> int | None:
        if self.discharge_ts is None:
            return None
        return max(0, (self.discharge_ts.date() - self.admission_ts.date()).days)


class Hospital(BaseModel):
    """A row of the pseudonymised registry -- structure only, no identity."""
    hospital_ref: str
    district_code: int
    state: str
    hospital_type: str
    basic_tier: bool
    specialties: list[str]


class Package(BaseModel):
    package_code: str
    package_name: str
    specialty: str | None                    # the package's own specialty: the one its code names (MP001C -> MP)
    amount_rs: int
    pre_investigations: str
    post_investigations: str
    govt_reserved: bool                      # reserved for government hospitals under EVERY listing
    daycare_candidate: bool
    # Every specialty the package is listed under, its own first. 473 packages have several, and a hospital may
    # bill one through any of them (F-35).
    specialties: list[str] = []
    reserved_under: dict[str, bool] = {}     # per listing: 52 packages are reserved under some listings only
    # The package master's referral_basis: a reserved package a private hospital may bill on a government referral.
    # Only 9 are; for the other reserved packages a referral letter explains nothing (F-51).
    referral_allowed: bool = False

    @model_validator(mode="after")
    def _listings(self) -> "Package":
        if self.specialty and self.specialty not in self.specialties:
            self.specialties = [self.specialty, *self.specialties]
        return self

    @property
    def is_major(self) -> bool:
        return self.amount_rs >= 20_000 and not self.daycare_candidate


class TriggerHit(BaseModel):
    trigger_id: str
    name: str
    severity: Severity
    evidence: str
    field_channels: list[Channel]
    checklist: list[str]
    source: str
    # Whether a desk finding alone may carry an egregious case to suspension. Only a death certificate does; a
    # reused document waits for the field, because a clerical upload error looks the same at the desk (B-28).
    desk_can_suspend: bool = False


class AgentFinding(BaseModel):
    agent: str
    channel: Channel | None = None
    conclusion: str
    supports_fraud: bool | None          # None = inconclusive, neither supports nor opposes
    confidence: float = Field(ge=0.0, le=1.0)
    citation: str                        # NFR-2: the document, field or rule relied on


class Adequacy(BaseModel):
    district_code: int
    district: str
    state: str
    specialty: str
    specialty_name: str
    n_providers: int
    km_to_alternative: float | None
    nearest_alternative: str | None
    population: int
    aspirational: bool
    capability_ok: bool
    state_convention: bool
    capability_reason: str
    hospital_listed: bool = True          # the flagged hospital is itself empanelled for this specialty
    registry_blank: bool = False          # the registry lists no specialties at all for it (D-4): role unknown


class CommitteeOption(BaseModel):
    option: str                                # something the Committee could decide
    consequence: str                           # what it would mean for the evidence, the hospital and access


class CommitteeBrief(BaseModel):
    """What the Committee Liaison files for the State Empanelment Committee with an escalation or a de-listing
    referral. It recommends; the Committee decides."""
    summary: str
    options: list[CommitteeOption]
    recommendation: str
    question: str


# The crew's agents, in the order they work a case (crew/config/agents.yaml), as the artefact names them.
AGENT_TITLES = {
    "case_router": "Case Router",
    "desk_investigator": "Desk Investigator",
    "billing_analyst": "Billing & Tariff Analyst",
    "provider_advocate": "Provider Advocate",
    "medical_auditor": "Medical Auditor",
    "field_evidence_analyst": "Field Evidence Analyst",
    "audit_reviewer": "Audit Reviewer",
    "committee_liaison": "Committee Liaison",
    "enforcement_officer": "Enforcement Officer",
}


class ToolCall(BaseModel):
    """One tool an agent called: the investigation trail every artefact carries."""
    agent: str                                 # a key of AGENT_TITLES
    tool: str
    arguments: dict[str, str] = {}             # each value shown in one line, long text shortened
    outcome: str                               # what the tool returned, in one line, or why it refused
    refused: bool = False


class Decision(BaseModel):
    case_id: str
    claim_id: str
    hospital_ref: str
    trigger_id: str | None
    triggers_fired: list[str]
    severity: Severity | None
    channels_used: list[Channel]
    field_channels_ordered: list[Channel] = []
    findings: list[AgentFinding]
    conflicts: list[str]
    adequacy: Adequacy | None                  # the billed specialty
    access_at_stake: list[Adequacy] = []       # every specialty a suspension would strip of its only real provider
    gate: str | None
    action: Action
    claim_withheld: bool
    reason_codes: list[str]
    confidence: float
    degraded: bool
    human_required: bool
    explanation: str
    artefact_path: str | None = None
    decided_ts: datetime
    model: str | None = None             # the LLM the crew ran on; None on a rules-only run
    # Who executed the action: the Enforcement Officer through its action tool, or the orchestrator with the template
    # explanation (rules-only runs, and crew runs where the officer did not act).
    acted_by: str = "orchestrator"
    trail: list[ToolCall] = []           # every tool the agents called, in order
    field_questions: list[str] = []      # the officer's case-specific questions for a field audit
    documents_requested: list[str] = []  # the officer's list of documents a show-cause notice asks for
    agents: list[str] = []               # the agents whose tasks completed on the case, in order; none when degraded
    review: str | None = None            # the Audit Reviewer's summary of its review
    disputed: list[str] = []             # findings the Audit Reviewer disputed, which then weighed nothing
    disputes_refused: list[str] = []     # disputes refused because the reading rests on a measurement (B-44a, F-61)
    opened_by_router: list[str] = []     # optional tracks the Case Router opened that no rule made mandatory (B-48)
    router_reasons: list[str] = []       # why it opened each, as "track: reason"
    defence: str | None = None           # the Provider Advocate's innocent explanation, when one stood unexcluded
    defence_excluded: bool | None = None  # whether the evidence on file excluded it; None when no advocate ran
    committee_brief: CommitteeBrief | None = None   # the Committee Liaison's brief, on escalations and referrals
