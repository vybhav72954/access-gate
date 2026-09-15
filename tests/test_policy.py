"""decide() is pure: every branch is testable with no LLM, no I/O, no data."""
from datetime import datetime

import pytest
from pydantic import ValidationError

from crew.schemas import Action, Adequacy, AgentFinding, Channel, Claim, Severity, TriggerHit
from rules import policy
from rules.policy import access_gate, aggregate_confidence, suspension_gate


def hit(severity=Severity.EGREGIOUS, channels=(Channel.HOSPITAL_VISIT,), desk_can_suspend=True):
    """A trigger whose desk evidence can be proof (like T10 with a certificate), unless told otherwise."""
    return TriggerHit(trigger_id="TX", name="test", severity=severity, evidence="e",
                      field_channels=list(channels), checklist=[], source="s", desk_can_suspend=desk_can_suspend)


def finding(supports, conf, agent="desk_audit"):
    return AgentFinding(agent=agent, supports_fraud=supports, confidence=conf, conclusion="c", citation="x")


def adequacy(n=20, km=None, aspirational=False, capability_ok=True, hospital_listed=True, registry_blank=False,
             specialty="cardiology"):
    return Adequacy(district_code=1, district="D", state="S", specialty=specialty, specialty_name=specialty.title(),
                    n_providers=n, km_to_alternative=km, nearest_alternative="N" if km else None,
                    population=1_000_000, aspirational=aspirational, capability_ok=capability_ok,
                    state_convention=False, capability_reason="r", hospital_listed=hospital_listed,
                    registry_blank=registry_blank)


def decide(problems, h, findings, a, field_on_file=frozenset(), network=None):
    """policy.decide for a hospital empanelled for the billed specialty alone, unless a network is given."""
    if network is None:
        network = [a] if a is not None and a.hospital_listed and not a.registry_blank else []
    return policy.decide(problems, h, findings, a, field_on_file, network=network)


FRAUD = [finding(True, 0.95)]
ON_FILE = frozenset({Channel.HOSPITAL_VISIT})     # the default hit's field channel has reported


# ── the access gate ────────────────────────────────────────────────────────

def test_well_served_district_is_suspended_automatically():
    v = decide([], hit(), FRAUD, adequacy(n=101))
    assert v.action is Action.SUSPEND and v.gate == "clear" and not v.human_required


def test_sole_provider_far_from_alternative_is_escalated():
    v = decide([], hit(), FRAUD, adequacy(n=1, km=83))
    assert v.action is Action.ESCALATE_SEC and v.human_required
    assert "SOLE_REAL_PROVIDER" in v.reason_codes


def test_sole_provider_with_alternative_nearby_is_suspended():
    v = decide([], hit(), FRAUD, adequacy(n=1, km=policy.DISTANCE_MATERIAL_KM - 1))
    assert v.action is Action.SUSPEND


def test_aspirational_district_is_protected_one_provider_earlier():
    assert decide([], hit(), FRAUD, adequacy(n=2, aspirational=True)).action is Action.ESCALATE_SEC
    assert decide([], hit(), FRAUD, adequacy(n=2, aspirational=False)).action is Action.SUSPEND


def test_every_applicable_protection_is_recorded():
    v = decide([], hit(), FRAUD, adequacy(n=1, km=83, aspirational=True))
    assert {"SOLE_REAL_PROVIDER", "ASPIRATIONAL_DISTRICT_THIN_NETWORK"} <= set(v.reason_codes)


def test_phantom_capability_inverts_the_gate():
    v = decide([], hit(), FRAUD, adequacy(n=1, km=120, capability_ok=False))
    assert v.action is Action.DELIST_SPECIALTY and v.gate == "phantom"


def test_unknown_adequacy_goes_to_a_human():
    v = decide([], hit(), FRAUD, None)
    assert v.action is Action.ESCALATE_SEC and v.gate == "unknown"


def test_being_pivotal_alone_does_not_escalate():
    """DD-1: a thin district never escalates a case that is not confirmed and egregious."""
    assert decide([], hit(Severity.SUBSTANTIVE), FRAUD, adequacy(n=1, km=200)).action is Action.SHOW_CAUSE
    assert decide([], hit(), [finding(False, 0.9)], adequacy(n=1, km=200)).action is Action.RELEASE_CLAIM


# ── evidence and confidence ────────────────────────────────────────────────

def test_inconclusive_desk_orders_the_triggers_field_channels():
    v = decide([], hit(channels=(Channel.BENEFICIARY_CALL,)), [finding(None, 0.0)], adequacy())
    assert v.action is Action.FIELD_AUDIT and v.field_channels == (Channel.BENEFICIARY_CALL,)


def test_below_floor_with_field_evidence_already_on_file_goes_to_a_human():
    v = decide([], hit(), [finding(True, 0.6)], adequacy(), ON_FILE)
    assert v.action is Action.NO_ACTION and v.human_required


def test_below_floor_with_no_field_channel_goes_to_a_human():
    v = decide([], hit(channels=()), [finding(True, 0.6)], adequacy())
    assert v.action is Action.NO_ACTION


def test_non_egregious_confirmed_finding_gets_a_notice():
    v = decide([], hit(Severity.SUBSTANTIVE), [finding(True, 0.85)], adequacy())
    assert v.action is Action.SHOW_CAUSE and v.claim_withheld


def test_cleared_trigger_releases_the_claim():
    v = decide([], hit(), [finding(False, 0.85)], adequacy())
    assert v.action is Action.RELEASE_CLAIM and not v.claim_withheld


def test_malformed_input_is_refused_not_guessed():
    v = decide(["MISSING_DISCHARGE"], None, [], None)
    assert v.action is Action.REFUSE and v.reason_codes == ("MALFORMED_MISSING_DISCHARGE",)


def test_unanimity_at_low_certainty_stays_low():
    assert aggregate_confidence([finding(True, 0.3), finding(True, 0.3, "b")]) == (True, 0.3)


def test_a_split_verdict_is_discounted_and_recorded_as_a_conflict():
    findings = [finding(True, 0.60), finding(False, 0.55, "field")]
    fraud, conf = aggregate_confidence(findings)
    assert fraud and conf == pytest.approx(0.60 / 1.15 * 0.60, abs=1e-3)
    assert decide([], hit(), findings, adequacy(), ON_FILE).conflicts


def test_inconclusive_findings_weigh_nothing():
    assert aggregate_confidence([finding(True, 0.9), finding(None, 0.0, "registry")]) == (True, 0.9)


def test_a_hospital_the_registry_lists_nothing_for_escalates_as_unknown():
    """D-4: a blank specialty list says nothing about what suspension would remove."""
    v = decide([], hit(), FRAUD, adequacy(n=0, registry_blank=True, hospital_listed=False))
    assert v.action is Action.ESCALATE_SEC and v.gate == "unknown" and "REGISTRY_LISTS_NO_SPECIALTIES" in v.reason_codes


def test_a_hospital_not_empanelled_for_the_specialty_is_not_protected():
    v = decide([], hit(), FRAUD, adequacy(n=1, km=120, hospital_listed=False))
    assert v.action is Action.SUSPEND and "HOSPITAL_NOT_EMPANELLED_FOR_SPECIALTY" in v.reason_codes


def test_one_field_report_cannot_suspend_while_another_channel_is_outstanding():
    """E-2 enforced by the policy, not just by trigger configuration."""
    two = hit(channels=(Channel.BENEFICIARY_CALL, Channel.BENEFICIARY_VISIT))
    findings = [finding(None, 0.0), finding(True, 0.95, agent="field_review")]
    one_back = decide([], two, findings, adequacy(n=101), frozenset({Channel.BENEFICIARY_VISIT}))
    assert one_back.action is Action.FIELD_AUDIT and one_back.field_channels == (Channel.BENEFICIARY_CALL,)
    assert "FIELD_EVIDENCE_INCOMPLETE" in one_back.reason_codes
    both = findings + [finding(True, 0.90, agent="field_review")]
    assert decide([], two, both, adequacy(n=101), frozenset(two.field_channels)).action is Action.SUSPEND


def test_desk_proof_does_not_wait_for_field_channels():
    v = decide([], hit(channels=(Channel.BENEFICIARY_CALL, Channel.BENEFICIARY_VISIT)), FRAUD, adequacy(n=101))
    assert v.action is Action.SUSPEND


def test_a_desk_finding_that_is_not_proof_in_itself_waits_for_the_field():
    """B-28, the team's decision: a reused document (T6) suspends only after both field channels confirm it."""
    two = (Channel.HOSPITAL_VISIT, Channel.BENEFICIARY_CALL)
    t6 = hit(channels=two, desk_can_suspend=False)
    desk = [finding(True, 0.90)]
    first = decide([], t6, desk, adequacy(n=101))
    assert first.action is Action.FIELD_AUDIT and first.field_channels == two
    assert first.reason_codes == ("FIELD_VERIFICATION_REQUIRED", "ORDER_FIELD_EVIDENCE")
    confirmed = desk + [finding(True, 0.85, "field_review"), finding(True, 0.80, "field_review")]
    assert decide([], t6, confirmed, adequacy(n=101), frozenset(two)).action is Action.SUSPEND
    split = desk + [finding(True, 0.85, "field_review"), finding(False, 0.80, "field_review")]
    assert decide([], t6, split, adequacy(n=101), frozenset(two)).action is Action.NO_ACTION


def test_the_floor_is_applied_to_the_unrounded_confidence():
    """0.6996 would round to 0.700 and clear a floor it is below; it is recorded as 0.69, never shown as 0.70."""
    just_below = [finding(True, 0.6996)]
    v = decide([], hit(channels=()), just_below, adequacy())
    assert v.action is Action.NO_ACTION and v.confidence == 0.69
    at_floor = decide([], hit(channels=()), [finding(True, 0.70)], adequacy(n=101))
    assert at_floor.action is Action.SUSPEND and at_floor.confidence == 0.70


def test_the_real_triggers_declare_which_desk_findings_are_proof():
    from rules.triggers import SPECS
    assert SPECS["T10"].desk_can_suspend and not SPECS["T6"].desk_can_suspend and not SPECS["T5"].desk_can_suspend


def test_weak_partial_field_evidence_orders_the_missing_channels():
    two = hit(channels=(Channel.BENEFICIARY_CALL, Channel.BENEFICIARY_VISIT))
    v = decide([], two, [finding(True, 0.60, agent="field_review")], adequacy(), frozenset({Channel.BENEFICIARY_CALL}))
    assert v.action is Action.FIELD_AUDIT and v.field_channels == (Channel.BENEFICIARY_VISIT,)


def test_distance_override_is_for_the_sweep_only():
    a = adequacy(n=1, km=83)
    assert access_gate(a)[0] == "protect"
    assert access_gate(a, distance_km=100)[0] == "clear"
    assert access_gate(a, distance_km=0)[0] == "protect"


def test_access_gate_reports_its_facts():
    gate, facts = access_gate(adequacy(n=1, km=83.3))
    assert gate == "protect" and "PROVIDERS_IN_DISTRICT=1" in facts and "KM_TO_ALTERNATIVE=83" in facts


def test_suspension_is_gated_on_every_specialty_the_hospital_provides():
    """F-22: suspension removes a hospital from every specialty. Well served in the billed one is not enough."""
    billed = adequacy(n=101)
    elsewhere = adequacy(n=1, km=120, specialty="neonatal")
    v = decide([], hit(), FRAUD, billed, network=[billed, elsewhere])
    assert v.action is Action.ESCALATE_SEC and v.gate == "protect" and v.human_required
    assert [a.specialty for a in v.at_stake] == ["neonatal"]
    assert {"SOLE_REAL_PROVIDER=neonatal", "SPECIALTY_AT_STAKE=neonatal"} <= set(v.reason_codes)
    assert decide([], hit(), FRAUD, billed, network=[billed, adequacy(n=4, specialty="neonatal")]).action is Action.SUSPEND


def test_a_hospital_not_empanelled_for_what_it_billed_is_still_protected_for_what_it_provides():
    billed = adequacy(n=3, hospital_listed=False)
    v = decide([], hit(), FRAUD, billed, network=[adequacy(n=2, aspirational=True, specialty="obgyn")])
    assert v.action is Action.ESCALATE_SEC and [a.specialty for a in v.at_stake] == ["obgyn"]


def test_an_implausible_listing_elsewhere_protects_nothing():
    billed = adequacy(n=40)
    phantom_elsewhere = adequacy(n=1, km=150, capability_ok=False, specialty="radiation_oncology")
    assert decide([], hit(), FRAUD, billed, network=[billed, phantom_elsewhere]).action is Action.SUSPEND


def test_a_phantom_billed_specialty_is_referred_whatever_else_the_hospital_provides():
    """The referral de-lists one specialty; what a suspension would remove still travels with it, for the Committee."""
    billed = adequacy(n=1, km=120, capability_ok=False)
    v = decide([], hit(), FRAUD, billed, network=[billed, adequacy(n=1, km=None, specialty="neonatal")])
    assert v.action is Action.DELIST_SPECIALTY and [a.specialty for a in v.at_stake] == ["neonatal"]
    assert decide([], hit(), FRAUD, billed).at_stake == ()


def test_every_specialty_at_stake_is_listed_billed_first():
    billed = adequacy(n=1, km=83)
    network = [adequacy(n=1, specialty="urology"), billed, adequacy(n=2, aspirational=True, specialty="ent")]
    gate, _, stake = suspension_gate(billed, network)
    assert gate == "protect" and [a.specialty for a in stake] == ["cardiology", "ent", "urology"]


def test_decide_requires_the_network():
    """No default: a caller that forgot it would silently gate on the billed specialty alone."""
    with pytest.raises(TypeError):
        policy.decide([], hit(), FRAUD, adequacy(n=101))          # noqa - the missing argument is the point


def test_no_single_field_report_can_suspend_a_hospital():
    """Every egregious trigger orders two independent field channels (docs/09-EVALUATION.md)."""
    from rules.triggers import SPECS
    egregious = [s for s in SPECS.values() if s.severity is Severity.EGREGIOUS]
    assert egregious and all(len(set(s.field_channels)) >= 2 for s in egregious)


# ── EC-1 is enforced by the type ───────────────────────────────────────────

def test_a_real_hospital_identity_cannot_enter_the_pipeline():
    t = datetime(2026, 6, 1)
    with pytest.raises(ValidationError, match="EC-1"):
        Claim(claim_id="c", hospital_ref="HOSP6P66487", district_code=1, package_code="p", beneficiary_ref="b",
              admission_ts=t, discharge_ts=t, amount_claimed=1, submitted_ts=t)


def test_a_blank_defence_never_stops_an_action():
    """F-64. `defence` is a public parameter of decide(); an empty or whitespace string is not an innocent account,
    and must never be what stops an enforcement action against a hospital. The advocacy guardrail will not pass one,
    but the policy may not rely on a guardrail upstream of it."""
    from rules.policy import DEFENDABLE, Verdict, defended
    from crew.schemas import Action, Channel
    v = Verdict(Action.SHOW_CAUSE, ("CONFIRMED",), 0.9)
    assert Action.SHOW_CAUSE in DEFENDABLE                      # it would be stopped by a real defence
    for blank in (None, "", "   ", chr(9) + chr(10)):
        assert defended(v, blank, (Channel.HOSPITAL_VISIT,)).action is Action.SHOW_CAUSE, repr(blank)
    assert defended(v, "an account the file bears", (Channel.HOSPITAL_VISIT,)).action is Action.FIELD_AUDIT
