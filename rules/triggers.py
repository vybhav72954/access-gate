"""Deterministic trigger evaluation (docs/02-LLD.md §4).

T-codes are NHA's published triggers (Anti-Fraud Framework Practitioners'
Guidebook, Annexure 2). R-codes are checks we derive from published data. We
claim no detection novelty: on a simulated corpus these fire by construction.

Each trigger declares the FIELD channels to order when the desk cannot settle
it. The desk audit always runs first; whether field channels are ordered, and
which, depends on what the desk finds -- that is the branching (docs/01-HLD §4.1).
Checklist text is the guidebook's own, extracted into data/reference/triggers.json.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from crew.schemas import Channel, Claim, Document, Hospital, Package, Severity, TriggerHit

ROOT = Path(__file__).resolve().parent.parent

SURGICAL = frozenset({
    "general_surgery", "ent", "ophthalmology", "obgyn", "orthopaedics", "polytrauma", "urology",
    "neurosurgery", "interventional_neuroradiology", "plastic_surgery", "burns", "cardiology", "ctvs",
    "paediatric_surgery", "surgical_oncology", "oral_maxillofacial",
})   # the HBP 1.0 S1-S16 family, which includes S12 Cardiology
ACUTE_MEDICAL = frozenset({"general_medicine", "paediatric_medicine"})
PRIVATE_TYPES = frozenset({"Private(For Profit)", "Private(Not For Profit)"})

IMPOSSIBLE_SURGEON_KM = 200.0     # same surgeon, same day, districts further apart than this
REPEAT_EPISODE_WINDOW = timedelta(days=30)
REPEAT_EPISODE_COUNT = 3
AMOUNT_TOLERANCE = 1.10           # >10% above the published package rate


def _key(v: str | None) -> str | None:
    """The same normalisation the Claim schema applies, for claims built without validation (model_copy)."""
    if v is None:
        return None
    return " ".join(str(v).split()).upper() or None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ClaimStore:
    """The claim corpus, indexed for the cross-claim triggers (T5, T6, T7).

    Keys are normalised and document hashes recomputed from the text, so a claim built without validation cannot
    split one surgeon into two (" reg-1" / "REG-1") or join two documents under a stale hash.
    """

    def __init__(self, claims: Iterable[Claim]):
        self.claims = {c.claim_id: c for c in claims}
        self.by_beneficiary: dict[str, list[Claim]] = defaultdict(list)
        self.by_surgeon: dict[str, list[Claim]] = defaultdict(list)
        self.by_doc_hash: dict[str, list[Claim]] = defaultdict(list)
        for c in self.claims.values():
            if ben := _key(c.beneficiary_ref):
                self.by_beneficiary[ben].append(c)
            if surgeon := _key(c.surgeon_reg_no):
                self.by_surgeon[surgeon].append(c)
            for d in c.documents:
                if d.text.strip():            # blank uploads all share one hash; they are not "reuse"
                    self.by_doc_hash[_digest(d.text)].append(c)

    def same_beneficiary(self, claim: Claim) -> list[Claim]:
        return self.by_beneficiary.get(_key(claim.beneficiary_ref) or "", [])

    def same_surgeon(self, claim: Claim) -> list[Claim]:
        return self.by_surgeon.get(_key(claim.surgeon_reg_no) or "", [])

    def identical(self, claim: Claim) -> list[tuple[Document, Claim]]:
        """(document, other claim) for each non-blank document also filed, byte for byte, on any other claim."""
        return [(d, o) for d in claim.documents if d.text.strip()
                for o in self.by_doc_hash.get(_digest(d.text), []) if o.claim_id != claim.claim_id]

    def reused(self, claim: Claim) -> list[tuple[Document, Claim]]:
        """(document, other claim) for each non-blank document also filed on a claim for another beneficiary."""
        mine = _key(claim.beneficiary_ref)
        return [(d, o) for d, o in self.identical(claim) if _key(o.beneficiary_ref) != mine]


def billed_specialty(package: Package, hospital: Hospital) -> str | None:
    """The specialty a hospital bills a package under: the first of the package's listings the hospital is
    empanelled for, else the package's own. A package listed under General Medicine and Paediatrics, billed by a
    paediatric hospital, is paediatrics (F-35)."""
    return next((s for s in package.specialties if s in hospital.specialties), package.specialty)


def reserved_for(package: Package, hospital: Hospital) -> bool:
    """Whether the package is government-reserved as this hospital can bill it. Reserved only if reserved under
    every listing the hospital is empanelled for -- or, when it is empanelled for none (R1's case), under every
    listing. 52 packages are reserved under one listing and open under another (F-35)."""
    billable = [s for s in package.specialties if s in hospital.specialties] or list(package.specialties)
    if not billable:
        return package.govt_reserved
    return all(package.reserved_under.get(s, package.govt_reserved) for s in billable)


@dataclass(frozen=True)
class Spec:
    trigger_id: str
    name: str
    severity: Severity
    field_channels: tuple[Channel, ...]
    source: str
    desk_can_suspend: bool = False        # a desk finding alone may carry this egregious trigger to suspension


SPECS = {s.trigger_id: s for s in [
    Spec("T2", "Zero length of stay on a major surgical package", Severity.SUBSTANTIVE,
         (Channel.HOSPITAL_VISIT, Channel.BENEFICIARY_CALL), "NHA Anti-Fraud Guidebook, Annexure 2, trigger 2"),
    Spec("T3", "Zero length of stay on a non-day-care medical package", Severity.SUBSTANTIVE,
         (Channel.BENEFICIARY_CALL,), "NHA Anti-Fraud Guidebook, Annexure 2, trigger 3"),
    Spec("T4", "Medical management beyond 10 days in a non-critical case", Severity.ADMINISTRATIVE,
         (Channel.HOSPITAL_VISIT,), "NHA Anti-Fraud Guidebook, Annexure 2, trigger 4"),
    # Egregious triggers end in suspension, so each orders TWO independent field channels: no single
    # field report can suspend a hospital. docs/09-EVALUATION.md measured the one-channel version.
    Spec("T5", "Same surgeon recorded in far-apart districts on the same day", Severity.EGREGIOUS,
         (Channel.HOSPITAL_VISIT, Channel.BENEFICIARY_CALL), "NHA Anti-Fraud Guidebook, Annexure 2, trigger 5"),
    # A reused document suspends only after the field confirms it: at the desk, fraud and a clerical upload error
    # look identical (team decision, 17 September 2026; LLD B-28).
    Spec("T6", "Same document or image used in more than one case", Severity.EGREGIOUS,
         (Channel.HOSPITAL_VISIT, Channel.BENEFICIARY_CALL), "NHA Anti-Fraud Guidebook, Annexure 2, trigger 6"),
    Spec("T7", "Repeated acute medical episodes for the same beneficiary", Severity.SUBSTANTIVE,
         (Channel.BENEFICIARY_CALL,), "NHA Anti-Fraud Guidebook, Annexure 2, trigger 7"),
    # A death certificate dating death before admission is proof in itself, so the desk may carry T10 through.
    Spec("T10", "Admission recorded after the beneficiary's death", Severity.EGREGIOUS,
         (Channel.BENEFICIARY_CALL, Channel.BENEFICIARY_VISIT), "NHA Anti-Fraud Guidebook, Annexure 2, trigger 10",
         desk_can_suspend=True),
    # R1 is SUBSTANTIVE, not egregious: CAG Report 11/2023 §4.5 documents hospitals delivering
    # specialties they were capable of but never applied to be empanelled for.
    Spec("R1", "Package billed for a specialty the hospital is not empanelled for", Severity.SUBSTANTIVE,
         (Channel.HOSPITAL_VISIT,), "Derived: PM-JAY registry; audited pattern, CAG Report 11/2023 §4.6"),
    Spec("R2", "Claimed amount exceeds the published package rate", Severity.SUBSTANTIVE,
         (), "Derived: Punjab SHA published HBP package master"),
    Spec("R3", "Government-reserved package billed by a private hospital", Severity.SUBSTANTIVE,
         (), "Derived: Punjab SHA published HBP package master"),
]}


# The desk-audit column of the guidebook's trigger table (Annexure 2: "Facts to be verified during desk
# audit"), transcribed. The PDF's five-column table flattens unreliably, so the parsed checklists in
# triggers.json mix desk items with hospital-visit and beneficiary questions; an agent shown that flat list
# treated field questions as prerequisites and would not settle cases the documents settle (F-21).
DESK_CHECKS: dict[str, tuple[str, ...]] = {
    "T2": ("Cross match the procedure performed with the day-care procedure list in the package master",
           "Verify the length of stay against the surgical package blocked, per standard guidelines",
           "Verify the mandatory documents for the blocked procedure",
           "Check whether the patient was released on DOR, LAMA or DAMA"),
    "T3": ("Verify the indication for hospitalisation against the supporting documents submitted (OPD or initial "
           "assessment notes, investigation reports, line of treatment, vitals chart)",
           "Check whether the patient was released on DOR, LAMA or DAMA"),
    "T4": ("Cross check the day-wise indoor case papers (repeated lab investigations, progress notes, vitals and "
           "treatment charts) to justify the prolonged stay",
           "Verify whether the line of treatment for the prolonged days was required and in step with the patient's "
           "improvement, vitals and repeated investigation reports",
           "Verify the above against the treating doctor's progress notes and nursing notes for each day"),
    "T5": ("Pull the data on the doctor recorded as performing surgeries in different hospitals on the same day",),
    "T6": ("Verify the set of claims where the same document or image was used (with emphasis on date of "
           "admission, same family, same first name, same member)",),
    "T7": ("Verify the indication for hospitalisation on each admission against the mandatory documents submitted",
           "Cross check similarities in supporting documents across all claims of the same member (OPD "
           "prescription, initial assessment, investigation values, bed numbers in photos, vitals pattern, line of "
           "treatment, progress notes)"),
    "T10": ("Cross match the date of admission with the date of death on the transaction management system",),
    "R1": ("Compare the package specialty against the hospital's registry specialty list",),
    "R2": ("Compare the amount claimed against the published package rate",
           "Check whether an implant or add-on invoice justifies the difference"),
    "R3": ("Check the government-reserved flag, under every listing the hospital could bill through, against the "
           "hospital type",
           "Check whether the package master allows the package on referral (referral_basis) and, only if it does, "
           "whether a referral from a government facility is on file"),
}


# How the desk items above appear in the parsed guidebook text, so they can be removed from the field list.
_PARSED_DESK_ITEMS = ("cross match doa", "cross match the procedure", "verify los", "verify mandatory",
                      "cross check whether patient has been released", "cross check all day wise",
                      "verify the above with", "pull out the data", "verify the claims set",
                      "verify the indication for hospitalization")


def field_checklist(trigger_id: str) -> list[str]:
    """The guidebook's checks that need a person on the ground: the parsed checklist minus the desk items.
    The derived R-checks come from published data, not the guidebook, and have no field checklist."""
    if not trigger_id.startswith("T"):
        return []
    return [c for c in _checklists().get(trigger_id, []) if not c.lower().startswith(_PARSED_DESK_ITEMS)]


@lru_cache(maxsize=1)
def _checklists() -> dict[str, list[str]]:
    path = ROOT / "data" / "reference" / "triggers.json"
    if not path.exists():
        return {}
    out = {}
    for t in json.loads(path.read_text(encoding="utf-8")):
        tid = t["trigger_id"]
        out[f"T{tid}" if isinstance(tid, int) else str(tid)] = t.get("checklist", [])
    return out


def guidance(trigger_id: str) -> dict:
    """The trigger_guidance tool: name, severity, field channels, guidebook checklist."""
    s = SPECS[trigger_id]
    return {"trigger_id": s.trigger_id, "name": s.name, "severity": s.severity.value,
            "field_channels": [c.value for c in s.field_channels],
            "desk_checklist": list(DESK_CHECKS.get(trigger_id, ())), "field_checklist": field_checklist(trigger_id),
            "checklist": _checklists().get(trigger_id, []), "source": s.source}


def died_before_admission(claim: Claim) -> bool:
    """Trigger 10's test, by calendar date -- the guidebook's 'cross match DOA and date of death'.

    A death register records a date. Compared as timestamps, a death registered at midnight preceded a same-day
    admission, so a patient who was admitted and died that day read as billed after death -- and with the
    certificate on file, as a confirmed egregious fraud (F-24). A same-day death is not evidence of anything.
    """
    return (claim.beneficiary_death_ts is not None
            and claim.admission_ts.date() > claim.beneficiary_death_ts.date())


def _hit(tid: str, evidence: str) -> TriggerHit:
    s = SPECS[tid]
    return TriggerHit(trigger_id=tid, name=s.name, severity=s.severity, evidence=evidence,
                      field_channels=list(s.field_channels), checklist=_checklists().get(tid, []),
                      source=s.source, desk_can_suspend=s.desk_can_suspend)


def evaluate(claim: Claim, hospital: Hospital, package: Package, store: ClaimStore, tools) -> list[TriggerHit]:
    """`tools` supplies registry_lookup, hbp_lookup and district_km (crew.tools.Tools)."""
    hits: list[TriggerHit] = []
    los, spec = claim.los_days, billed_specialty(package, hospital)

    if los == 0 and spec in SURGICAL and package.is_major:
        hits.append(_hit("T2", f"LOS 0 on {package.package_code} ({package.package_name}, Rs {package.amount_rs:,})"))
    if los == 0 and spec in ACUTE_MEDICAL and not package.daycare_candidate:
        hits.append(_hit("T3", f"LOS 0 on non-day-care medical package {package.package_code}"))
    if los is not None and los > 10 and spec in ACUTE_MEDICAL and not claim.icu_flag:
        hits.append(_hit("T4", f"LOS {los} days on {package.package_code} with no ICU flag"))

    for other in store.same_surgeon(claim):
        if other.claim_id == claim.claim_id or other.admission_ts.date() != claim.admission_ts.date():
            continue
        oh = tools.registry_lookup(other.hospital_ref)
        if oh is None or oh.district_code == hospital.district_code:
            continue
        km = tools.district_km(hospital.district_code, oh.district_code)
        if km is not None and km > IMPOSSIBLE_SURGEON_KM:   # unknown distance never fires
            hits.append(_hit("T5", f"surgeon {_key(claim.surgeon_reg_no)} also billed {other.claim_id} on "
                                   f"{claim.admission_ts.date()} in a district {km:.0f} km away"))
            break

    reused = store.reused(claim)
    if reused:
        doc, other = reused[0]
        hits.append(_hit("T6", f"{doc.doc_type} (sha256 {_digest(doc.text)[:12]}...) also filed on {other.claim_id} "
                               f"for a different beneficiary"))

    if spec in ACUTE_MEDICAL:
        # The admissions in the 30 days up to and including this one. A window either side counted admissions
        # up to 60 days apart as "within 30 days", and admissions that had not happened when this claim arrived.
        acute = {claim.claim_id}
        for other in store.same_beneficiary(claim):
            if not timedelta(0) <= claim.admission_ts - other.admission_ts <= REPEAT_EPISODE_WINDOW:
                continue
            op = tools.hbp_lookup(other.package_code)
            if op is not None and set(op.specialties) & ACUTE_MEDICAL:
                acute.add(other.claim_id)
        if len(acute) >= REPEAT_EPISODE_COUNT:
            hits.append(_hit("T7", f"{len(acute)} acute medical admissions for {_key(claim.beneficiary_ref)} "
                                   f"in the {REPEAT_EPISODE_WINDOW.days} days to this admission"))

    if died_before_admission(claim):
        days = (claim.admission_ts.date() - claim.beneficiary_death_ts.date()).days
        hits.append(_hit("T10", f"admission {days} days after the beneficiary's registered death"))

    # A blank specialty list is missing data, not evidence (data defect D-4: 30% of hospitals list nothing). And a
    # package listed under several specialties is billable through any of them (F-35).
    if package.specialties and hospital.specialties and not set(package.specialties) & set(hospital.specialties):
        hits.append(_hit("R1", f"{package.package_code} is listed under {', '.join(package.specialties)}; hospital "
                               f"is empanelled for {', '.join(hospital.specialties)}"))
    if package.amount_rs > 0 and claim.amount_claimed > package.amount_rs * AMOUNT_TOLERANCE:
        hits.append(_hit("R2", f"claimed Rs {claim.amount_claimed:,} against published rate Rs {package.amount_rs:,}"))
    # Only a hospital KNOWN to be private: 71 registry rows have no hospital type at all.
    if hospital.hospital_type in PRIVATE_TYPES and reserved_for(package, hospital):
        hits.append(_hit("R3", f"{package.package_code} is reserved for government hospitals under every listing "
                               f"this hospital could bill it through; billed by {hospital.hospital_type}"))
    return hits


def primary(hits: list[TriggerHit]) -> TriggerHit | None:
    """The hit that governs the decision: highest severity, then catalogue order."""
    if not hits:
        return None
    order = list(SPECS)
    return max(hits, key=lambda h: (h.severity.value, -order.index(h.trigger_id)))
