"""The agent benchmark: 40 cases whose truth sits in free text or across claims (docs/10-AGENT-EVALUATION.md).

The 5,000-case evaluation (metrics/run.py) measures the policy and the access gate on the rules path, where every
desk reading is a pattern. It cannot say whether an agent reads documents better or worse than those patterns, because
its documents are neutral by design. These cases can: each is a hand-written claim whose truth -- fraud or innocent --
is stated in the documents, in one of six ways:

* keyword       the documents use the words the rules look for, and mean them (the rules should be right)
* paraphrase    the truth is stated in words the patterns do not match
* trap          the patterns match words that mean something else ("complication-free", "sepsis screen negative")
* mislabelled   the evidence is on file under another document type than the rules look for
* contradiction one document asserts what another contradicts (hard for a reader of prose, and for the patterns)
* cross-claim   the evidence is in the beneficiary's other admissions (trigger 7): copied records, or distinct episodes

Every claim carries the evidence its package's master entry lists as mandatory, so no case turns on a missing upload
unless the missing evidence is the truth itself (an operation abandoned before incision has no histopathology). A live
crew refused to clear a scenario lacking two of its package's mandatory documents (F-55); here only the case's own
question is open.

They are written by the team that built the crew, which is a bias to disclose, not hide: the keyword cases are there
so the rules have cases they should win, and the contradiction cases are there to catch an agent that believes
well-written notes. Every case fires exactly its intended trigger (tests/test_benchmark.py). No trigger here is
egregious, so no case reaches the access gate: the benchmark isolates the desk reading.

Claims, documents and conduct are simulated. Hospitals and packages are real rows of the published reference data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from crew.schemas import Claim, Document, Hospital, Package
from crew.tools import REF, Tools
from rules.triggers import ClaimStore

T0 = datetime(2026, 7, 6, 9, 30)
AHMEDABAD = 438
READINGS = ("keyword", "paraphrase", "trap", "mislabelled", "contradiction", "cross-claim")

# Changes made after the first live run (17 September), each with its reason. The report prints them beside both runs'
# results: a case, a prompt or a rule changed after a result is a result that needs disclosing. None of them tells the
# investigator how to read a benchmark case; the two genuine reading errors of the first run were left alone.
CORRECTIONS = (
    "Crew: rebuilt from two agents to six after the first run, at the team's decision. The Desk Investigator now reads "
    "the documents' integrity; a Medical Auditor judges clinical need on T2, T3, T4 and T7, without seeing the desk's "
    "finding; a Field Evidence Analyst weighs field reports; an Audit Reviewer checks the readings and may dispute one, "
    "which then weighs nothing; a Committee Liaison briefs the State Empanelment Committee on a referral; the "
    "Enforcement Officer is unchanged. No benchmark case has field reports or reaches the access gate. The first run "
    "is the two-agent crew's.",
    "Cases: the innocent same-day thyroidectomies (BCH-T2-01 to 03) filed a final histopathology report on a claim "
    "submitted five hours after discharge. The crew called that implausible on BCH-T2-02, rightly, because tissue takes "
    "days to report. Reports are now dated five days after surgery, and every claim is submitted after its reports "
    "(T2 claims a week after discharge, the rest two days; no case brief shows the submission date).",
    "Instruction: the investigator had been told that a missing mandatory document is not by itself evidence of fraud. "
    "On BCH-R3-06 it applied that to the missing referral, the very document the trigger is about, and released the "
    "claim. The instruction now says that when the missing document is the one that would explain the trigger, the "
    "trigger stands unexplained. This was a defect in an instruction added hours before the run (F-56), not a lesson "
    "drawn from the benchmark's other cases.",
    "Rule (F-58, both paths): a clearance of R2 now needs an invoice or bill on file, and of R3 a referral. On "
    "BCH-R2-07 the crew released an above-rate claim with no invoice at all. The rules path always met this rule, "
    "because it clears only on those documents.",
    "Rule (B-44a, F-61, after the 18 September run): a dispute may set aside a reading, never a measurement. Where "
    "the claim store itself finds a document byte-identical to another claim's, a dispute of a reading that supports "
    "fraud is refused and recorded; the reading keeps its weight. On BCH-T7-03 the reviewer disputed the desk's "
    "byte-identical-notes finding, which removed the only dissent and so raised the surviving reading's confidence "
    "from 0.46 to 0.85 and released a fraud. The reviewer's instructions changed with it: they no longer promise what "
    "a dispute does, and name a comparison the store makes itself as not theirs to dispute. This is a change to the "
    "decision path and to an agent's instructions, not to any case, and it perturbs every case's prompt.",
    "Tool: compare_documents measures similarity over words, not characters, because a character comparison took five "
    "seconds a ratio on long documents. Only the T7 cases compare documents.",
    "Trail: every tool call now runs and is recorded, including a call whose arguments do not fit the tool. On "
    "BCH-R2-04 the officer made repeated calls that left no trace, and the orchestrator executed the decision. This "
    "changes what the trail shows, not what any tool does.",
)


@dataclass
class BenchCase:
    case_id: str
    trigger: str
    fraud: bool                 # the truth the documents establish
    reading: str                # how the truth is written (READINGS)
    note: str                   # the truth in one line, for the report
    claim: Claim
    related: list[Claim] = field(default_factory=list)


def _hospital(tools: Tools, specialty: str, kind: str = "Private(For Profit)", index: int = 0) -> Hospital:
    hs = sorted((h for h in tools.hospitals() if h.district_code == AHMEDABAD and specialty in h.specialties
                 and h.hospital_type == kind and not h.basic_tier), key=lambda h: h.hospital_ref)
    assert len(hs) > index, f"need {index + 1} {kind} {specialty} hospitals in Ahmedabad"
    return hs[index]


def _package(tools: Tools, code: str) -> Package:
    p = tools.hbp_lookup(code)
    assert p is not None, f"{code} is not in the package master"
    return p


def _doc(doc_type: str, text: str) -> Document:
    return Document(doc_type=doc_type, text=" ".join(text.split()))


class _Builder:
    def __init__(self, tools: Tools):
        self.tools, self.cases = tools, []
        # Per-day packages have no fixed rate: a claim is the published average claim for the specialty.
        vol = pd.read_csv(REF / "specialty_volume.csv", keep_default_na=False)
        self.avg_rs = dict(zip(vol.specialty, vol.avg_claim_rs.astype(float)))

    def claim(self, cid: str, h: Hospital, p: Package, ben: str, docs: list[Document], *, los: int, admit: datetime = T0,
              amount: int | None = None, submitted_after: timedelta = timedelta(days=2), **kw) -> Claim:
        rate = p.amount_rs if p.amount_rs > 0 else int(self.avg_rs.get(p.specialty, 10_000))
        discharge = admit + timedelta(days=los, hours=7 if los else 9)
        # Submitted after the reports a claim files have come back: a live crew rightly found a final histopathology
        # report on a claim submitted five hours after a same-day discharge implausible (docs/10, corrections).
        return Claim(claim_id=cid, hospital_ref=h.hospital_ref, district_code=h.district_code,
                     package_code=p.package_code, beneficiary_ref=ben, admission_ts=admit, discharge_ts=discharge,
                     amount_claimed=rate if amount is None else amount, documents=docs,
                     submitted_ts=discharge + submitted_after, **kw)

    def add(self, case_id, trigger, fraud, reading, note, claim, related=()):
        assert reading in READINGS
        self.cases.append(BenchCase(case_id, trigger, fraud, reading, note, claim, list(related)))


def build_benchmark(tools: Tools) -> tuple[list[BenchCase], ClaimStore]:
    b = _Builder(tools)
    _long_stays(b)
    _surgical_same_day(b)
    _medical_same_day(b)
    _repeat_admissions(b)
    _above_rate(b)
    _reserved_packages(b)
    claims = [c.claim for c in b.cases] + [r for c in b.cases for r in c.related]
    return b.cases, ClaimStore(claims)


# ── T4 · medical management beyond ten days, no ICU flag ─────────────────────
# Mandatory (general medicine per-day packages): admission notes with vitals and examination, indoor case papers and
# treatment details, all investigation reports, a detailed discharge summary.

def _long_stays(b: _Builder) -> None:
    h = _hospital(b.tools, "general_medicine")

    def case(n, code, fraud, reading, note, admission, progress, investigations, summary, extra=()):
        cid, ben = f"BCH-T4-{n:02d}", f"BEN-B4{n:02d}"
        docs = [_doc("clinical_notes", f"Admission notes, claim {cid}. {admission}"),
                _doc("progress_notes", f"Indoor case papers and treatment chart, claim {cid}. {progress}"),
                _doc("investigation_report", f"Investigation reports, claim {cid}. {investigations}"),
                *extra, _doc("discharge_summary", f"Discharge summary, claim {cid}, beneficiary {ben}. {summary}")]
        b.add(cid, "T4", fraud, reading, note, b.claim(cid, h, _package(b.tools, code), ben, docs, los=12))

    case(1, "MG016A", False, "paraphrase", "hypoxia needing escalating oxygen and a change of antibiotic",
         "Fever, cough and breathlessness for five days. SpO2 88% on room air, respiratory rate 30/min, crackles over "
         "the right lower zone.",
         "Day 1-3: IV ceftriaxone, oxygen by mask at 4 L/min. Day 4: breathless on minimal exertion; oxygen need rose to "
         "6 L/min; antibiotic switched to meropenem on the culture report. Day 7: breathing still laboured. Day 9: "
         "weaned to 2 L/min. Day 11: SpO2 95% on room air, afebrile for 48 hours.",
         "Chest X-ray day 1: right lower lobe consolidation. Sputum culture day 4: Klebsiella pneumoniae resistant to "
         "ceftriaxone. Chest X-ray day 7: new shadowing in the left lower lobe. Total leucocyte count 18,400 on day 1, "
         "9,200 on day 11.",
         "Community-acquired pneumonia with a resistant organism; oxygen dependence until day 10. Discharged on day 12 "
         "on oral antibiotics.")
    case(2, "MG004A", False, "paraphrase", "platelets fell to 18,000 and needed transfusion",
         "Fever for four days with body ache and rash. Pulse 104/min, BP 108/70 mmHg.",
         "Day 3: IV fluids. Day 5: bleeding gums; two units of platelets transfused. Day 6: gum bleeding stopped. "
         "Day 8: taking oral fluids well. Day 11: tolerating a normal diet.",
         "NS1 antigen positive. Platelet counts per cu mm: day 1 90,000; day 3 42,000; day 5 18,000; day 6 31,000; "
         "day 8 64,000; day 11 1,10,000.",
         "Dengue fever with severe thrombocytopenia requiring platelet transfusion. Discharged on day 12 after two stable "
         "counts.")
    case(3, "MG045A", False, "paraphrase", "kidney function worsening to dialysis",
         "Vomiting and reduced urine output for three days. Pulse 96/min, BP 150/90 mmHg, puffiness of the face.",
         "Day 2: urine output under 400 ml in 24 hours. Day 4: nephrology review advised haemodialysis. Day 4, 6 and 8: "
         "haemodialysis sessions. Day 10: urine output 1.8 litres a day. Day 12: fit for discharge.",
         "Serum creatinine (mg/dL): day 1 2.1; day 4 4.6; day 10 2.4; day 12 1.6. Serum potassium (mmol/L): day 1 5.6; "
         "day 4 6.2; day 12 4.4.",
         "Acute kidney injury needing three sessions of haemodialysis. Discharged on day 12 with nephrology follow-up.")
    case(4, "MG049C", False, "paraphrase", "a stroke patient who could not swallow safely",
         "Sudden weakness of the right arm and leg with slurred speech. BP 170/100 mmHg.",
         "Day 2: failed bedside swallow assessment; nasogastric feeding started. Day 5: chest physiotherapy for "
         "aspiration risk. Day 8: repeat swallow assessment still unsafe for thin liquids. Day 10: managing soft diet "
         "under supervision; nasogastric tube removed. Day 11: walking with support.",
         "CT brain day 1: acute left capsular infarct. Carotid Doppler: 40% stenosis on the left. Lipid profile: LDL "
         "162 mg/dL.",
         "Acute ischaemic stroke with dysphagia; tube-fed until day 10. Discharged on day 12 to home physiotherapy.")
    case(5, "MG006A", False, "keyword", "sepsis on day 7",
         "Fever for ten days with abdominal pain. Pulse 110/min, BP 112/70 mmHg.",
         "Day 4: IV ceftriaxone continued. Day 7: developed sepsis with falling blood pressure 84/50; antibiotics "
         "escalated and fluids given. Day 9: blood pressure stable. Day 11: afebrile.",
         "Blood culture day 1: Salmonella typhi. Lactate day 7: 4.1 mmol/L. Total leucocyte count day 7: 17,900.",
         "Enteric fever complicated by sepsis. Discharged on day 12.")
    case(6, "MG001A", True, "trap", "a complication-free stay kept on for the family's convenience",
         "Fever for two days. Temperature 100.2 F, pulse 88/min, BP 122/78 mmHg.",
         "Day 2: afebrile. Complication-free course. Day 3 to 11: vitals normal; patient walking in the ward and eating "
         "well; IV fluids continued as the family preferred to take her home after the festival.",
         "Complete blood count, malaria antigen and dengue NS1 on day 1: all within normal limits or negative.",
         "Acute febrile illness. Discharged on day 12 at the family's convenience.")
    case(7, "MG016A", True, "trap", "sepsis was screened for and excluded; the stay was not needed",
         "Cough and low-grade fever for three days. SpO2 97% on room air.",
         "Day 1: sepsis screen negative; chest clear on repeat examination. Day 3 onwards: comfortable, ambulant, eating "
         "well, no oxygen. Day 5 to 11: discharge deferred at the relatives' request.",
         "Chest X-ray day 1: no consolidation. Total leucocyte count 8,600. C-reactive protein 6 mg/L.",
         "Lower respiratory tract infection, settled by day 3. Discharged on day 12.")
    case(8, "MG001A", True, "keyword", "a plainly padded stay",
         "Fever for one day. Temperature 99.8 F. Vitals stable.",
         "Stable throughout, no complications. Ambulant from day 2. Day 3 to 11: awaiting the family to arrange "
         "transport home.",
         "Complete blood count day 1: normal. Malaria antigen: negative.",
         "Acute febrile illness. Discharged on day 12.")
    vitals = _doc("vitals_chart", "Vitals chart, claim BCH-T4-09, days 1 to 12: temperature 98.4-98.8 F, pulse 76-84/min, "
                                  "respiratory rate 16/min, BP 118-126/76-80 mmHg, SpO2 98-99% on room air on every "
                                  "recording; no oxygen administered.")
    case(9, "MG016A", True, "contradiction", "notes call the patient critical; the vitals chart is normal throughout",
         "Cough for two days. Patient anxious.",
         "Day 3: patient critically breathless, needs close monitoring. Day 6: remains critical. Day 10: improving.",
         "Chest X-ray day 1: clear lung fields. Total leucocyte count 7,900.",
         "Pneumonia, critical course. Discharged on day 12.", extra=[vitals])
    case(10, "MG001A", True, "paraphrase", "weakness without any finding, kept for 'strengthening'",
         "Weakness and tiredness for a week. Temperature 98.6 F, pulse 80/min, BP 120/80 mmHg. Examination normal.",
         "Day 2: still feels weak; advised continued stay for strengthening. Day 5: tonics continued. Day 8: mobilising "
         "slowly. Day 11: family ready to take the patient home.",
         "Haemoglobin 12.9 g/dL; blood sugar, thyroid function and electrolytes normal.",
         "General weakness. Discharged on day 12.")


# ── T2 · zero length of stay on a major surgical package ─────────────────────
# Total thyroidectomy, mandatory: clinical notes, thyroid function tests and FNAC/USG; histopathology, a post-procedure
# clinical photograph, detailed operative notes and a detailed discharge summary.

def _surgical_same_day(b: _Builder) -> None:
    h = _hospital(b.tools, "general_surgery")
    p = _package(b.tools, "SG070B")

    def case(n, fraud, reading, note, operative, summary, performed=True, extra=()):
        cid, ben = f"BCH-T2-{n:02d}", f"BEN-B2{n:02d}"
        docs = [_doc("clinical_notes", f"Pre-operative notes, claim {cid}. Multinodular goitre with pressure symptoms; "
                                       f"total thyroidectomy planned."),
                _doc("investigation_report", f"Claim {cid}: thyroid function tests within normal limits; FNAC of the "
                                             f"dominant nodule benign (Bethesda II)."),
                _doc("radiology_report", f"Claim {cid}: USG neck shows a multinodular goitre, largest nodule 3.2 cm."),
                _doc("operative_note", f"Operative note, claim {cid}. {operative}")]
        if performed:
            reported = T0 + timedelta(days=5)
            docs += [_doc("histopathology", f"Histopathology, claim {cid}, specimen received {T0:%d %b %Y}, reported "
                                            f"{reported:%d %b %Y}: multinodular goitre, no malignancy."),
                     _doc("clinical_photograph", f"Claim {cid}: post-procedure photograph of the neck wound with the "
                                                 f"patient's ID and date.")]
        docs += [*extra, _doc("discharge_summary", f"Discharge summary, claim {cid}, beneficiary {ben}. {summary}")]
        b.add(cid, "T2", fraud, reading, note, b.claim(cid, h, p, ben, docs, los=0, submitted_after=timedelta(days=7)))

    done = ("Total thyroidectomy under general anaesthesia; both recurrent laryngeal nerves identified and preserved; "
            "haemostasis secured; drain placed. Specimen sent for histopathology.")
    case(1, False, "paraphrase", "the patient insisted on going home the same evening",
         done, "Recovered well from anaesthesia. In the evening the patient and her husband insisted on going home to "
               "care for a sick child, despite advice to stay 48 hours for calcium monitoring; they signed an undertaking "
               "accepting the risk.")
    case(2, False, "keyword", "discharged against medical advice (DAMA)",
         done, "Stable after surgery. Discharged against medical advice (DAMA) at 9 pm at the family's insistence; form "
               "signed.")
    case(3, False, "paraphrase", "the patient left the ward without informing staff",
         done, "Post-operative course uneventful until 7 pm, when the patient left the ward without informing the "
               "nursing staff; the attendant telephoned later to say they had reached home.")
    case(4, True, "paraphrase", "the operation was abandoned on the table, but billed",
         "Patient shifted to the operation theatre; on induction BP 210/120 mmHg; the anaesthetist declared her unfit "
         "and the surgery was abandoned before incision.",
         "Sent home the same day on antihypertensives, advised to return once blood pressure is controlled.",
         performed=False)
    case(5, True, "trap", "discharged on request, but nothing was operated",
         "Procedure could not be carried out as the patient had not fasted; rescheduled.",
         "Discharged on request (DOR) the same evening.", performed=False)
    case(6, True, "contradiction", "an operative note, but histopathology received no specimen and the patient was an "
                                   "outpatient",
         done, "Seen in the surgical OPD; advised surgery.", performed=False,
         extra=[_doc("histopathology", "Histopathology request for claim BCH-T2-06: no specimen received.")])


# ── T3 · zero length of stay on an inpatient medical package ─────────────────

def _medical_same_day(b: _Builder) -> None:
    h = _hospital(b.tools, "general_medicine", index=1)

    def case(n, code, fraud, reading, note, notes, summary, papers=None, investigations=None):
        cid, ben = f"BCH-T3-{n:02d}", f"BEN-B3{n:02d}"
        docs = [_doc("clinical_notes", f"Casualty and admission notes, claim {cid}. {notes}")]
        if papers:
            docs.append(_doc("progress_notes", f"Indoor case papers and treatment chart, claim {cid}. {papers}"))
        if investigations:
            docs.append(_doc("investigation_report", f"Investigation reports, claim {cid}. {investigations}"))
        docs.append(_doc("discharge_summary", f"Discharge summary, claim {cid}, beneficiary {ben}. {summary}"))
        b.add(cid, "T3", fraud, reading, note, b.claim(cid, h, _package(b.tools, code), ben, docs, los=0))

    case(1, "MG009B", False, "paraphrase", "relatives took the patient home against the doctors' advice",
         "Admitted at 10 am with severe dehydration: pulse 124/min, BP 86/56 mmHg, sunken eyes.",
         "By 6 pm the relatives took the patient home against the doctors' advice to complete 24 hours of observation; "
         "refusal form signed.",
         papers="10 am: Ringer lactate 1 litre over one hour. 11 am to 4 pm: two further litres; ondansetron. 5 pm: "
                "pulse 92/min, BP 110/70 mmHg, passing urine.",
         investigations="Serum sodium 131 mmol/L, potassium 3.2 mmol/L, blood urea 58 mg/dL.")
    case(2, "MG017A", False, "paraphrase", "the patient died the day of admission",
         "Admitted at 11 am with severe pneumonia: SpO2 78%, respiratory rate 40/min.",
         "Condition worsened despite treatment; cardiac arrest at 4 pm; resuscitation unsuccessful. Declared dead at "
         "4.40 pm.",
         papers="11 am: oxygen by non-rebreather mask; IV piperacillin-tazobactam. 2 pm: SpO2 82%, BP 88/54; "
                "noradrenaline started. 4 pm: cardiac arrest; CPR for 40 minutes.",
         investigations="Chest X-ray: bilateral consolidation. Arterial blood gas: pH 7.18, pO2 48 mmHg, lactate 6.2.")
    case(3, "MG016A", True, "paraphrase", "an emergency-department visit billed as an admission",
         "Seen in the emergency department with cough for two days; chest clear; SpO2 98%.",
         "Prescribed oral antibiotics and sent home within two hours.")
    case(4, "MG016A", True, "trap", "a casualty visit sent home 'on request'",
         "Cough and mild fever. Chest clear, SpO2 97%. Observed in casualty for three hours; one injection given.",
         "Discharge on request: sent home from casualty the same afternoon; not an inpatient admission.")
    case(5, "MG009B", False, "keyword", "left against medical advice (LAMA)",
         "Admitted at 9 am with severe dehydration; pulse 118/min.",
         "Patient left against medical advice (LAMA) at 5 pm; LAMA form signed by the patient and attendant.",
         papers="9 am to 4 pm: three litres of IV fluids; ondansetron; urine output established by noon.",
         investigations="Serum sodium 133 mmol/L, potassium 3.4 mmol/L; stool microscopy: no ova or cysts.")


# ── T7 · repeat acute admissions: evidence across the member's claims ────────

def _repeat_admissions(b: _Builder) -> None:
    h = _hospital(b.tools, "general_medicine", index=2)

    def admission(cid, ben, code, back, notes, summary, extra=()):
        docs = [_doc("clinical_notes", f"Admission notes, claim {cid}, beneficiary {ben}. {notes}"), *extra,
                _doc("discharge_summary", f"Discharge summary, claim {cid}, beneficiary {ben}. {summary}")]
        return b.claim(cid, h, _package(b.tools, code), ben, docs, los=3, admit=T0 - timedelta(days=back))

    def case(n, fraud, note, episodes):
        cid, ben = f"BCH-T7-{n:02d}", f"BEN-B7{n:02d}"
        claims = [admission(f"{cid}{tag}", ben, *e) for tag, e in zip(("P1", "P2", ""), episodes)]
        b.add(cid, "T7", fraud, "cross-claim", note, claims[-1], claims[:-1])

    copied = ("Complaint: fever with body ache for three days. On examination: temperature 101.2 F, pulse 98/min, BP "
              "120/80 mmHg, SpO2 97% on room air, bed no. 14. Plan: IV fluids, antipyretics, observation.")
    copied_papers = _doc("progress_notes", "Indoor case papers. Day 1: IV fluids, paracetamol; temperature 101.2 F. Day 2: "
                                           "temperature 99.4 F, eating well. Day 3: afebrile, fit for discharge.")
    copied_labs = _doc("lab_report", "Haemoglobin 11.8 g/dL; total count 8,400; platelets 2,40,000; malaria antigen "
                                     "negative.")
    case(1, True, "the same admission notes, vitals, bed, case papers and blood counts, filed three times", [
        ("MG001A", 26, copied, "Acute febrile illness; afebrile by day 3; discharged in stable condition.",
         [copied_papers, copied_labs]),
        ("MG001A", 13, copied, "Acute febrile illness; afebrile by day 3; discharged in stable condition.",
         [copied_papers, copied_labs]),
        ("MG001A", 0, copied, "Acute febrile illness; afebrile by day 3; discharged in stable condition.",
         [copied_papers, copied_labs])])
    template = ("On examination: temperature 100.8 F, pulse 96/min, BP 118/78 mmHg, SpO2 97% on room air, bed no. 9. "
                "Plan: IV fluids, antibiotics, observation.")
    labs = _doc("lab_report", "Haemoglobin 11.2 g/dL, total count 9,800, platelets 2,10,000, serum creatinine 0.9 mg/dL.")
    papers = _doc("progress_notes", "Indoor case papers. Day 1: IV fluids, IV antibiotics; temperature 100.8 F. Day 2: "
                                    "improving. Day 3: afebrile; discharged.")
    case(2, True, "identical vitals, case papers and blood counts under three different diagnoses", [
        ("MG001A", 27, "Complaint: viral fever. " + template, "Viral fever; recovered; discharged.", [papers, labs]),
        ("MG006A", 14, "Complaint: enteric fever. " + template, "Enteric fever; recovered; discharged.", [papers, labs]),
        ("MG016A", 0, "Complaint: chest infection. " + template, "Chest infection; recovered; discharged.",
         [papers, labs])])
    progress = _doc("progress_notes", "Indoor case papers. Day 1: IV fluids, paracetamol; temperature 100.6 F, pulse "
                                      "94/min. Day 2: temperature 99.2 F, pulse 88/min, eating well. Day 3: afebrile, "
                                      "vitals stable, fit for discharge.")
    case(3, True, "distinct admission notes over identical day-by-day case papers", [
        ("MG001A", 25, "Fever and headache for two days; temperature 101 F.",
         "Acute febrile illness, treated and discharged.",
         [progress, _doc("lab_report", "Malaria antigen and dengue NS1 negative; total count 7,200.")]),
        ("MG009B", 12, "Loose stools six times since morning; mild dehydration.",
         "Acute gastroenteritis, rehydrated and discharged.",
         [progress, _doc("lab_report", "Serum sodium 134 mmol/L; stool microscopy: no ova or cysts.")]),
        ("MG016A", 0, "Cough with fever for three days; crackles at the left base.",
         "Lower respiratory tract infection, treated and discharged.",
         [progress, _doc("radiology_report", "Chest X-ray: patchy opacity in the left lower zone.")])])

    def own(text):
        return _doc("progress_notes", f"Indoor case papers. {text}")

    case(4, False, "three different illnesses, each with its own investigation findings", [
        ("MG003A", 24, "Fever with chills every other day for a week; temperature 103 F; spleen palpable.",
         "Vivax malaria treated with chloroquine and primaquine; afebrile from day 2.",
         [own("Day 1: chloroquine started; temperature 103 F with rigors. Day 2: afebrile. Day 3: primaquine; "
              "discharged."),
          _doc("lab_report", "Peripheral smear: Plasmodium vivax trophozoites seen. Platelets 1,10,000/cu mm.")]),
        ("MG011A", 12, "Blood and mucus in stools with cramps for two days; temperature 100.4 F.",
         "Bacillary dysentery treated with ceftriaxone; stools normal by day 3.",
         [own("Day 1: IV ceftriaxone, IV fluids; eight stools with blood. Day 2: three stools, no blood. Day 3: formed "
              "stool; discharged."),
          _doc("lab_report", "Stool microscopy: plenty of pus cells and red cells. Stool culture: Shigella flexneri.")]),
        ("MG016A", 0, "Cough with rusty sputum and breathlessness for four days; SpO2 92%; crackles at the right base.",
         "Right lower lobe pneumonia treated with IV antibiotics; SpO2 97% on room air by day 3.",
         [own("Day 1: oxygen 2 L/min, IV amoxicillin-clavulanate. Day 2: SpO2 95% on room air. Day 3: afebrile; "
              "discharged on oral antibiotics."),
          _doc("radiology_report", "Chest X-ray: consolidation of the right lower lobe. Total leucocyte count 16,800.")])])
    case(5, False, "COPD exacerbations with a documented trigger and blood gases each time", [
        ("MG029A", 27, "Known COPD for eight years. Breathlessness after a cold for three days; wheeze; SpO2 86%.",
         "Exacerbation of COPD after a viral infection; nebulised and steroids; discharged on day 3.",
         [own("Day 1: nebulised salbutamol and ipratropium six-hourly; IV hydrocortisone. Day 2: SpO2 90%. Day 3: SpO2 "
              "93%, walking to the washroom."),
          _doc("lab_report", "Arterial blood gas on admission: pH 7.31, pCO2 58 mmHg, pO2 54 mmHg.")]),
        ("MG029A", 15, "Known COPD. Ran out of inhalers ten days ago; breathlessness for two days; SpO2 88%.",
         "Exacerbation of COPD after stopping inhalers; inhaler technique retaught; discharged on day 3.",
         [own("Day 1: nebulisation four-hourly; oral prednisolone. Day 2: inhalers restarted, technique checked. Day 3: "
              "SpO2 92% on room air."),
          _doc("lab_report", "Arterial blood gas on admission: pH 7.34, pCO2 52 mmHg, pO2 58 mmHg.")]),
        ("MG029A", 0, "Known COPD. Fever and purulent sputum for four days; SpO2 85%.",
         "Infective exacerbation of COPD; antibiotics and nebulisation; pulmonology follow-up arranged.",
         [own("Day 1: controlled oxygen 1 L/min, IV ceftriaxone, nebulisation. Day 2: fever settled. Day 3: SpO2 91% "
              "on room air; sputum clearer."),
          _doc("lab_report", "Arterial blood gas on admission: pH 7.29, pCO2 61 mmHg, pO2 51 mmHg. Sputum culture: "
                             "Haemophilus influenzae.")])])
    case(6, False, "sickle-cell crises with falling haemoglobin and transfusions", [
        ("MG065A", 28, "Known sickle cell disease. Severe pain in the back and legs for a day after a fever.",
         "Vaso-occlusive crisis; IV fluids and analgesia; discharged on day 3.",
         [own("Day 1: IV fluids, IV morphine, pain score 9 of 10. Day 2: oral analgesia, pain 5 of 10. Day 3: pain 2 of "
              "10; walking."),
          _doc("lab_report", "Haemoglobin 7.8 g/dL; reticulocytes 9%.")]),
        ("MG065A", 16, "Known sickle cell disease. Chest pain and cough; SpO2 91%.",
         "Acute chest syndrome; oxygen, antibiotics and one unit of blood; discharged on day 3.",
         [own("Day 1: oxygen 3 L/min, IV azithromycin and ceftriaxone; one unit of packed red cells. Day 2: SpO2 95%. "
              "Day 3: off oxygen."),
          _doc("lab_report", "Haemoglobin 6.4 g/dL; chest X-ray: new left lower zone shadow.")]),
        ("MG065A", 0, "Known sickle cell disease. Pain in both hips for two days; unable to walk.",
         "Vaso-occlusive crisis; analgesia and hydration; haematology follow-up for hydroxyurea.",
         [own("Day 1: IV fluids and IV analgesia. Day 2: pain easing; physiotherapy. Day 3: walking with support."),
          _doc("lab_report", "Haemoglobin 7.1 g/dL; reticulocytes 11%.")])])


# ── R2 · claim above the published rate ──────────────────────────────────────
# Revision total hip replacement, mandatory: clinical notes and X-ray/CT; post-procedure X-ray showing the implant,
# operative notes, implant barcodes, discharge summary.

def _above_rate(b: _Builder) -> None:
    hip, h = _package(b.tools, "SB038D"), _hospital(b.tools, "orthopaedics")
    hip_story = ("Loosening of a hip implant placed eight years ago with pain on walking; revision total hip replacement "
                 "planned.", "X-ray pelvis shows loosening at the prosthesis-bone interface.",
                 "revision total hip replacement", "post-operative X-ray shows the revision cup and stem in position",
                 None)

    def case(n, fraud, reading, note, claimed, extra, p=hip, hospital=h, story=hip_story):
        cid, ben = f"BCH-R2-{n:02d}", f"BEN-BR2{n:02d}"
        indication, imaging, what, postop, photo = story
        docs = [_doc("clinical_notes", f"Pre-operative notes, claim {cid}. {indication}"),
                _doc("radiology_report", f"Claim {cid}: {imaging}"),
                _doc("operative_note", f"Operative note, claim {cid}: {what} performed; implants as per invoice; "
                                       f"barcode stickers attached."),
                _doc("post_procedure_xray", f"Claim {cid}: {postop}.")]
        if photo:
            docs.append(_doc("clinical_photograph", f"Claim {cid}: {photo}"))
        docs += [*extra, _doc("discharge_summary", f"Discharge summary, claim {cid}, beneficiary {ben}. Recovered after "
                                                   f"{what}; walking with a frame on day 4; discharged on day 5.")]
        b.add(cid, "R2", fraud, reading, note, b.claim(cid, hospital, p, ben, docs, los=5, amount=claimed))

    implants = ("Revision acetabular cup, cementless, 54 mm: Rs 38,000. Revision femoral stem, long modular: Rs 22,000. "
                "Total Rs 60,000. Barcode stickers attached.")
    case(1, False, "keyword", "an implant invoice that accounts for the excess", 100_000,
         [_doc("implant_invoice", f"Implant invoice. {implants}")])
    case(2, False, "mislabelled", "the implant invoice, filed as a vendor bill", 100_000,
         [_doc("vendor_bill", f"Vendor bill. {implants}")])
    spine_story = ("Lumbar canal stenosis with spondylolisthesis; claudication after 100 metres; laminectomy with fusion "
                   "and fixation planned.", "MRI lumbar spine: L4-L5 spondylolisthesis with severe canal stenosis.",
                   "laminectomy with fusion and fixation",
                   "post-operative X-ray shows pedicle screws and rods at L4-L5 with graft in place",
                   "post-procedure photograph of the healed scar with the patient's ID and date.")
    case(3, False, "mislabelled", "pedicle screws and rods, filed as a pharmacy bill", 79_000,
         [_doc("pharmacy_bill", "Pharmacy and implant bill: titanium pedicle screws 6 x Rs 5,000; connecting rods 2 x "
                                "Rs 4,500. Total Rs 39,000. Barcodes attached.")],
         p=_package(b.tools, "SN034B"), hospital=_hospital(b.tools, "neurosurgery"), story=spine_story)
    case(4, True, "trap", "an 'implant invoice' listing only consumables", 98_000,
         [_doc("implant_invoice", "Implant invoice. Surgical gloves 40 pairs; drapes; sutures; IV sets; dressing "
                                  "packs; syringes. Total Rs 58,000.")])
    case(5, True, "trap", "an implant invoice for a coronary stent, on a hip operation", 70_000,
         [_doc("implant_invoice", "Implant invoice. Drug-eluting coronary stent 3.0 x 28 mm: Rs 30,000. Barcode "
                                  "attached.")])
    case(6, True, "contradiction", "a genuine invoice that explains Rs 12,000 of a Rs 60,000 excess", 100_000,
         [_doc("implant_invoice", "Implant invoice. Cemented acetabular liner: Rs 12,000. Barcode attached.")])
    case(7, True, "keyword", "no invoice at all for the excess", 95_000, [])


# ── R3 · a reserved package billed by a private hospital ─────────────────────
# Caesarean delivery, mandatory: admission notes with antenatal records and the indication, labour charting; operative
# notes, the child's status at delivery and discharge, progress notes, discharge summary.

def _reserved_packages(b: _Builder) -> None:
    h = _hospital(b.tools, "obgyn")
    caesarean = _package(b.tools, "SO057A")
    assert caesarean.referral_allowed and all(caesarean.reserved_under.values())

    def case(n, fraud, reading, note, extra):
        cid, ben = f"BCH-R3-{n:02d}", f"BEN-BR3{n:02d}"
        docs = [_doc("clinical_notes", f"Admission notes, claim {cid}. Term pregnancy in labour for 14 hours with "
                                       f"failure to progress; emergency caesarean section decided."),
                _doc("antenatal_record", f"Antenatal card, claim {cid}: four antenatal visits; haemoglobin 10.8 g/dL; "
                                         f"blood group B positive; USG at 32 weeks normal."),
                _doc("partograph", f"Labour chart, claim {cid}: cervix 6 cm at 8 am and 6 cm at 2 pm despite "
                                   f"oxytocin; fetal heart 140-150/min."),
                _doc("operative_note", f"Operative note, claim {cid}: lower segment caesarean section; live baby, "
                                       f"2.9 kg, cried at birth, Apgar 8 and 9; mother stable."),
                _doc("progress_notes", f"Post-operative notes, claim {cid}. Day 1: catheter out, breastfeeding. Day 2: "
                                       f"wound healthy. Day 3: mother and baby well."),
                *extra, _doc("discharge_summary", f"Discharge summary, claim {cid}, beneficiary {ben}. Mother and "
                                                  f"baby well, baby 2.85 kg at discharge; discharged on day 3.")]
        b.add(cid, "R3", fraud, reading, note, b.claim(cid, h, caesarean, ben, docs, los=3))

    case(1, False, "keyword", "a referral from a government community health centre",
         [_doc("referral_letter", "Referral letter, Community Health Centre, Sanand (Government of Gujarat): referred "
                                  "for emergency caesarean section; no obstetrician or blood bank available. Signed, "
                                  "Medical Officer.")])
    case(2, False, "mislabelled", "the government referral, filed as a referral slip",
         [_doc("referral_slip", "Primary Health Centre, Bavla (Government of Gujarat): mother referred in obstructed "
                                "labour for caesarean section at the nearest equipped hospital. Medical Officer, PHC.")])
    case(3, False, "keyword", "a referral from a government district hospital",
         [_doc("referral_letter", "District Hospital (Government): referred for caesarean section as the operation "
                                  "theatre is closed for repairs. Medical Superintendent.")])
    case(4, True, "trap", "a 'referral letter' from a private maternity clinic",
         [_doc("referral_letter", "Shree Maternity Clinic (private practice): referred for caesarean section. Dr. "
                                  "K. Patel, proprietor.")])
    case(5, True, "trap", "a 'referral letter' recording that there was no referral",
         [_doc("referral_letter", "Self-referred: the patient came directly to this hospital; no referral from any "
                                  "facility.")])
    case(6, True, "keyword", "no referral on file", [])
