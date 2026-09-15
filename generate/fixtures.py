"""The ten test scenarios (docs/05-TEST-PLAN.md §2).

Built from real reference data, never hard-coded: hospitals are chosen from the
pseudonymised registry by the property each scenario needs, and every choice is
asserted. If the data ever stops supporting a scenario -- as happened to the
original Gaya demo case -- this module fails loudly instead of testing a fiction.

Claims, documents and conduct are simulated. District network structure is real.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from crew.schemas import Action, Channel, Claim, Document, FieldReport, Hospital, Package
from crew.tools import BULK_STATES, Tools
from rules.triggers import ClaimStore

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 6, 10, 9, 0)


@dataclass
class Scenario:
    sid: str
    title: str
    claim: Claim
    expected: Action
    proves: str
    simulate_llm_outage: bool = False
    expected_channels: list[Channel] = field(default_factory=list)


# ── selection helpers ─────────────────────────────────────────────────────

def _package(tools: Tools, specialty: str, *, major: bool, lo: int = 1_000, hi: int = 250_000) -> Package:
    # Reserved under no listing at all, so no scenario can turn on which listing a hospital bills through.
    c = sorted((p for p in tools.packages()
                if p.specialty == specialty and not any(p.reserved_under.values()) and lo <= p.amount_rs <= hi
                and (p.is_major if major else not p.daycare_candidate)),
               key=lambda p: (p.amount_rs, p.package_code))
    assert c, f"no {'major ' if major else ''}{specialty} package in Rs {lo:,}-{hi:,}"
    return c[len(c) // 2]


def _hospitals(tools: Tools, district: int, *, has: str | None = None, lacks: str | None = None,
               basic_tier: bool | None = None) -> list[Hospital]:
    return sorted((h for h in tools.hospitals()
                   if h.district_code == district
                   and (has is None or has in h.specialties)
                   and (lacks is None or lacks not in h.specialties)
                   and (basic_tier is None or h.basic_tier == basic_tier)),
                  key=lambda h: h.hospital_ref)


def _claim(cid: str, h: Hospital, p: Package, ben: str, *, admit: datetime = T0, los: int = 3,
           docs: list[Document] | None = None, amount: int | None = None, **kw) -> Claim:
    return Claim(claim_id=cid, hospital_ref=h.hospital_ref, district_code=h.district_code, package_code=p.package_code,
                 beneficiary_ref=ben, admission_ts=admit, discharge_ts=admit + timedelta(days=los, hours=6),
                 amount_claimed=p.amount_rs if amount is None else amount, documents=docs or [],
                 submitted_ts=admit + timedelta(days=los, hours=10), **kw)


def _named(tools: Tools, code: str, specialty: str) -> Package:
    """A package the scenario needs by name, checked to be what the scenario assumes."""
    p = tools.hbp_lookup(code)
    assert p is not None and p.specialty == specialty and not any(p.reserved_under.values()) \
        and not p.daycare_candidate, f"{code} is no longer an unreserved, inpatient {specialty} package"
    return p


def _summary(cid: str, ben: str, p: Package, note: str = "Uneventful recovery; discharged in stable condition.") -> Document:
    return Document(doc_type="discharge_summary",
                    text=f"Discharge summary, claim {cid}, beneficiary {ben}. Procedure: {p.package_name}. {note}")


def build_scenarios(tools: Tools) -> tuple[list[Scenario], ClaimStore]:
    fig = json.loads((ROOT / "data/reference/figures.json").read_text(encoding="utf-8"))
    BAHRAICH, AHMEDABAD = fig["demo_escalate"]["district_code"], fig["demo_contrast"]["district_code"]
    support: list[Claim] = []

    # ── S1 / S2 · the thesis: identical fraud, two districts ──────────────
    cardio = _package(tools, "cardiology", major=True)
    sole = _hospitals(tools, BAHRAICH, has="cardiology")
    assert len(sole) == 1 and not sole[0].basic_tier, "Bahraich must have exactly one real cardiology provider"
    busy = _hospitals(tools, AHMEDABAD, has="cardiology", basic_tier=False)
    assert len(busy) >= 50, "Ahmedabad must be well served in cardiology"

    def after_death(cid: str, h: Hospital, ben: str) -> Claim:
        died = T0 - timedelta(days=13)
        return _claim(cid, h, cardio, ben, beneficiary_death_ts=died, docs=[
            _summary(cid, ben, cardio),
            Document(doc_type="death_certificate",
                     text=f"Certificate of death. Beneficiary {ben}. Date of death {died:%d %B %Y}. "
                          f"Registered with the municipal registrar."),
            Document(doc_type="pre_investigation", text=f"Claim {cid}: {cardio.pre_investigations}")])

    s1 = after_death("CLM-S01", busy[0], "BEN-10001")
    s2 = after_death("CLM-S02", sole[0], "BEN-10002")

    # ── S3 · zero length of stay, explained ───────────────────────────────
    # Every document the package master requires is on file, as NHA's trigger-2 checklist requires ("Verify
    # mandatory documents for blocked procedure"); a live crew rightly refused to clear the claim without them
    # (F-06). The procedure is one a patient can plausibly walk out of the same evening: the median major package
    # was a partial gastrectomy, whose histopathology and intra-operative photograph the file lacked, and the live
    # crew with tools issued a show-cause notice asking for exactly those (F-55).
    gs = _named(tools, "SG053B", "general_surgery")
    assert gs.is_major, "S3 needs a major surgical package: trigger 2 fires only on one"
    gsh = _hospitals(tools, AHMEDABAD, has="general_surgery", basic_tier=False)[0]
    s3 = _claim("CLM-S03", gsh, gs, "BEN-10003", los=0, docs=[
        Document(doc_type="clinical_notes",
                 text="Claim CLM-S03, pre-procedure clinical notes: heartburn and regurgitation for two years despite "
                      "medication; sliding hiatus hernia confirmed; laparoscopic repair planned."),
        Document(doc_type="radiology_report",
                 text="Claim CLM-S03: barium swallow X-ray and USG abdomen show a sliding hiatus hernia."),
        Document(doc_type="endoscopy_report",
                 text="Claim CLM-S03: upper GI endoscopy shows a sliding hiatus hernia with grade B oesophagitis."),
        Document(doc_type="operative_note",
                 text=f"Claim CLM-S03, detailed operative note: {gs.package_name} with fundoplication performed under "
                      f"general anaesthesia; procedure completed; patient shifted to recovery in stable condition."),
        Document(doc_type="intra_procedure_photograph",
                 text="Claim CLM-S03: intra-procedure clinical photographs of the hiatal repair, with patient ID and "
                      "date."),
        _summary("CLM-S03", "BEN-10003", gs, "Procedure performed as planned; patient stable in recovery."),
        Document(doc_type="lama_form",
                 text="Patient left against medical advice (LAMA) on the evening of the procedure, against the "
                      "surgeon's advice to stay; form signed by patient and attendant.")])

    # ── S4 · specialty not empanelled — a check on real published data ────
    # R1 needs a hospital whose registry lists specialties, but none the package is listed under: a blank list is
    # missing data (D-4), and a package listed under two specialties is billable through either (F-35)
    r1h = next(h for h in _hospitals(tools, AHMEDABAD, basic_tier=False)
               if h.specialties and not set(cardio.specialties) & set(h.specialties))
    s4 = _claim("CLM-S04", r1h, cardio, "BEN-10004", docs=[_summary("CLM-S04", "BEN-10004", cardio)])

    # ── S5 · phantom capability: reused document at a CHC/PHC sole provider ──
    ro = _package(tools, "radiation_oncology", major=False, lo=5_000)
    phantoms = []
    for h in tools.hospitals():
        if h.basic_tier and "radiation_oncology" in h.specialties and h.state.upper() not in BULK_STATES:
            a = tools.district_adequacy(h, "radiation_oncology")
            if a and a.n_providers == 1:
                phantoms.append((-a.population, h.hospital_ref, h))
    assert phantoms, "need a basic-tier sole provider of a tertiary specialty outside the bulk states"
    ph = sorted(phantoms)[0][2]
    reused = Document(doc_type="discharge_summary",
                      text="Discharge summary. Course of radiotherapy completed as planned. Tolerated well. "
                           "Follow up in oncology OPD after four weeks.")
    support.append(_claim("CLM-S05A", ph, ro, "BEN-20051", admit=T0 - timedelta(days=9), docs=[reused]))
    # Site verification on file: a de-listing referral should rest on it, and a live crew asked for it (F-07).
    s5 = _claim("CLM-S05", ph, ro, "BEN-10005", docs=[reused], field_reports=[
        FieldReport(channel=Channel.HOSPITAL_VISIT, supports_fraud=True, confidence=0.85,
                    summary="The facility is a basic-tier (CHC/PHC) facility with no radiotherapy unit or linear "
                            "accelerator; "
                            "the discharge summary on this claim is identical to one filed nine days earlier for "
                            "another patient."),
        FieldReport(channel=Channel.BENEFICIARY_CALL, supports_fraud=True, confidence=0.80,
                    summary="Beneficiary says radiotherapy was received at a hospital in another district; they "
                            "visited this facility only for a referral slip.")])

    # ── S6 · impossible surgeon, evidence gathered in the field ───────────
    ortho = _package(tools, "orthopaedics", major=True)
    oh = _hospitals(tools, AHMEDABAD, has="orthopaedics", basic_tier=False)
    far = sorted({(tools.district_adequacy(h, "orthopaedics").n_providers, h.district_code)
                  for h in tools.hospitals()
                  if "orthopaedics" in h.specialties and not h.basic_tier and h.district_code != AHMEDABAD
                  and (km := tools.district_km(AHMEDABAD, h.district_code)) is not None and 250 <= km <= 600},
                 reverse=True)
    assert far and far[0][0] >= 10, "need a second well-served orthopaedics district 250-600 km away"
    other_h = _hospitals(tools, far[0][1], has="orthopaedics", basic_tier=False)[0]
    surgeon = "REG-SIM-5501"
    support.append(_claim("CLM-S06A", other_h, ortho, "BEN-20061", surgeon_reg_no=surgeon,
                          docs=[_summary("CLM-S06A", "BEN-20061", ortho)]))
    s6 = _claim("CLM-S06", oh[0], ortho, "BEN-10006", surgeon_reg_no=surgeon,
                docs=[_summary("CLM-S06", "BEN-10006", ortho)],
                field_reports=[
                    FieldReport(channel=Channel.HOSPITAL_VISIT, supports_fraud=True, confidence=0.85,
                                summary="OT register has no entry for this beneficiary on the billed date; the "
                                        "listed surgeon's roster shows him at another facility."),
                    FieldReport(channel=Channel.BENEFICIARY_CALL, supports_fraud=True, confidence=0.80,
                                summary="Beneficiary states the fracture was plastered in OPD and no surgery "
                                        "was performed.")])

    # ── S7 / S8 · repeat acute episodes: order evidence, then weigh it ────
    # Three admissions each, clinically distinct and plausible -- no copied records, which a desk with tools would
    # rightly call fabrication -- but thin: investigations are advised and no report is attached, so the indication for
    # admission cannot be verified from the documents. Whether the admissions happened is the beneficiary's to confirm.
    # These per-day packages have no fixed rate; each claim is the published average General Medicine claim.
    gmh = _hospitals(tools, AHMEDABAD, has="general_medicine", basic_tier=False)[0]
    avg = pd.read_csv(ROOT / "data/reference/specialty_volume.csv", keep_default_na=False)
    gm_rs = int(avg.loc[avg.specialty == "general_medicine", "avg_claim_rs"].iloc[0])

    def episode(cid: str, ben: str, code: str, back: int, notes: str, summary: str, **kw) -> Claim:
        p = _named(tools, code, "general_medicine")
        return _claim(cid, gmh, p, ben, admit=T0 - timedelta(days=back), amount=gm_rs, docs=[
            Document(doc_type="clinical_notes", text=f"Admission notes, claim {cid}, beneficiary {ben}. {notes}"),
            Document(doc_type="discharge_summary", text=f"Discharge summary, claim {cid}, beneficiary {ben}. {summary}")],
                      **kw)

    support += [
        episode("CLM-S07P1", "BEN-10007", "MG001A", 24,
                "Complaint: fever for two days with body ache. On admission: temperature 101 F, pulse 96/min, BP "
                "118/76 mmHg; examination otherwise unremarkable. Plan: IV fluids, antipyretics, observation. Blood "
                "investigations advised; reports not attached.",
                "Admitted with fever and body ache; treated with IV fluids and antipyretics; afebrile by the third day. "
                "Discharged in stable condition; review in OPD after one week."),
        episode("CLM-S07P2", "BEN-10007", "MG009B", 12,
                "Complaint: loose stools and vomiting since the previous night. On admission: pulse 104/min, BP 104/68 "
                "mmHg, dry tongue, sunken eyes. Plan: IV Ringer lactate, antiemetics. Stool examination advised; report "
                "not attached.",
                "Admitted with acute gastroenteritis and dehydration; rehydrated with IV fluids; stools settled on the "
                "second day and oral feeds tolerated. Discharged in stable condition."),
        episode("CLM-S08P1", "BEN-10008", "MG001A", 24,
                "Complaint: fever with chills and headache for three days. On admission: temperature 102 F, pulse "
                "102/min, BP 124/80 mmHg; no neck stiffness. Plan: IV fluids, antipyretics. Malaria and dengue tests "
                "advised; reports not attached.",
                "Admitted with fever, chills and headache; fever subsided on the second day of treatment. Discharged on "
                "oral medication; advised to return if the fever recurs."),
        episode("CLM-S08P2", "BEN-10008", "MG011A", 12,
                "Complaint: frequent small stools with blood and mucus and abdominal cramps for two days. On admission: "
                "temperature 100 F, pulse 98/min, BP 112/72 mmHg, lower abdominal tenderness. Plan: IV fluids, "
                "antibiotics. Stool microscopy advised; report not attached.",
                "Admitted with dysentery; treated with IV fluids and antibiotics; stools normal by the third day. "
                "Discharged on oral antibiotics."),
    ]
    s7 = episode("CLM-S07", "BEN-10007", "MG016A", 0,
                 "Complaint: cough with fever and breathlessness for four days. On admission: temperature 100.4 F, "
                 "respiratory rate 24/min, SpO2 95% on room air, crackles at the right lung base. Plan: IV antibiotics, "
                 "nebulisation. Chest X-ray advised; report not attached.",
                 "Admitted with community-acquired pneumonia; treated with IV antibiotics and nebulisation; breathing "
                 "comfortably and afebrile by the fourth day. Discharged on oral antibiotics.")
    s8 = episode("CLM-S08", "BEN-10008", "MG028A", 0,
                 "Complaint: cough with wheeze and low-grade fever for five days. On admission: temperature 99.8 F, "
                 "respiratory rate 22/min, SpO2 96% on room air, scattered rhonchi. Plan: nebulisation, antibiotics. Chest "
                 "X-ray advised; report not attached.",
                 "Admitted with acute bronchitis; wheeze settled with nebulisation by the third day. Discharged on "
                 "inhalers and oral antibiotics.", field_reports=[
                     FieldReport(channel=Channel.BENEFICIARY_CALL, supports_fraud=True, confidence=0.60,
                                 summary="Beneficiary recalls one admission this month, not three."),
                     FieldReport(channel=Channel.HOSPITAL_VISIT, supports_fraud=False, confidence=0.55,
                                 summary="IPD register shows three admissions with vitals charts and drug administration "
                                         "records on each.")])

    # ── S9 · the crew is unavailable ──────────────────────────────────────
    s9 = after_death("CLM-S09", sole[0], "BEN-10009")

    # ── S10 · malformed and ambiguous ─────────────────────────────────────
    dup = tools.ambiguous_district_names()
    assert dup, "need a district name that resolves to more than one LGD code"
    s10 = Claim(claim_id="CLM-S10", hospital_ref=gsh.hospital_ref, district_code=None, district_name=dup[0].title(),
                package_code=gs.package_code, beneficiary_ref="BEN-10010", admission_ts=T0, discharge_ts=None,
                amount_claimed=gs.amount_rs, submitted_ts=T0 + timedelta(days=2),
                documents=[_summary("CLM-S10", "BEN-10010", gs)])

    scenarios = [
        Scenario("S1", "Service after death - Ahmedabad, 101 cardiology providers", s1, Action.SUSPEND,
                 "The pipeline decides and acts unattended", expected_channels=[Channel.DESK_AUDIT]),
        Scenario("S2", "Identical pattern - Bahraich, sole cardiology provider", s2, Action.ESCALATE_SEC,
                 "The thesis: same fraud, different decision", expected_channels=[Channel.DESK_AUDIT]),
        Scenario("S3", "Zero length of stay explained by a LAMA form", s3, Action.RELEASE_CLAIM,
                 "False positives are released, not left hanging", expected_channels=[Channel.DESK_AUDIT]),
        Scenario("S4", "Cardiology billed by a hospital not empanelled for it", s4, Action.SHOW_CAUSE,
                 "A detection running on real published data"),
        Scenario("S5", "Reused document at a PHC listed as sole RT provider", s5, Action.DELIST_SPECIALTY,
                 "Phantom capability - the gate inverts",
                 expected_channels=[Channel.DESK_AUDIT, Channel.HOSPITAL_VISIT, Channel.BENEFICIARY_CALL]),
        Scenario("S6", "Impossible surgeon, field evidence on file", s6, Action.SUSPEND,
                 "Branching: channels follow the evidence",
                 expected_channels=[Channel.DESK_AUDIT, Channel.HOSPITAL_VISIT, Channel.BENEFICIARY_CALL]),
        Scenario("S7", "Repeat admissions, desk cannot settle", s7, Action.FIELD_AUDIT,
                 "Branching: buys evidence instead of guessing", expected_channels=[Channel.DESK_AUDIT]),
        Scenario("S8", "Repeat admissions, field reports conflict", s8, Action.NO_ACTION,
                 "Low confidence is defined, not accidental"),
        Scenario("S9", "Sole provider case with the crew unavailable", s9, Action.ESCALATE_SEC,
                 "Designed degradation, not a crash", simulate_llm_outage=True),
        Scenario("S10", f"No discharge date; district '{dup[0].title()}' is ambiguous", s10, Action.REFUSE,
                 "Never fabricates to keep the pipeline moving"),
    ]
    store = ClaimStore([s.claim for s in scenarios] + support)
    return scenarios, store
