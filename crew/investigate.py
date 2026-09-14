"""Deterministic investigation -- the degraded path (NFR-1).

Each function mirrors one agent's job using rules over structured evidence.
The LLM crew (crew/crew_llm.py) does the same jobs by reading the documents;
when it is unavailable, fails, or returns invalid output, these run instead and
the decision is marked `degraded`.

The desk audit may only use what a desk has: the claim's documents and the
registry. Anything needing a person on the ground is INCONCLUSIVE here, which
is what makes the policy order a field audit rather than guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from crew.schemas import AgentFinding, Channel, Claim, Hospital, Package, TriggerHit
from rules.triggers import ClaimStore, billed_specialty, died_before_admission

EXPLAINED_SAME_DAY = re.compile(r"\b(LAMA|DAMA|left against medical advice|discharge(d)? against medical advice|"
                                r"discharge on request|DOR)\b", re.I)
# A same-day discharge explains a zero length of stay, not billing for a procedure the file says never happened.
# Found by the Gemini crew on scenario S3 (docs/05-TEST-PLAN.md, F-06).
NOT_PERFORMED = re.compile(r"\b(deferred|postponed|cancell?ed|not (?:been )?(?:performed|done|undertaken))\b", re.I)
CLINICAL_JUSTIFICATION = re.compile(r"\b(complication|sepsis|ICU|critical|ventilat|deteriorat)\w*", re.I)
# Justification for a long stay must come from clinical documentation. Searching every document let a
# discharge summary that merely repeats a package name ("Severe sepsis") clear a padded stay.
CLINICAL_DOCS = frozenset({"clinical_notes", "progress_notes", "icu_notes", "case_sheet"})

# NHA trigger-2 checklist: "Verify mandatory documents for blocked procedure". The HBP master states the
# requirement in prose, so the desk checks that each CATEGORY the prose names is on file, under a document type of
# that kind. Checking only three categories let a claim missing its histopathology, photographs or investigation
# reports be cleared; the live crew with tools found both gaps (F-55, F-56). The corpus files the same categories
# (generate/corpus.py), so a complete claim is never blocked.
PRE_EVIDENCE = frozenset({"pre_investigation", "clinical_notes", "radiology_report", "investigation_report",
                          "lab_report", "endoscopy_report", "histopathology"})
NEEDS_PROCEDURE_NOTE = re.compile(r"\b(operative|operation|procedure)\s+notes?\b", re.I)


@dataclass(frozen=True)
class Requirement:
    label: str                   # what is missing, in words
    doc_types: re.Pattern        # the document types that satisfy it
    filed_as: str                # the document type a complete claim files it under (the corpus)


DISCHARGE_SUMMARY = Requirement("discharge summary", re.compile(r"^discharge_summary$"), "discharge_summary")
PRE_PROCEDURE = Requirement("pre-procedure evidence", re.compile(rf"^(?:{'|'.join(sorted(PRE_EVIDENCE))})$"),
                            "pre_investigation")
# (what the post-procedure requirement says, what satisfies it). Implant barcodes are not here: the master asks for them
# "if used", and an implant invoice is R2's evidence, read by its own rule.
AFTER_PROCEDURE = (
    (NEEDS_PROCEDURE_NOTE, Requirement("operative or procedure note", re.compile(r"^(?:operative|procedure)_note$"),
                                       "operative_note")),
    (re.compile(r"histopath|\bHPE\b", re.I), Requirement("histopathology report", re.compile(r"histopath|hpe|biopsy"),
                                                         "histopathology")),
    (re.compile(r"photo|photgraph|still image|\bstills\b|\bpic\b", re.I),
     Requirement("clinical photograph", re.compile(r"photo|still|image"), "clinical_photograph")),
    (re.compile(r"x-?ray|\bCT\b|\bMRI\b|\bUSG\b|imaging|angiogra|doppler|\becho\b", re.I),
     Requirement("post-procedure imaging", re.compile(r"x_?ray|radiolog|imaging|(?:^|_)ct(?:_|$)|mri|usg|ultrasound|echo|"
                                                      r"angiogra|doppler|scan"), "post_procedure_imaging")),
    (re.compile(r"investigation|reports of the tests|lab tests", re.I),
     Requirement("investigation reports", re.compile(r"investigation|lab|patholog|haematolog|hematolog|biochem|microbio|"
                                                     r"culture|radiolog|x_?ray|imaging|scan|ultrasound|usg|echo|mri"),
                 "investigation_report")),
    (re.compile(r"\bICPs?\b|case papers|progress notes", re.I),
     Requirement("indoor case papers", re.compile(r"progress|case_?paper|case_?sheet|icp|indoor|treatment_chart|nursing"),
                 "case_papers")),
)


def required_documents(package: Package) -> list[Requirement]:
    """The document categories the package master makes mandatory for this package."""
    reqs = [DISCHARGE_SUMMARY]
    if (package.pre_investigations or "").strip().lower() not in ("", "na", "nan", "-", "none"):
        reqs.append(PRE_PROCEDURE)
    post = package.post_investigations or ""
    return reqs + [r for says, r in AFTER_PROCEDURE if says.search(post)]

# Clinical text states absences as often as findings. Read as keywords, "Stable, no complications" justified a
# padded stay, "Non-critical course" justified it too, and "Not a LAMA case" explained a zero stay (F-23).
# A small NegEx-style reading: a cue within five words before a mention, in the same clause, negates it, and so
# does an absence stated right after it ("Complications: nil", "ICU not required").
NEGATION_BEFORE = re.compile(r"\b(?:no|not|non|without|nil|never|neither|nor|denies|denied|absence of|free of|"
                             r"negative for|rules? out|ruled out)\b|n't\b", re.I)
# "No" is also "number" in Indian case papers ("Bed No 12", "IP No 4471", "No of days"): not a negation there.
NUMBER_NO = re.compile(r"\bno\b(?=\s+(?:\d|of\b))", re.I)
_ABSENT_NEXT = r"(?:no|nil|none|n/?a|not applicable|negative)"   # "LAMA: No", "Sepsis? No", "Sepsis: negative"
_ABSENT_NEAR = (r"(?:ruled out|excluded|absent|not (?:required|needed|indicated|seen|noted|found|present|observed|"
                r"done|given))")                                   # "ICU admission not required"
NEGATION_AFTER = re.compile(rf"^\s*[:\-?(]?\s*(?:{_ABSENT_NEXT}|{_ABSENT_NEAR})\b"
                            rf"|^[^.;:!?\n,]{{0,30}}?\b{_ABSENT_NEAR}\b", re.I)
CLAUSE_END = re.compile(r"[.;:!?\n,]|\b(?:but|however|although|though|except|yet|later|subsequently|then)\b", re.I)


def affirmed(pattern: re.Pattern, text: str) -> list[re.Match]:
    """The mentions of `pattern` in `text` that are not negated."""
    out, prev_end, prev_negated = [], None, False
    for m in pattern.finditer(text):
        if prev_end is not None and re.fullmatch(r"\s*\(\s*", text[prev_end:m.start()]):
            # An abbreviation in brackets restates the mention before it: "not a left against medical advice
            # (LAMA) case" negates both.
            negated = prev_negated
        else:
            before = text[:m.start()]
            start = max((c.end() for c in CLAUSE_END.finditer(before)), default=0)
            clause = NUMBER_NO.sub(" number ", before[start:])
            negated = bool(NEGATION_BEFORE.search(" ".join(re.findall(r"[A-Za-z0-9']+", clause)[-5:])))
        negated = negated or bool(NEGATION_AFTER.search(text[m.end():m.end() + 60]))
        if not negated:
            out.append(m)
        prev_end, prev_negated = m.end(), negated
    return out


# ── the death certificate's own date ─────────────────────────────────────────

_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
           "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_DATE = (rf"(\d{{1,2}}(?:st|nd|rd|th)?[\s\-/.]+(?:{_MONTHS})[a-z]*[\s\-/.,]+\d{{4}}"      # 28 May 2026, 28-May-2026
         rf"|(?:{_MONTHS})[a-z]*\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"                  # May 28, 2026
         rf"|\d{{4}}-\d{{1,2}}-\d{{1,2}}"                                                   # 2026-05-28
         rf"|\d{{1,2}}[/.\-]\d{{1,2}}[/.\-]\d{{4}})")                                       # 28/05/2026 (day first)
DEATH_DATE = re.compile(rf"\b(?:date\s+of\s+death|died\s+on|expired\s+on|death\s+on|DOD)\b\W{{0,5}}{_DATE}", re.I)


def _parse_date(s: str) -> date | None:
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s.strip(), flags=re.I)
    s = re.sub(r"[\s\-/.,]+", " ", s).strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%Y %m %d", "%d %m %Y"):
        try:
            return datetime.strptime(s.replace("Sept ", "Sep "), fmt).date()
        except ValueError:
            continue
    return None


def certified_death_dates(claim: Claim) -> list[date]:
    """Dates of death stated on the death certificates on file ("Date of death 28 May 2026", "DOD: 28/05/2026")."""
    return [d for doc in claim.documents if doc.doc_type == "death_certificate"
            for m in DEATH_DATE.finditer(doc.text) if (d := _parse_date(m.group(1))) is not None]


def _says(pattern: re.Pattern, claim: Claim):
    """Documents that affirm `pattern`."""
    return [d for d in claim.documents if affirmed(pattern, d.text)]


def _doc(claim: Claim, *types: str):
    return [d for d in claim.documents if d.doc_type in types]


INVOICE_DOCS = re.compile(r"invoice|bill|receipt")        # document types that can account for an amount (R2)
REFERRAL_DOCS = re.compile(r"referral")                   # document types that can carry a referral (R3)


def _typed(claim: Claim, kinds: re.Pattern):
    """Documents whose type is of a kind, whatever the exact label: a desk reads a 'vendor_bill' as an invoice."""
    return [d for d in claim.documents if kinds.search(d.doc_type)]


def missing_mandatory(claim: Claim, package: Package) -> list[str]:
    """Mandatory document categories the package requires and the claim does not carry."""
    types = {d.doc_type for d in claim.documents}
    return [r.label for r in required_documents(package) if not any(r.doc_types.search(t) for t in types)]


def desk_preconditions(finding: AgentFinding, claim: Claim, hit: TriggerHit, package: Package) -> AgentFinding:
    """What a desk reading needs before it may count -- applied to the rules desk AND the crew's desk agent,
    so neither path can clear or convict on less evidence than the other.

    * clearing a claim needs the package's mandatory documents (F-06)
    * a same-day discharge cannot clear a claim whose file says the procedure was not performed (F-06)
    * billing after death needs a death certificate at the desk; a register entry is a lead (E-1) -- and the
      certificate must itself date the death before the admission. One dating it after admission contradicts the
      register, and one with no readable date of death proves nothing (F-39)
    """
    def inconclusive(reason: str, cite: str) -> AgentFinding:
        return finding.model_copy(update={"supports_fraud": None, "confidence": 0.0,
                                          "conclusion": f"{finding.conclusion} {reason}",
                                          "citation": f"{finding.citation}; {cite}"})

    if finding.supports_fraud is False:
        missing = missing_mandatory(claim, package)
        if missing:
            return inconclusive(f"But mandatory documents are missing ({', '.join(missing)}), so the desk cannot "
                                f"clear the claim.", "HBP package master: mandatory documents")
        if hit.trigger_id in ("T2", "T3") and _says(NOT_PERFORMED, claim):
            return inconclusive("But the file says the procedure was not performed, so billing it is unexplained.",
                                "documents on file")
        # A recorded fact the documents cannot explain away: only the field can. An agent that "cleared" these
        # would release a claim over a certificate dating death before admission, or over a byte-identical file (F-44).
        if hit.trigger_id == "T6":
            return inconclusive("But the same document is on file for another beneficiary; that is a fact of the file, "
                                "and only a hospital visit or the beneficiary can explain it.", "document hashes")
        if hit.trigger_id == "R3" and not package.referral_allowed:
            return inconclusive("But the package master gives this reserved package no referral route, so nothing on "
                                "file can permit a private hospital to bill it.", "hbp_package_listings: referral_basis")
        # The document that would explain the trigger must be on file for a clearance. Live, the crew released an
        # above-rate claim with no invoice at all and a reserved package with no referral, reading each gap as an
        # upload slip (F-58). Whether a document that IS on file explains the trigger is the reader's call.
        if hit.trigger_id == "R2" and not _typed(claim, INVOICE_DOCS):
            return inconclusive("But no invoice or bill on file accounts for the amount above the published rate.",
                                "claim documents")
        if hit.trigger_id == "R3" and not _typed(claim, REFERRAL_DOCS):
            return inconclusive("But no referral is on file, and a private hospital may bill this package only on one.",
                                "claim documents")
        if hit.trigger_id == "T10":
            admitted = claim.admission_ts.date()
            dates = certified_death_dates(claim)
            if dates and all(d < admitted for d in dates):
                return inconclusive(f"But the death certificate on file dates the death {max(dates):%d %B %Y}, before "
                                    f"the admission; the desk cannot clear that.", "death_certificate")
    if finding.supports_fraud is True and hit.trigger_id == "T10":
        if not _doc(claim, "death_certificate"):
            return inconclusive("But no death certificate is on file; a register entry alone is a lead, not proof.",
                                "beneficiary death registry")
        dates = certified_death_dates(claim)
        admitted = claim.admission_ts.date()
        if not dates:
            return inconclusive("But the death certificate on file states no readable date of death, so it cannot "
                                "confirm the register.", "death_certificate")
        if any(d >= admitted for d in dates):
            return inconclusive(f"But the death certificate dates the death {max(dates):%d %B %Y}, not before the "
                                f"admission on {admitted:%d %B %Y}: it contradicts the register.", "death_certificate")
    return finding


def desk_audit(claim: Claim, hit: TriggerHit, package: Package, store: ClaimStore) -> AgentFinding:
    """Documents on file versus the trigger's checklist, subject to desk_preconditions()."""
    return desk_preconditions(_desk_reading(claim, hit, package, store), claim, hit, package)


def _desk_reading(claim: Claim, hit: TriggerHit, package: Package, store: ClaimStore) -> AgentFinding:
    t = hit.trigger_id

    def f(supports, conf, conclusion, citation):
        return AgentFinding(agent="desk_audit", channel=Channel.DESK_AUDIT, supports_fraud=supports,
                            confidence=conf, conclusion=conclusion, citation=citation)

    if t in ("T2", "T3"):
        explained, not_done = _says(EXPLAINED_SAME_DAY, claim), _says(NOT_PERFORMED, claim)
        if explained and not_done:
            return f(None, 0.0, "The same-day discharge is documented, but the file also says the procedure was not "
                                "performed; whether this package should have been billed needs the OT register and "
                                "the patient.", f"{explained[0].doc_type}; {not_done[0].doc_type}")
        if explained:
            return f(False, 0.85, "Same-day discharge is documented as patient-initiated (LAMA/DAMA/DOR); "
                                  "zero length of stay is explained.", f"{explained[0].doc_type}")
        return f(None, 0.0, "No document explains the same-day discharge; whether the procedure took place "
                            "needs the OT register and the patient.", "claim documents")

    if t == "T4":
        justified = [d for d in _says(CLINICAL_JUSTIFICATION, claim) if d.doc_type in CLINICAL_DOCS]
        if justified:
            return f(False, 0.80, "Clinical notes document a complication justifying the extended stay.",
                     justified[0].doc_type)
        return f(True, 0.72, f"{claim.los_days}-day stay with no ICU flag and no clinical justification on file.",
                 "clinical notes; icu_flag")

    if t == "T5":
        return f(None, 0.0, "Billing timestamps alone cannot place the surgeon; needs rosters and the OT register.",
                 "claim documents")

    if t == "T6":
        reused = store.reused(claim)
        if reused:
            d, other = reused[0]
            return f(True, 0.90, f"The {d.doc_type} is byte-identical to one filed on {other.claim_id} for a different "
                                 f"beneficiary.", f"{d.doc_type} sha256 {d.sha256[:12]}")
        return f(None, 0.0, "No reused document found on file.", "claim documents")

    if t == "T7":
        return f(None, 0.0, "Repeat admissions are clinically possible; only the beneficiary can say whether "
                            "they happened.", "beneficiary claim history")

    if t == "T10":
        certs = _doc(claim, "death_certificate")
        if died_before_admission(claim):
            days = (claim.admission_ts.date() - claim.beneficiary_death_ts.date()).days
            if certs:
                return f(True, 0.95, f"Admission recorded {days} days after the beneficiary's certified death.",
                         certs[0].doc_type)
            # Guidebook Annexure 2, trigger 10: a date-of-death mismatch is verified by asking the relatives
            # and for the death certificate -- field work. A register entry alone can be wrong, and acting
            # on it would suspend a hospital over a data error.
            return f(None, 0.0, f"The death register dates death {days} days before admission, but no death "
                                "certificate is on file; a register entry alone is a lead, not proof.",
                     "beneficiary death registry")
        return f(None, 0.0, "The registered date of death is not before the day of admission.",
                 "beneficiary death registry")

    if t == "R2":
        if _doc(claim, "implant_invoice"):
            return f(False, 0.80, "Implant invoice on file accounts for the amount above the package rate.",
                     "implant_invoice")
        return f(True, 0.80, f"Claimed Rs {claim.amount_claimed:,} exceeds the published rate "
                             f"Rs {package.amount_rs:,} with no implant invoice.", "hbp_package_rates")

    if t == "R3":
        if not package.referral_allowed:
            return f(True, 0.80, "The package is reserved for government hospitals with no referral route in the package "
                                 "master, so no referral can permit a private hospital to bill it.",
                     "hbp_package_listings: referral_basis")
        if _doc(claim, "referral_letter"):
            return f(False, 0.80, "A referral from a government facility is on file, and the package master allows this "
                                  "package on referral.", "referral_letter; hbp_package_listings: referral_basis")
        return f(True, 0.80, "Government-reserved package billed without the referral it allows.",
                 "hbp_package_listings: referral_basis")

    # R1 is a registry question, not a documents question.
    return f(None, 0.0, "Nothing on the documents bears on this trigger.", "claim documents")


def registry_verification(claim: Claim, hit: TriggerHit, hospital: Hospital, package: Package) -> AgentFinding:
    """Claimed facts versus the published registry.

    The registry is evidence for ONE allegation -- R1, billing a specialty the hospital is not empanelled
    for -- and for nothing else: a registry mismatch says nothing about whether a patient died before
    admission or was admitted three times. A blank specialty list is missing data (D-4), never evidence.
    """
    def neutral(conclusion: str) -> AgentFinding:
        return AgentFinding(agent="registry_verification", supports_fraud=None, confidence=0.0,
                            conclusion=conclusion, citation="registry_pseudonymised")

    if not hospital.specialties:
        return neutral("The registry lists no specialties for this hospital (data defect D-4), so it can neither "
                       "confirm nor contradict what was billed.")
    if not package.specialties:
        return neutral("The package maps to no registry specialty, so the registry can neither confirm nor "
                       "contradict what was billed.")
    listed = ", ".join(package.specialties)
    if not set(package.specialties) & set(hospital.specialties):
        if hit.trigger_id == "R1":
            return AgentFinding(
                agent="registry_verification", supports_fraud=True, confidence=0.85,
                conclusion=f"{package.package_code} is listed under {listed}; the registry lists this hospital for "
                           f"none of them.",
                citation="registry_pseudonymised: specialties")
        return neutral(f"The registry lists this hospital for none of {listed} (R1 also fired), but that bears on "
                       f"R1, not on {hit.trigger_id}.")
    return neutral(f"Registry consistent: {hospital.hospital_type or 'type not recorded'}, empanelled for "
                   f"{billed_specialty(package, hospital)}. Consistency with the registry neither confirms nor clears "
                   f"the trigger.")


def field_review(claim: Claim) -> list[AgentFinding]:
    """Field reports already on file, when a case re-enters after a field audit."""
    return [AgentFinding(agent="field_review", channel=r.channel, supports_fraud=r.supports_fraud,
                         confidence=r.confidence, conclusion=r.summary, citation=f"field report: {r.channel.value}")
            for r in claim.field_reports]


def channels_used(claim: Claim) -> list[Channel]:
    """The desk always; field channels only when their evidence is actually on file."""
    seen = [Channel.DESK_AUDIT]
    for r in claim.field_reports:
        if r.channel not in seen:
            seen.append(r.channel)
    return seen


def investigate(claim: Claim, hit: TriggerHit, hospital: Hospital, package: Package,
                store: ClaimStore) -> list[AgentFinding]:
    return [desk_audit(claim, hit, package, store),
            registry_verification(claim, hit, hospital, package),
            *field_review(claim)]
