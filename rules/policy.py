"""The decision policy (docs/02-LLD.md §3).

This is the file a reviewer will attack first. Every constant carries the reason for
its value; if a number changes, the reason changes with it.

`decide()` is a pure function -- no I/O, no LLM, no clock. Agents PROPOSE
findings; this function DISPOSES. That is why a decision is reproducible and
why the degraded path is a real code path rather than a mock.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Sequence

from crew.schemas import HUMAN_ACTIONS, Action, Adequacy, AgentFinding, Channel, Severity, TriggerHit

# ── thresholds ─────────────────────────────────────────────────────────────

CONFIDENCE_FLOOR = 0.70
# Below this the system takes no enforcement action. The prior is brutal: PIB
# (PRID 1847423) puts confirmed fraud at 0.18% of authorised admissions, so at a
# 1% false-positive rate ~86% of flags are innocent. Low-confidence findings are
# mostly noise -- they buy more evidence (a field audit) or a human, never a notice.

SOLE_PROVIDER = 1
# The count at which enforcement zeroes out a capability in a district.
# Not a judgement: one provider, removed, leaves none.

DISTANCE_MATERIAL_KM = 50.0
# A sole provider whose nearest alternative is closer than this is not an access
# crisis -- the specialty is a bus ride away. Median distance to an alternative
# is 50.7 km, so this splits sole-provider cells roughly in half. It is the
# number to sweep and publish (docs/05-TEST-PLAN.md §4.2), not to assert.

ASPIRATIONAL_SHIFT = 1
# NITI aspirational districts are protected one provider earlier: removing one
# of two providers halves capacity where the sole-provider rate is already
# 27.0% against 17.9% elsewhere. NHA's own empanelment guidelines annexe the
# aspirational list, so the carve-out is existing policy, not ours.

SEVERITY_SUSPEND = Severity.EGREGIOUS
# Only egregious findings -- service after death, document reuse, an impossible
# surgeon -- reach suspension. Anything less gets a show-cause notice: reversible,
# five days to answer, and proportionate to a 0.18% prior.


@dataclass(frozen=True)
class Verdict:
    action: Action
    reason_codes: tuple[str, ...]
    confidence: float
    gate: str | None = None
    field_channels: tuple[Channel, ...] = ()
    conflicts: tuple[str, ...] = field(default_factory=tuple)
    at_stake: tuple[Adequacy, ...] = ()      # specialties a suspension would strip of their only real provider

    @property
    def human_required(self) -> bool:
        return self.action in HUMAN_ACTIONS

    @property
    def claim_withheld(self) -> bool:
        # NAFU withholds the claim when the trigger fires; only a clearance releases it.
        return self.action is not Action.RELEASE_CLAIM


# ── reconciliation ─────────────────────────────────────────────────────────

def weigh(findings: list[AgentFinding]) -> tuple[float, float, tuple[str, ...]]:
    """Confidence mass for and against fraud. Inconclusive findings weigh nothing."""
    support = sum(f.confidence for f in findings if f.supports_fraud is True)
    oppose = sum(f.confidence for f in findings if f.supports_fraud is False)
    conflicts: tuple[str, ...] = ()
    if support and oppose:
        pro = ", ".join(sorted({f.agent + (f"/{f.channel.value}" if f.channel else "")
                                for f in findings if f.supports_fraud is True}))
        con = ", ".join(sorted({f.agent + (f"/{f.channel.value}" if f.channel else "")
                                for f in findings if f.supports_fraud is False}))
        conflicts = (f"fraud supported by [{pro}] and opposed by [{con}]",)
    return support, oppose, conflicts


def aggregate_confidence(findings: list[AgentFinding]) -> tuple[bool, float]:
    """(fraud?, confidence), unrounded.

    Confidence = (winning share of the evidence mass) x (strongest single finding
    on the winning side). Unanimity at low certainty stays low; a split verdict is
    discounted by the split. Rounding is for display only: 0.6996 rounded to 0.7 would clear the floor it is below.
    """
    support, oppose, _ = weigh(findings)
    total = support + oppose
    if total == 0:
        return False, 0.0
    fraud = support > oppose
    side = [f.confidence for f in findings if f.supports_fraud is fraud]
    return fraud, max(support, oppose) / total * max(side)


# ── the access gate ────────────────────────────────────────────────────────

def access_gate(a: Adequacy, distance_km: float | None = None) -> tuple[str, tuple[str, ...]]:
    """'clear' | 'protect' | 'phantom' | 'unknown', and why.

    `n_providers` counts the flagged hospital itself when it is empanelled for
    the specialty, so `n_providers <= 1` means removing it leaves nobody.
    `distance_km` overrides DISTANCE_MATERIAL_KM for the threshold sweep only.
    """
    threshold = DISTANCE_MATERIAL_KM if distance_km is None else distance_km
    facts = (f"PROVIDERS_IN_DISTRICT={a.n_providers}",
             f"KM_TO_ALTERNATIVE={'none-listed' if a.km_to_alternative is None else round(a.km_to_alternative)}",
             f"POPULATION={a.population}", f"ASPIRATIONAL={a.aspirational}")

    if a.registry_blank:
        # The registry lists nothing for this hospital (D-4): what it provides, and so what suspending it
        # would remove, cannot be established. An irreversible action with an unknowable consequence is a
        # human's call -- the same rule as adequacy that cannot be computed (B-6).
        return "unknown", facts + ("REGISTRY_LISTS_NO_SPECIALTIES",)
    if not a.hospital_listed:
        # Not empanelled for what it billed: suspending it removes no empanelled provider of the specialty.
        return "clear", facts + ("HOSPITAL_NOT_EMPANELLED_FOR_SPECIALTY",)

    if a.n_providers <= SOLE_PROVIDER and not a.capability_ok:
        # The access being "protected" may not exist, and its presence in the
        # registry masks the gap. The right action is the opposite of protection.
        return "phantom", facts + ("CAPABILITY_IMPLAUSIBLE", "DISTRICT_UNCOVERED")

    protections = _protections(a, threshold)
    if protections:
        return "protect", facts + protections

    return "clear", facts + ("ALTERNATIVES_EXIST",)


def _protections(a: Adequacy, threshold: float) -> tuple[str, ...]:
    """Why losing this provider would be an access loss -- for a real listing (not phantom)."""
    out = []
    if a.n_providers <= SOLE_PROVIDER and (a.km_to_alternative is None or a.km_to_alternative >= threshold):
        out.append("SOLE_REAL_PROVIDER")
    if a.aspirational and a.n_providers <= SOLE_PROVIDER + ASPIRATIONAL_SHIFT:
        out.append("ASPIRATIONAL_DISTRICT_THIN_NETWORK")
    return tuple(out)


def suspension_gate(billed: Adequacy | None, network: Sequence[Adequacy], distance_km: float | None = None
                    ) -> tuple[str, tuple[str, ...], tuple[Adequacy, ...]]:
    """What suspending the HOSPITAL would do to access: (gate, facts, specialties at stake).

    A suspension takes a hospital out of the scheme for every specialty it is empanelled for, not only the one
    it billed. Gating on the billed specialty alone auto-suspended hospitals that were their district's only
    real provider of something else -- 6.5% of confirmed egregious findings, more than the gate protected (F-22).
    So every specialty in `network` (the hospital's own empanelled specialties) is checked.

    The billed specialty alone decides 'phantom': a de-listing referral removes that one listing, nothing else.
    Its specialties at stake are still returned, because the Committee that receives the referral may suspend.
    """
    if billed is not None and billed.registry_blank:
        gate, facts = access_gate(billed, distance_km)            # 'unknown': nothing listed to check
        return gate, facts, ()
    if billed is None and not network:
        return "unknown", ("ADEQUACY_UNKNOWN",), ()
    threshold = DISTANCE_MATERIAL_KM if distance_km is None else distance_km

    facts: tuple[str, ...] = ()
    cells = {a.specialty: a for a in network}
    phantom = False
    if billed is not None:
        gate, facts = access_gate(billed, distance_km)
        phantom = gate == "phantom"
        if billed.hospital_listed:
            cells[billed.specialty] = billed

    at_stake = []
    for spec in sorted(cells, key=lambda s: (billed is None or s != billed.specialty, s)):    # billed first
        a = cells[spec]
        if a.n_providers <= SOLE_PROVIDER and not a.capability_ok:
            continue                  # an implausible listing elsewhere is access that may not exist
        why = _protections(a, threshold)
        if why:
            at_stake.append(a)
            if billed is None or spec != billed.specialty:
                facts += tuple(f"{w}={spec}" for w in why)
    if phantom:
        return "phantom", facts + tuple(f"SPECIALTY_AT_STAKE={a.specialty}" for a in at_stake), tuple(at_stake)
    if at_stake:
        return "protect", facts + tuple(f"SPECIALTY_AT_STAKE={a.specialty}" for a in at_stake), tuple(at_stake)
    return "clear", facts + ("NO_SPECIALTY_LOSES_ITS_ONLY_PROVIDER",), ()


# ── the defence ────────────────────────────────────────────────────────────

DEFENDABLE = frozenset({Action.SHOW_CAUSE, Action.SUSPEND})   # what the desk does to a hospital with no human first


def defended(v: Verdict, defence: str | None, missing: tuple[Channel, ...]) -> Verdict:
    """B-49. An innocent explanation the evidence on file does not exclude stops an action no human has reviewed.

    It can only slow a case down. A release is never reached this way, so an unexcluded defence cannot clear a fraud;
    an escalation or a de-listing referral already puts the decision to the State Empanelment Committee, so those
    stand. The Audit Reviewer may dispute the defence, and a disputed defence never reaches here.
    """
    # An empty or blank defence is not a defence. The advocacy guardrail will not pass one, but `decide(defence=...)`
    # is callable from anywhere, and a blank string must never be what stops an action against a hospital.
    if not str(defence or "").strip() or v.action not in DEFENDABLE:
        return v
    if missing:
        return replace(v, action=Action.FIELD_AUDIT, field_channels=missing,
                       reason_codes=v.reason_codes + ("DEFENCE_UNEXCLUDED", "ORDER_FIELD_EVIDENCE"))
    return replace(v, action=Action.NO_ACTION, reason_codes=v.reason_codes + ("DEFENCE_UNEXCLUDED", "HUMAN_REVIEW"))


# ── the decision ───────────────────────────────────────────────────────────

def decide(problems: list[str], hit: TriggerHit | None, findings: list[AgentFinding],
           adequacy: Adequacy | None, field_on_file: frozenset[Channel] = frozenset(), *,
           network: Sequence[Adequacy], defence: str | None = None) -> Verdict:
    """Pure. Every branch is unit-tested in tests/test_policy.py.

    `adequacy` is the billed specialty's; `network` is the adequacy of every specialty the hospital is
    empanelled for -- what a suspension would actually remove. It has no default on purpose: a caller that
    forgets it would silently gate on the billed specialty alone (F-22).
    `field_on_file` is the set of channels whose field reports are on the claim.
    `defence` is the Provider Advocate's innocent explanation where the evidence on file did not exclude it and the
    Audit Reviewer did not dispute it; it can only slow a case down (`defended`, B-49). The rules path never sets it.
    """
    if problems:
        return Verdict(Action.REFUSE, tuple(f"MALFORMED_{p}" for p in problems), 1.0)
    if hit is None:
        return Verdict(Action.RELEASE_CLAIM, ("NO_TRIGGER",), 1.0)

    _, _, conflicts = weigh(findings)
    fraud, exact = aggregate_confidence(findings)
    below = exact < CONFIDENCE_FLOOR - 1e-9                  # decided on the exact value, float noise aside
    # Recorded rounded DOWN to two places, so a confidence just below the floor can never be printed as meeting it
    # (0.6996 shows as 0.69, not 0.70 beside "below the 0.7 floor").
    conf = math.floor(exact * 100 + 1e-9) / 100
    missing = tuple(c for c in hit.field_channels if c not in field_on_file)

    if exact == 0.0 or below:
        why = ("DESK_INCONCLUSIVE",) if exact == 0.0 else ("BELOW_CONFIDENCE_FLOOR",)
        if missing:
            # Buy evidence rather than guess: order the channels this trigger needs that are not yet on file.
            return Verdict(Action.FIELD_AUDIT, why + ("ORDER_FIELD_EVIDENCE",), conf,
                           field_channels=missing, conflicts=conflicts)
        return Verdict(Action.NO_ACTION, why + ("HUMAN_REVIEW",), conf, conflicts=conflicts)

    if not fraud:
        return Verdict(Action.RELEASE_CLAIM, ("TRIGGER_CLEARED",), conf, conflicts=conflicts)

    if hit.severity < SEVERITY_SUSPEND:
        return defended(Verdict(Action.SHOW_CAUSE, ("CONFIRMED", f"SEVERITY_{hit.severity.name}"), conf,
                                conflicts=conflicts), defence, missing)

    # Only a trigger whose desk evidence is proof in itself (a death certificate: T10) may skip the field. A reused
    # document looks the same at the desk whether it is fraud or a clerical upload error (B-28).
    desk_proves = hit.desk_can_suspend and any(
        f.agent == "desk_audit" and f.supports_fraud is True and f.confidence >= CONFIDENCE_FLOOR for f in findings)
    if missing and not desk_proves:
        # E-2: an egregious finding needs every field channel the trigger orders. One report back and one
        # outstanding is not enough to suspend, however confident that one report is.
        why = "FIELD_EVIDENCE_INCOMPLETE" if len(missing) < len(hit.field_channels) else "FIELD_VERIFICATION_REQUIRED"
        return Verdict(Action.FIELD_AUDIT, (why, "ORDER_FIELD_EVIDENCE"), conf,
                       field_channels=missing, conflicts=conflicts)

    # The access gate, over everything a suspension would remove. An irreversible action whose access
    # consequence is unknowable ('unknown') is a human's call, like one that would remove a sole provider.
    gate, facts, at_stake = suspension_gate(adequacy, network)
    if gate == "phantom":
        return Verdict(Action.DELIST_SPECIALTY, ("CONFIRMED_EGREGIOUS",) + facts, conf, gate=gate,
                       conflicts=conflicts, at_stake=at_stake)
    if gate in ("protect", "unknown"):
        return Verdict(Action.ESCALATE_SEC, ("CONFIRMED_EGREGIOUS",) + facts, conf, gate=gate,
                       conflicts=conflicts, at_stake=at_stake)
    return defended(Verdict(Action.SUSPEND, ("CONFIRMED_EGREGIOUS",) + facts, conf, gate=gate,
                            conflicts=conflicts), defence, missing)
