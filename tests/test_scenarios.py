"""The ten scenarios end to end (docs/05-TEST-PLAN.md), deterministic path."""
import re
from datetime import timedelta

import pandas as pd
import pytest

from crew.llm import ClaudeLLM
from crew.run import OutageClient, process
from crew.schemas import Action, Channel, Document
from crew.tools import ROOT

PRIMARY = {"S1": "T10", "S2": "T10", "S3": "T2", "S4": "R1", "S5": "T6", "S6": "T5", "S7": "T7", "S8": "T7",
           "S9": "T10", "S10": None}


def run(world, tools, sid, tmp_path, **kw):
    scenarios, store = world
    return process(scenarios[sid].claim, store, tools, out_dir=tmp_path, **kw)


@pytest.mark.parametrize("sid", [f"S{i}" for i in range(1, 11)])
def test_scenario_produces_expected_action(world, tools, sid, tmp_path):   # AC-1
    d = run(world, tools, sid, tmp_path)
    assert d.action is world[0][sid].expected


@pytest.mark.parametrize("sid", [f"S{i}" for i in range(1, 10)])
def test_scenario_fires_only_its_intended_trigger(world, tools, sid, tmp_path):
    """Guards the fixtures: an accidental extra trigger would test something else."""
    d = run(world, tools, sid, tmp_path)
    assert d.trigger_id == PRIMARY[sid]
    assert d.triggers_fired == [PRIMARY[sid]]


def test_outcomes_are_genuinely_distinct(world, tools, tmp_path):                 # AC-2
    actions = {run(world, tools, sid, tmp_path).action for sid in world[0]}
    assert len(actions) >= 6


def test_thesis_same_fraud_different_decision(world, tools, tmp_path):           # AC-3
    s1, s2 = run(world, tools, "S1", tmp_path), run(world, tools, "S2", tmp_path)
    c1, c2 = world[0]["S1"].claim, world[0]["S2"].claim
    assert (s1.trigger_id, c1.package_code, c1.amount_claimed, c1.los_days) == \
           (s2.trigger_id, c2.package_code, c2.amount_claimed, c2.los_days)
    assert [(f.agent, f.supports_fraud, f.confidence) for f in s1.findings] == \
           [(f.agent, f.supports_fraud, f.confidence) for f in s2.findings]
    assert s1.adequacy.specialty == s2.adequacy.specialty == "cardiology"
    assert s1.action is Action.SUSPEND and s2.action is Action.ESCALATE_SEC
    assert s2.adequacy.n_providers == 1 and s2.adequacy.aspirational and s2.adequacy.km_to_alternative >= 60
    assert s1.adequacy.n_providers >= 50


def test_channels_follow_the_evidence(world, tools, tmp_path):                    # AC-4
    s1, s6, s7 = (run(world, tools, sid, tmp_path) for sid in ("S1", "S6", "S7"))
    assert s6.channels_used == world[0]["S6"].expected_channels
    assert s1.channels_used == [Channel.DESK_AUDIT] and s7.channels_used == [Channel.DESK_AUDIT]
    assert s6.channels_used != s1.channels_used
    assert s7.field_channels_ordered == [Channel.BENEFICIARY_CALL]


def test_conflicting_field_reports_are_recorded(world, tools, tmp_path):
    d = run(world, tools, "S8", tmp_path)
    assert d.conflicts and d.confidence < 0.70 and d.human_required


def test_a_lama_form_does_not_explain_billing_a_deferred_procedure(world, tools, tmp_path):
    """F-06, found by the Gemini crew: leaving the same day explains a zero stay, not billing surgery never done."""
    s3 = world[0]["S3"].claim
    claim = s3.model_copy(update={"claim_id": "CLM-S03-DEFERRED", "documents": [
        Document(doc_type=d.doc_type, text=d.text.replace("Procedure performed as planned", "Procedure deferred"))
        for d in s3.documents]})
    d = process(claim, world[1], tools, out_dir=tmp_path)
    assert d.trigger_id == "T2" and d.action is Action.FIELD_AUDIT
    assert d.field_channels_ordered == [Channel.HOSPITAL_VISIT, Channel.BENEFICIARY_CALL]


def test_the_desk_cannot_clear_a_claim_missing_mandatory_documents(world, tools, tmp_path):
    """NHA trigger-2 checklist: verify mandatory documents. An explanation without them is inconclusive."""
    s3 = world[0]["S3"].claim
    claim = s3.model_copy(update={"claim_id": "CLM-S03-NO-OPNOTE",
                                  "documents": [d for d in s3.documents if d.doc_type != "operative_note"]})
    d = process(claim, world[1], tools, out_dir=tmp_path)
    desk = next(f for f in d.findings if f.agent == "desk_audit")
    assert d.action is Action.FIELD_AUDIT and desk.supports_fraud is None
    assert "operative or procedure note" in desk.conclusion


def test_a_long_stay_is_justified_by_clinical_notes_not_a_package_name(world, tools, tmp_path):
    """A discharge summary repeating 'Severe sepsis' must not clear a padded stay; clinical notes can."""
    from crew.investigate import CLINICAL_JUSTIFICATION
    from rules.triggers import ACUTE_MEDICAL
    s7 = world[0]["S7"].claim
    hospital = tools.registry_lookup(s7.hospital_ref)
    public = hospital.hospital_type in ("Public", "GOI")
    pkg = sorted((p for p in tools.packages() if p.specialty in ACUTE_MEDICAL and p.specialty in hospital.specialties
                  and CLINICAL_JUSTIFICATION.search(p.package_name) and (public or not p.govt_reserved)),
                 key=lambda p: p.package_code)[0]
    from crew.investigate import DISCHARGE_SUMMARY, required_documents
    base = [Document(doc_type="discharge_summary", text=f"Discharge summary, CLM-T4. {pkg.package_name}. Stable.")]
    base += [Document(doc_type=r.filed_as, text=f"CLM-T4: {r.label} on file.")         # every mandatory category
             for r in required_documents(pkg) if r is not DISCHARGE_SUMMARY]

    def claim(cid, docs):
        return s7.model_copy(update={"claim_id": cid, "beneficiary_ref": f"BEN-{cid}", "package_code": pkg.package_code,
                                     "amount_claimed": pkg.amount_rs, "documents": docs,
                                     "discharge_ts": s7.admission_ts + timedelta(days=12, hours=6),
                                     "submitted_ts": s7.admission_ts + timedelta(days=12, hours=10)})

    padded = process(claim("CLM-T4-PAD", base), world[1], tools, out_dir=tmp_path)
    notes = [Document(doc_type="clinical_notes", text="CLM-T4: hospital-acquired pneumonia, a complication on day 6.")]
    justified = process(claim("CLM-T4-OK", base + notes), world[1], tools, out_dir=tmp_path)
    assert padded.triggers_fired == ["T4"] and padded.action is Action.SHOW_CAUSE
    assert justified.action is Action.RELEASE_CLAIM
    # F-23: notes that record what did NOT happen are the padded stay, not its justification
    absent = [Document(doc_type="progress_notes", text="CLM-T4: stable, no complications; non-critical course. "
                                                       "ICU admission not required. Sepsis ruled out.")]
    assert process(claim("CLM-T4-NEG", base + absent), world[1], tools, out_dir=tmp_path).action is Action.SHOW_CAUSE


def test_a_negated_discharge_explanation_explains_nothing(world, tools, tmp_path):
    """F-23: 'Not a LAMA case' is the absence of the explanation the desk looks for."""
    s3 = world[0]["S3"].claim
    negated = [Document(doc_type=d.doc_type, text="Not a LAMA case; no DAMA or discharge on request (DOR) recorded.")
               if d.doc_type == "lama_form" else d for d in s3.documents]
    d = process(s3.model_copy(update={"claim_id": "CLM-S03-NOTLAMA", "documents": negated}), world[1], tools,
                out_dir=tmp_path)
    assert d.trigger_id == "T2" and d.action is Action.FIELD_AUDIT
    # ...and a procedure the file says was NOT deferred does not block a documented release
    kept = s3.documents + [Document(doc_type="progress_notes", text="CLM-S03: surgery was not deferred.")]
    ok = process(s3.model_copy(update={"claim_id": "CLM-S03-NOTDEF", "documents": kept}), world[1], tools,
                 out_dir=tmp_path)
    assert ok.action is Action.RELEASE_CLAIM


def test_phantom_referral_rests_on_site_verification(world, tools, tmp_path):
    d = run(world, tools, "S5", tmp_path)
    assert d.action is Action.DELIST_SPECIALTY and d.channels_used == world[0]["S5"].expected_channels


def test_a_death_register_entry_alone_orders_field_verification(world, tools, tmp_path):
    """Guidebook trigger 10 verifies a date-of-death mismatch in the field. Without a certificate on
    file, acting at the desk would suspend a hospital over a register error."""
    s1 = world[0]["S1"].claim
    bare = s1.model_copy(update={"claim_id": "CLM-S01-NOCERT",
                                 "documents": [d for d in s1.documents if d.doc_type != "death_certificate"]})
    d = process(bare, world[1], tools, out_dir=tmp_path)
    assert d.trigger_id == "T10" and d.action is Action.FIELD_AUDIT
    assert d.field_channels_ordered == [Channel.BENEFICIARY_CALL, Channel.BENEFICIARY_VISIT]


def test_crew_outage_degrades_rather_than_crashes(world, tools, tmp_path):        # AC-5
    d = run(world, tools, "S9", tmp_path, llm=ClaudeLLM().with_client(OutageClient()))
    assert d.degraded and d.action is Action.ESCALATE_SEC


def test_malformed_claim_is_refused_with_reasons(world, tools, tmp_path):         # AC-6
    d = run(world, tools, "S10", tmp_path)
    assert d.action is Action.REFUSE
    assert {"MALFORMED_MISSING_DISCHARGE", "MALFORMED_AMBIGUOUS_DISTRICT"} <= set(d.reason_codes)


def test_runs_are_reproducible(world, tools, tmp_path):                           # AC-7
    logs = []
    for i in (1, 2):
        out = tmp_path / f"run{i}"
        for sid in world[0]:
            run(world, tools, sid, out)
        logs.append((out / "decision_log.csv").read_bytes())
    assert logs[0] == logs[1]


def test_no_real_identity_leaks_into_artefacts(world, tools, tmp_path):          # AC-8
    for sid in world[0]:
        run(world, tools, sid, tmp_path)
    reg = pd.read_excel(ROOT / "data/registry/PMJAY_empanelled_hospitals_2026-07-16.xls")
    names = {str(n).strip().upper() for n in reg["Hospital Name"] if len(str(n).strip()) >= 12}
    texts = [p.read_text(encoding="utf-8") for p in (tmp_path / "artefacts").glob("*.md")]
    assert texts, "no artefacts were written"
    for text in texts:
        upper = text.upper()
        assert not re.search(r"HOSP\d", text), "an NHA hospital id leaked"
        leaked = [n for n in names if n in upper]
        assert not leaked, f"EC-1 violation: {leaked[:3]}"
        assert "SIMULATED CASE" in text


# ── correctness audit, 17 September: each test fails without its fix ─────

def _t10(world, tools, cid, hospital, specialty):
    """A billing-after-death claim with a certificate on file, at any hospital, for any specialty."""
    from crew.schemas import Claim
    s1 = world[0]["S1"].claim
    pkg = sorted((p for p in tools.packages() if p.specialty == specialty and not p.govt_reserved and p.amount_rs > 0),
                 key=lambda p: p.package_code)[0]
    return Claim(**{**s1.model_dump(), "claim_id": cid, "beneficiary_ref": f"BEN-{cid}",
                    "hospital_ref": hospital.hospital_ref, "district_code": hospital.district_code,
                    "package_code": pkg.package_code, "amount_claimed": pkg.amount_rs,
                    "documents": [Document(doc_type=d.doc_type,
                                           text=d.text.replace("CLM-S01", cid).replace("BEN-10001", f"BEN-{cid}"))
                                  for d in s1.documents]})


def test_malformed_timelines_are_refused_not_read_as_zero_stays(world, tools, tmp_path):
    s3 = world[0]["S3"].claim
    backwards = s3.model_copy(update={"claim_id": "CLM-TL-1", "discharge_ts": s3.admission_ts - timedelta(days=1)})
    early = s3.model_copy(update={"claim_id": "CLM-TL-2", "submitted_ts": s3.discharge_ts - timedelta(hours=1)})
    d1, d2 = (process(c, world[1], tools, out_dir=tmp_path) for c in (backwards, early))
    assert d1.action is Action.REFUSE and "MALFORMED_DISCHARGE_BEFORE_ADMISSION" in d1.reason_codes
    assert d2.action is Action.REFUSE and "MALFORMED_SUBMITTED_BEFORE_DISCHARGE" in d2.reason_codes


def test_blank_documents_are_not_document_reuse(world, tools):
    from rules import triggers
    s3 = world[0]["S3"].claim
    a = s3.model_copy(update={"claim_id": "CLM-BL-A", "beneficiary_ref": "BEN-BL-A",
                              "documents": s3.documents + [Document(doc_type="lab_report", text="  ")]})
    b = a.model_copy(update={"claim_id": "CLM-BL-B", "beneficiary_ref": "BEN-BL-B",
                             "documents": [Document(doc_type="lab_report", text="  ")]})
    store = triggers.ClaimStore([a, b])
    hits = triggers.evaluate(a, tools.registry_lookup(a.hospital_ref), tools.hbp_lookup(a.package_code), store, tools)
    assert "T6" not in {h.trigger_id for h in hits}


def test_a_blank_registry_list_is_not_evidence_and_escalates_unknown_access(world, tools, tmp_path):
    """D-4: 30% of hospitals list no specialty. No R1; an egregious case there cannot be judged for access."""
    blank = sorted((h for h in tools.hospitals() if not h.specialties and tools.district_adequacy(h, "cardiology")),
                   key=lambda h: h.hospital_ref)[0]
    d = process(_t10(world, tools, "CLM-BLANK", blank, "cardiology"), world[1], tools, out_dir=tmp_path)
    assert d.triggers_fired == ["T10"]
    assert d.action is Action.ESCALATE_SEC and d.gate == "unknown"
    assert "registry lists no specialties" in d.explanation
    assert "could not be established from the registry" in (tmp_path / d.artefact_path).read_text(encoding="utf-8")


def test_registry_evidence_counts_only_for_r1(world, tools, tmp_path):
    """A registry mismatch says nothing about repeat admissions: T7 with an inconclusive desk buys evidence."""
    from rules import triggers
    s7 = world[0]["S7"].claim
    home = tools.registry_lookup(s7.hospital_ref)
    other = sorted((h for h in tools.hospitals() if h.district_code == home.district_code and h.specialties
                    and "general_medicine" not in h.specialties), key=lambda h: h.hospital_ref)[0]
    episodes = [c.model_copy(update={"hospital_ref": other.hospital_ref})
                for c in world[1].by_beneficiary[s7.beneficiary_ref]]
    index = next(c for c in episodes if c.claim_id == s7.claim_id)
    d = process(index, triggers.ClaimStore(episodes), tools, out_dir=tmp_path)
    assert set(d.triggers_fired) == {"T7", "R1"} and d.trigger_id == "T7"
    assert d.action is Action.FIELD_AUDIT
    reg = next(f for f in d.findings if f.agent == "registry_verification")
    assert reg.supports_fraud is None and "bears on R1" in reg.conclusion


def test_districts_without_geometry_still_get_adequacy(world, tools, tmp_path):
    """167 listed hospitals sit in 23 districts with no boundary: counts are known, only distance is not."""
    points = set(pd.read_csv(ROOT / "data/reference/district_points.csv").district_code)
    priced = {p.specialty for p in tools.packages() if not p.govt_reserved and p.amount_rs > 0}
    h = sorted((h for h in tools.hospitals() if h.district_code not in points and set(h.specialties) & priced),
               key=lambda h: h.hospital_ref)[0]
    spec = sorted(set(h.specialties) & priced)[0]
    a = tools.district_adequacy(h, spec)
    assert a is not None and a.km_to_alternative is None and a.n_providers >= 1
    d = process(_t10(world, tools, "CLM-NOGEO", h, spec), world[1], tools, out_dir=tmp_path)
    assert d.adequacy is not None and d.action in (Action.SUSPEND, Action.ESCALATE_SEC, Action.DELIST_SPECIALTY)


def test_r3_needs_a_hospital_known_to_be_private(world, tools):
    from crew.schemas import Hospital
    from rules import triggers
    s4 = world[0]["S4"].claim
    reserved = sorted((p for p in tools.packages() if p.govt_reserved and p.specialty), key=lambda p: p.package_code)[0]
    claim = s4.model_copy(update={"claim_id": "CLM-R3", "package_code": reserved.package_code,
                                  "amount_claimed": reserved.amount_rs})
    store = triggers.ClaimStore([claim])

    def fired(hospital_type):
        h = Hospital(hospital_ref="HOSP-99999", district_code=s4.district_code, state="GUJARAT",
                     hospital_type=hospital_type, basic_tier=False, specialties=[reserved.specialty])
        return {x.trigger_id for x in triggers.evaluate(claim, h, reserved, store, tools)}
    assert "R3" in fired("Private(For Profit)")
    assert "R3" not in fired("") and "R3" not in fired("Public")


# ── second correctness audit, 17 September: each test fails without its fix ─

def _adequacy(**kw):
    from crew.schemas import Adequacy
    base = dict(district_code=1, district="D", state="S", specialty="obgyn", specialty_name="Obstetrics",
                n_providers=2, km_to_alternative=None, nearest_alternative=None, population=1_000_000,
                aspirational=True, capability_ok=True, state_convention=False, capability_reason="r")
    return Adequacy(**{**base, **kw})


def test_suspension_checks_every_specialty_the_hospital_provides(world, tools, tmp_path):
    """F-22: well served in the billed specialty, but the district's only real provider of another one."""
    priced = {p.specialty for p in tools.packages() if not p.govt_reserved and p.amount_rs > 0}
    for h in sorted((h for h in tools.hospitals() if len(h.specialties) >= 3), key=lambda h: h.hospital_ref):
        network = tools.hospital_network(h)
        busy = [a for a in network if a.n_providers >= 10 and a.specialty in priced]
        sole = [a for a in network if a.n_providers == 1 and a.capability_ok and not a.aspirational
                and a.km_to_alternative is not None and a.km_to_alternative >= 100]
        if busy and sole:
            break
    d = process(_t10(world, tools, "CLM-F22", h, busy[0].specialty), world[1], tools, out_dir=tmp_path)
    assert d.adequacy.n_providers >= 10 and d.action is Action.ESCALATE_SEC and d.gate == "protect"
    assert sole[0].specialty in [a.specialty for a in d.access_at_stake]
    assert f"the only real provider of {sole[0].specialty_name}" in d.explanation
    artefact = (tmp_path / d.artefact_path).read_text(encoding="utf-8")
    assert "What a suspension would remove" in artefact and sole[0].specialty_name in artefact
    log = pd.read_csv(tmp_path / "decision_log.csv", keep_default_na=False)
    assert sole[0].specialty in log.set_index("claim_id").loc["CLM-F22", "specialties_at_stake"].split("|")


def test_a_death_on_the_day_of_admission_is_not_billing_after_death(world, tools, tmp_path):
    """F-24: a register records a date. Death 'at midnight' preceded a same-day admission by the clock."""
    s1 = world[0]["S1"].claim
    midnight = s1.admission_ts.replace(hour=0, minute=0)
    same_day = s1.model_copy(update={"claim_id": "CLM-SAMEDAY", "beneficiary_death_ts": midnight})
    d = process(same_day, world[1], tools, out_dir=tmp_path)
    assert d.triggers_fired == [] and d.action is Action.RELEASE_CLAIM
    eve = s1.model_copy(update={"claim_id": "CLM-EVE", "beneficiary_death_ts": midnight - timedelta(minutes=1)})
    assert process(eve, world[1], tools, out_dir=tmp_path).triggers_fired == ["T10"]      # the day before still fires


def test_a_district_without_geometry_can_be_named(world, tools, tmp_path):
    """F-25: names resolved against districts with boundaries only, so 346 hospitals' districts were 'unknown'."""
    points = set(pd.read_csv(ROOT / "data/reference/district_points.csv").district_code)
    priced = {p.specialty for p in tools.packages() if not p.govt_reserved and p.amount_rs > 0}
    h = sorted((h for h in tools.hospitals() if h.district_code not in points and set(h.specialties) & priced),
               key=lambda h: h.hospital_ref)[0]
    name = tools.district_label(h.district_code).split(",")[0]
    assert tools.resolve_district(name.upper()) == [h.district_code] and not name.startswith("district ")
    spec = sorted(set(h.specialties) & priced)[0]
    named = _t10(world, tools, "CLM-NAMED", h, spec).model_copy(update={"district_code": None, "district_name": name})
    d = process(named, world[1], tools, out_dir=tmp_path)
    assert d.action is not Action.REFUSE and d.trigger_id == "T10"


def test_repeat_admissions_are_counted_in_the_30_days_to_this_one(world, tools):
    """F-30: a window either side called admissions 50 days apart 'within 30 days', and counted ones not yet made."""
    from rules import triggers
    s7 = world[0]["S7"].claim
    hospital, package = tools.registry_lookup(s7.hospital_ref), tools.hbp_lookup(s7.package_code)

    def episodes(*days):
        claims = [s7.model_copy(update={"claim_id": f"CLM-T7W-{x}", "beneficiary_ref": "BEN-T7W",
                                        "admission_ts": s7.admission_ts + timedelta(days=x),
                                        "discharge_ts": s7.admission_ts + timedelta(days=x, hours=30),
                                        "submitted_ts": s7.admission_ts + timedelta(days=x, hours=40)}) for x in days]
        store = triggers.ClaimStore(claims)
        return {c.claim_id: [h for h in triggers.evaluate(c, hospital, package, store, tools) if h.trigger_id == "T7"]
                for c in claims}

    spread = episodes(0, 25, 50)
    assert not any(spread.values())
    tight = episodes(0, 10, 20)
    assert not tight["CLM-T7W-0"] and not tight["CLM-T7W-10"]
    assert "in the 30 days to this admission" in tight["CLM-T7W-20"][0].evidence


def test_the_runner_never_deletes_a_directory_it_did_not_write(tmp_path, monkeypatch):
    """F-27: `--out results` meant shutil.rmtree(results), before the arguments were even checked."""
    import sys
    from crew import run as runner
    precious = tmp_path / "results"
    precious.mkdir()
    (precious / "metrics.json").write_text("{}")
    assert "did not write" in runner.clear_previous_run(precious)
    monkeypatch.setattr(sys, "argv", ["crew.run", "--out", str(precious)])       # no --scenarios: an error
    with pytest.raises(SystemExit):
        runner.main()
    assert (precious / "metrics.json").exists()
    previous = tmp_path / "out"
    (previous / "artefacts").mkdir(parents=True)
    (previous / "decision_log.csv").write_text("x")
    assert runner.clear_previous_run(previous) is None and list(previous.iterdir()) == []


def test_explanations_state_the_access_facts_exactly(world, tools):
    """F-32: 'the only real provider' for one of two; '1 empanelled providers ... does not remove access' for a
    sole provider with a neighbour in reach; 'empanelled for None'; 'the documents cannot settle' for weak field
    evidence."""
    from crew.investigate import registry_verification
    from crew.run import explain
    from crew.schemas import Severity, TriggerHit
    from rules.policy import Verdict
    hit = TriggerHit(trigger_id="T10", name="t", severity=Severity.EGREGIOUS, evidence="e", field_channels=[],
                     checklist=[], source="s")
    two = _adequacy()
    text = explain(Verdict(Action.ESCALATE_SEC, (), 0.95, gate="protect", at_stake=(two,)), hit, two, [two])
    assert "one of only 2 providers of Obstetrics" in text and "only real provider" not in text
    near = _adequacy(specialty="cardiology", specialty_name="Cardiology", n_providers=1, km_to_alternative=31.0,
                     nearest_alternative="Gonda", aspirational=False)
    text = explain(Verdict(Action.SUSPEND, (), 0.95, gate="clear"), hit, near, [near])
    assert "providers of Cardiology" not in text and "does not remove access" not in text and "Gonda, 31 km" in text
    weak = Verdict(Action.FIELD_AUDIT, ("BELOW_CONFIDENCE_FLOOR", "ORDER_FIELD_EVIDENCE"), 0.5)
    assert "falls below the confidence floor" in explain(weak, hit, None)
    s3 = world[0]["S3"].claim
    unmapped = tools.hbp_lookup(s3.package_code).model_copy(update={"specialty": None})
    assert "None" not in registry_verification(s3, hit, tools.registry_lookup(s3.hospital_ref), unmapped).conclusion


def test_inpatient_packages_are_not_day_care(world, tools):
    """F-33: substring matching marked skin-grafting burns surgery ('follow-up dressings') and COPD ('opd') as
    day-care, so a zero-length stay on them could never fire trigger 2 or 3."""
    from rules import triggers
    for code in ("BM001B", "BM004D", "BM006A"):
        assert tools.hbp_lookup(code).is_major and not tools.hbp_lookup(code).daycare_candidate
    for code in ("MG072A", "MG074A", "BM001A", "SU017A", "ER001A", "SB052B"):
        assert tools.hbp_lookup(code).daycare_candidate                      # genuine same-day care stays listed
    s3 = world[0]["S3"].claim

    def zero_stay(code, specialty):
        h = sorted((h for h in tools.hospitals() if specialty in h.specialties and h.hospital_type == "Public"),
                   key=lambda h: h.hospital_ref)[0]
        p = tools.hbp_lookup(code)
        c = s3.model_copy(update={"claim_id": f"CLM-{code}", "hospital_ref": h.hospital_ref,
                                  "district_code": h.district_code, "package_code": code,
                                  "amount_claimed": p.amount_rs, "beneficiary_ref": f"BEN-{code}"})
        return {x.trigger_id for x in triggers.evaluate(c, h, p, triggers.ClaimStore([c]), tools)}
    assert "T2" in zero_stay("BM001B", "burns")
    assert "T3" in zero_stay("MG029A", "general_medicine")


def test_a_log_from_another_version_is_never_appended_to(world, tools, tmp_path):
    """A new column under an old header would shift every field of every new row."""
    (tmp_path / "decision_log.csv").write_text("case_id,claim_id,action\r\n", encoding="utf-8")
    with pytest.raises(ValueError, match="different columns"):
        run(world, tools, "S1", tmp_path)
