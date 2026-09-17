"""Third audit, 17 September: edge cases. Each test fails without its fix (docs/05-TEST-PLAN.md, F-35 to F-46)."""
import argparse
from datetime import timedelta, timezone

import pytest
from pydantic import ValidationError

from crew import actions
from crew.run import clear_previous_run, process
from crew.schemas import IST, Action, Channel, Claim, Document, FieldReport
from generate.corpus import CorpusConfig
from metrics import run as metrics
from rules import triggers


def _fires(claim, tools, store=None):
    h, p = tools.registry_lookup(claim.hospital_ref), tools.hbp_lookup(claim.package_code)
    return {x.trigger_id for x in triggers.evaluate(claim, h, p, store or triggers.ClaimStore([claim]), tools)}


def _at(claim, hospital, package, cid):
    return claim.model_copy(update={"claim_id": cid, "beneficiary_ref": f"BEN-{cid}", "hospital_ref": hospital.hospital_ref,
                                    "district_code": hospital.district_code, "package_code": package.package_code,
                                    "amount_claimed": package.amount_rs, "field_reports": []})


def _hospital(tools, has=(), lacks=(), kind=None):
    return sorted((h for h in tools.hospitals() if h.specialties and all(s in h.specialties for s in has)
                   and not any(s in h.specialties for s in lacks) and (kind is None or h.hospital_type == kind)),
                  key=lambda h: h.hospital_ref)[0]


# ── F-35 · a package is billable through every specialty it is listed under ──

def test_a_package_listed_under_two_specialties_is_billable_through_either(world, tools):
    """MP001C is listed under General Medicine and Paediatrics. A paediatric hospital billing it was accused (R1)."""
    s7, p = world[0]["S7"].claim, tools.hbp_lookup("MP001C")
    assert set(p.specialties) == {"paediatric_medicine", "general_medicine"} and p.specialty == "paediatric_medicine"
    paediatric = _hospital(tools, has=["paediatric_medicine"], lacks=["general_medicine"], kind="Public")
    assert "R1" not in _fires(_at(s7, paediatric, p, "CLM-XL-1"), tools)
    neither = _hospital(tools, lacks=["paediatric_medicine", "general_medicine"], kind="Public")
    assert "R1" in _fires(_at(s7, neither, p, "CLM-XL-2"), tools)                  # still caught when it should be


def test_a_package_reserved_under_one_listing_is_open_through_another(world, tools):
    """MG007A is government-reserved under General Medicine only. A private paediatric hospital got R1 and R3."""
    s7, p = world[0]["S7"].claim, tools.hbp_lookup("MG007A")
    assert p.reserved_under == {"general_medicine": True, "paediatric_medicine": False} and not p.govt_reserved
    private_paeds = _hospital(tools, has=["paediatric_medicine"], lacks=["general_medicine"], kind="Private(For Profit)")
    assert not {"R1", "R3"} & _fires(_at(s7, private_paeds, p, "CLM-XL-3"), tools)
    private_gm = _hospital(tools, has=["general_medicine"], lacks=["paediatric_medicine"], kind="Private(For Profit)")
    assert "R3" in _fires(_at(s7, private_gm, p, "CLM-XL-4"), tools)               # reserved as it bills it


def test_adequacy_is_for_the_specialty_the_hospital_bills_through(world, tools, tmp_path):
    s1, p = world[0]["S1"].claim, tools.hbp_lookup("MP001C")
    paediatric = _hospital(tools, has=["paediatric_medicine"], lacks=["general_medicine"], kind="Public")
    claim = _at(s1, paediatric, p, "CLM-XL-5")
    # Its own store: S1's documents under another beneficiary in S1's store would rightly read as reuse (T6).
    d = process(claim, triggers.ClaimStore([claim]), tools, out_dir=tmp_path)
    assert d.trigger_id == "T10" and d.adequacy.specialty == "paediatric_medicine"


# ── F-36 · document hashes are the hash of the text ─────────────────────────

def test_a_supplied_document_hash_must_match_its_text():
    with pytest.raises(ValidationError, match="sha256"):
        Document(doc_type="lab_report", text="Haemoglobin 11.2", sha256="f" * 64)
    ok = Document(doc_type="Lab Report", text="Haemoglobin 11.2")
    assert Document(doc_type="lab_report", text="Haemoglobin 11.2", sha256=ok.sha256.upper()).sha256 == ok.sha256
    assert ok.doc_type == "lab_report"


def test_two_different_documents_cannot_share_a_stale_hash(world, tools):
    s3 = world[0]["S3"].claim
    a = s3.model_copy(update={"claim_id": "CLM-H-A", "beneficiary_ref": "BEN-H-A"})
    stale = a.documents[0].model_copy(update={"text": "entirely different text"})     # model_copy keeps the old hash
    b = a.model_copy(update={"claim_id": "CLM-H-B", "beneficiary_ref": "BEN-H-B", "documents": [stale]})
    assert "T6" not in _fires(a, tools, triggers.ClaimStore([a, b]))


# ── F-37 · a claim id names a file, so it cannot name a path ────────────────

@pytest.mark.parametrize("cid", ["../../escaped", "..\\..\\pwned", "a/b", "", ".hidden", "x" * 65, "CLM 1"])
def test_claim_ids_are_plain_tokens(world, cid):
    with pytest.raises(ValidationError, match="claim_id"):
        Claim(**{**world[0]["S1"].claim.model_dump(), "claim_id": cid})


def test_an_unvalidated_claim_id_never_reaches_the_file_system(world, tools, tmp_path):
    s3 = world[0]["S3"].claim
    bypass = s3.model_copy(update={"claim_id": "..\\..\\..\\..\\pwned", "documents": s3.documents[:-1]})
    with pytest.raises(ValidationError):
        process(bypass, world[1], tools, out_dir=tmp_path / "run")
    decision = process(s3.model_copy(update={"documents": s3.documents[:-1]}), world[1], tools, out_dir=tmp_path / "ok")
    forged = decision.model_copy(update={"case_id": "CASE-../../../pwned"})
    with pytest.raises(ValueError, match="outside"):
        actions.execute(forged, s3, None, None, tmp_path / "run2")
    assert not list(tmp_path.rglob("*pwned*"))


# ── F-38 · claims are strict about their fields ─────────────────────────────

def test_a_misspelled_claim_field_is_an_error_not_a_silent_omission(world):
    s1 = world[0]["S1"].claim
    with pytest.raises(ValidationError, match="beneficiary_death_date"):
        Claim(**{**s1.model_dump(exclude={"beneficiary_death_ts"}), "beneficiary_death_date": s1.beneficiary_death_ts})


def test_timestamps_with_an_offset_are_read_in_indian_time(world, tools, tmp_path):
    s3 = world[0]["S3"].claim
    utc = {k: getattr(s3, k).replace(tzinfo=IST).astimezone(timezone.utc)
           for k in ("admission_ts", "discharge_ts", "submitted_ts")}
    c = Claim(**{**s3.model_dump(), "claim_id": "CLM-UTC", **utc})
    assert c.admission_ts == s3.admission_ts and c.admission_ts.tzinfo is None
    mixed = s3.model_copy(update={"claim_id": "CLM-MIXED", "admission_ts": utc["admission_ts"]})   # bypasses validation
    assert process(mixed, world[1], tools, out_dir=tmp_path).action is Action.RELEASE_CLAIM          # raised TypeError


def test_identifiers_are_compared_as_the_same_person_or_surgeon(world, tools):
    s6 = world[0]["S6"].claim
    variant = s6.model_copy(update={"claim_id": "CLM-SURG", "surgeon_reg_no": " reg-sim-5501 ", "field_reports": []})
    other = world[1].claims["CLM-S06A"]
    assert "T5" in _fires(variant, tools, triggers.ClaimStore([variant, other]))
    with pytest.raises(ValidationError, match="blank"):
        Claim(**{**s6.model_dump(), "beneficiary_ref": "   "})
    assert Claim(**{**s6.model_dump(), "surgeon_reg_no": "  "}).surgeon_reg_no is None


# ── F-39 · a death certificate must date the death before admission ─────────

def _certified(s1, text, cid):
    docs = [Document(doc_type="death_certificate", text=text) if d.doc_type == "death_certificate" else d
            for d in s1.documents]
    return s1.model_copy(update={"claim_id": cid, "documents": docs})


def test_a_certificate_dating_death_after_admission_contradicts_the_register(world, tools, tmp_path):
    s1 = world[0]["S1"].claim
    later = _certified(s1, f"Certificate of death. Date of death {s1.admission_ts + timedelta(days=2):%d %B %Y}.", "CLM-C1")
    d = process(later, world[1], tools, out_dir=tmp_path)
    assert d.action is Action.FIELD_AUDIT and "contradicts the register" in d.findings[0].conclusion


def test_a_certificate_with_no_readable_date_proves_nothing(world, tools, tmp_path):
    s1 = world[0]["S1"].claim
    d = process(_certified(s1, "Certificate of death issued by the municipal registrar.", "CLM-C2"), world[1], tools,
                out_dir=tmp_path)
    assert d.action is Action.FIELD_AUDIT and "no readable date of death" in d.findings[0].conclusion


@pytest.mark.parametrize("stated", ["DOD: {:%d/%m/%Y}", "Date of death {:%Y-%m-%d}", "Died on {:%B %d, %Y}",
                                    "date of death: {:%d-%b-%Y}"])
def test_common_date_formats_on_a_certificate_are_read(world, tools, tmp_path, stated):
    s1 = world[0]["S1"].claim
    c = _certified(s1, "Certificate of death. " + stated.format(s1.beneficiary_death_ts), "CLM-C3")
    assert process(c, world[1], tools, out_dir=tmp_path).action is Action.SUSPEND


# ── F-40, F-41 · intake refuses contradictions instead of choosing ─────────

def test_a_district_name_that_contradicts_the_code_is_refused(world, tools, tmp_path):
    s1 = world[0]["S1"].claim
    wrong = process(s1.model_copy(update={"claim_id": "CLM-D1", "district_name": "Bahraich"}), world[1], tools,
                    out_dir=tmp_path)
    assert wrong.action is Action.REFUSE and "MALFORMED_DISTRICT_NAME_CODE_MISMATCH" in wrong.reason_codes
    right = process(s1.model_copy(update={"claim_id": "CLM-D2", "district_name": " ahmedabad "}), world[1], tools,
                    out_dir=tmp_path)
    assert right.action is Action.SUSPEND


def test_field_evidence_is_one_report_per_field_channel(world, tools, tmp_path):
    s7 = world[0]["S7"].claim
    desk = FieldReport(channel=Channel.DESK_AUDIT, supports_fraud=True, confidence=0.95, summary="x")
    call = FieldReport(channel=Channel.BENEFICIARY_CALL, supports_fraud=True, confidence=0.8, summary="x")
    d1 = process(s7.model_copy(update={"claim_id": "CLM-F1", "field_reports": [desk]}), world[1], tools, out_dir=tmp_path)
    d2 = process(s7.model_copy(update={"claim_id": "CLM-F2", "field_reports": [call, call, call]}), world[1], tools,
                 out_dir=tmp_path)
    assert d1.action is Action.REFUSE and "MALFORMED_FIELD_REPORT_ON_DESK_CHANNEL" in d1.reason_codes
    assert d2.action is Action.REFUSE and "MALFORMED_DUPLICATE_FIELD_REPORT" in d2.reason_codes


# ── F-23 again · clinical shorthand ──────────────────────────────────────────

@pytest.mark.parametrize("text, affirmed", [
    ("Bed No 12 shifted to ICU on day 4.", True),            # "No" is number
    ("IP No 4471: sepsis, antibiotics escalated.", True),
    ("No of days in ICU: 3.", True),
    ("Sepsis? No", False),
    ("Sepsis: negative", False),
    ("No sepsis; stable.", False),
])
def test_clinical_shorthand_is_read_correctly(text, affirmed):
    from crew.investigate import CLINICAL_JUSTIFICATION
    from crew.investigate import affirmed as reads
    assert bool(reads(CLINICAL_JUSTIFICATION, text)) is affirmed


# ── B-28 · document reuse waits for the field (team decision) ───────────────

def test_a_reused_document_at_a_well_served_hospital_waits_for_the_field(world, tools, tmp_path):
    s1 = world[0]["S1"].claim
    shared = Document(doc_type="discharge_summary", text="Discharge summary. Angioplasty completed as planned; stable.")
    base = s1.model_copy(update={"beneficiary_death_ts": None,
                                 "documents": [shared] + [d for d in s1.documents if d.doc_type == "pre_investigation"]})
    earlier = base.model_copy(update={"claim_id": "CLM-R6A", "beneficiary_ref": "BEN-R6A",
                                      "admission_ts": s1.admission_ts - timedelta(days=9),
                                      "discharge_ts": s1.discharge_ts - timedelta(days=9),
                                      "submitted_ts": s1.submitted_ts - timedelta(days=9)})
    claim = base.model_copy(update={"claim_id": "CLM-R6B", "beneficiary_ref": "BEN-R6B"})
    store = triggers.ClaimStore([earlier, claim])
    first = process(claim, store, tools, out_dir=tmp_path)
    assert first.triggers_fired == ["T6"] and first.action is Action.FIELD_AUDIT
    assert "FIELD_VERIFICATION_REQUIRED" in first.reason_codes and "clerical error" in first.explanation
    reports = [FieldReport(channel=Channel.HOSPITAL_VISIT, supports_fraud=True, confidence=0.85, summary="no IPD file"),
               FieldReport(channel=Channel.BENEFICIARY_CALL, supports_fraud=True, confidence=0.80, summary="never admitted")]
    final = process(claim.model_copy(update={"field_reports": reports}), store, tools, out_dir=tmp_path)
    assert final.action is Action.SUSPEND


# ── F-43, F-44 and the rest · safe to run carelessly ─────────────────────────

def test_a_trial_evaluation_never_touches_the_published_one():
    out, doc, published = metrics.destination(1000, True)
    assert not published and out.name == "trial-n1000-quick" and doc.parent == out
    assert metrics.destination(CorpusConfig.n_flagged, False) == (metrics.RESULTS, metrics.DOC, True)
    assert not metrics.destination(CorpusConfig.n_flagged, True)[2]
    with pytest.raises(argparse.ArgumentTypeError):
        metrics._cases("10")


def test_corpus_settings_out_of_range_are_rejected():
    for bad in ({"fpr": 0}, {"recall": 1.5}, {"field_conf": (0.9, 0.6)}, {"n_flagged": 0}):
        with pytest.raises(ValueError, match="out of range"):
            CorpusConfig(**bad)


def test_a_bad_gemini_setting_is_a_clear_error(monkeypatch):
    from crew.gemini import GeminiLLM
    monkeypatch.setenv("GEMINI_MAX_RPM", "ten")
    with pytest.raises(ValueError, match="GEMINI_MAX_RPM"):
        GeminiLLM()


def test_the_artefacts_folder_is_not_emptied_of_files_the_run_did_not_write(tmp_path):
    (tmp_path / "artefacts").mkdir()
    (tmp_path / "artefacts" / "my_notes.txt").write_text("keep me")
    assert "artefacts/my_notes.txt" in clear_previous_run(tmp_path)
    assert (tmp_path / "artefacts" / "my_notes.txt").exists()


def test_a_refused_log_leaves_no_artefact_behind(world, tools, tmp_path):
    (tmp_path / "decision_log.csv").write_text("case_id,action\r\n", encoding="utf-8")
    with pytest.raises(ValueError, match="different columns"):
        process(world[0]["S1"].claim, world[1], tools, out_dir=tmp_path)
    assert not (tmp_path / "artefacts").exists()


def test_every_hospital_counts_itself_in_its_own_district_cells(tools):
    """The gate reads n_providers <= 1 as 'removing this hospital leaves nobody'. A listed specialty whose cell
    counted zero would be protected (or phantom) by a data error, so the invariant is checked on the real data."""
    broken = [(h.hospital_ref, s) for h in tools.hospitals() for s in h.specialties
              if (a := tools.district_adequacy(h, s)) is not None and a.n_providers < 1]
    assert not broken, broken[:5]


def test_an_empty_log_file_is_started_afresh(world, tools, tmp_path):
    (tmp_path / "decision_log.csv").write_text("", encoding="utf-8")
    process(world[0]["S1"].claim, world[1], tools, out_dir=tmp_path)
    assert (tmp_path / "decision_log.csv").read_text(encoding="utf-8").startswith("case_id,claim_id")


def test_a_model_run_that_silently_fell_back_to_the_rules_says_so(tmp_path, monkeypatch, capsys):
    """F-46: with a bad key every case degrades and every row still said PASS."""
    import sys
    from crew import providers
    from crew import run as runner
    from crew.gemini import GeminiLLM
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(providers, "make_llm", lambda provider, client=None: GeminiLLM().with_client(runner.OutageClient()))
    monkeypatch.setattr(sys, "argv", ["crew.run", "--scenarios", "--llm", "--provider", "gemini", "--only", "S3",
                                      "--out", str(tmp_path / "run")])
    assert runner.main() == 2
    assert "ran on the rules, not on gemini (S3)" in capsys.readouterr().out


# ── F-51 · a referral clears only a package the master allows on referral ────

def _reserved_claim(world, tools, referral_allowed, with_letter, cid):
    s4 = world[0]["S4"].claim
    p = sorted((p for p in tools.packages() if p.govt_reserved and p.referral_allowed is referral_allowed
                and p.amount_rs > 0 and len(p.specialties) == 1), key=lambda p: p.package_code)[0]
    h = _hospital(tools, has=p.specialties, kind="Private(For Profit)")
    docs = [d for d in s4.documents] + ([Document(doc_type="referral_letter", text=f"{cid}: referred by the district "
                                                  f"hospital for want of the service.")] if with_letter else [])
    from crew.investigate import DISCHARGE_SUMMARY, required_documents
    docs += [Document(doc_type=r.filed_as, text=f"{cid}: {r.label} on file.")          # every mandatory category
             for r in required_documents(p) if r is not DISCHARGE_SUMMARY]
    return _at(s4, h, p, cid).model_copy(update={"documents": docs})


def test_a_referral_letter_clears_only_a_package_allowed_on_referral(world, tools, tmp_path):
    """180 reserved listings have no referral route; a letter released private billing of any of them."""
    strict = _reserved_claim(world, tools, referral_allowed=False, with_letter=True, cid="CLM-R3-STRICT")
    open_ = _reserved_claim(world, tools, referral_allowed=True, with_letter=True, cid="CLM-R3-OPEN")
    d1 = process(strict, triggers.ClaimStore([strict]), tools, out_dir=tmp_path)
    d2 = process(open_, triggers.ClaimStore([open_]), tools, out_dir=tmp_path)
    assert d1.triggers_fired == ["R3"] and d1.action is Action.SHOW_CAUSE
    assert d2.triggers_fired == ["R3"] and d2.action is Action.RELEASE_CLAIM


def test_the_corpus_places_innocent_r3_flags_where_a_referral_is_possible(tools):
    """An innocent R3 flag is a legitimate referral; on any other reserved package the billing is the breach."""
    from generate.corpus import CorpusBuilder, World
    b = CorpusBuilder(tools, CorpusConfig(n_flagged=10), World(tools))
    innocent = [b.case(i, "R3", False) for i in range(1, 6)]
    assert all(tools.hbp_lookup(c.claim.package_code).referral_allowed for c in innocent)


def test_naming_a_provider_without_llm_is_an_error(monkeypatch, tmp_path):
    """F-52: `--provider claude` without `--llm` ran the rules, silently, as if the crew had run on Claude."""
    import sys
    from crew import run as runner
    monkeypatch.setattr(sys, "argv", ["crew.run", "--scenarios", "--provider", "claude", "--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit) as exit_info:
        runner.main()
    assert exit_info.value.code == 2 and not (tmp_path / "o").exists()


# ── F-56 · the desk checks every document category the package master makes mandatory ────

@pytest.mark.parametrize("code, labels", [
    ("SG070B", {"discharge summary", "pre-procedure evidence", "operative or procedure note", "histopathology report",
                "clinical photograph"}),
    ("MG001A", {"discharge summary", "pre-procedure evidence", "indoor case papers", "investigation reports"}),
    ("SB038D", {"discharge summary", "pre-procedure evidence", "operative or procedure note",
                "post-procedure imaging"}),
])
def test_the_mandatory_categories_are_read_from_the_package_master(tools, code, labels):
    from crew.investigate import required_documents
    assert {r.label for r in required_documents(tools.hbp_lookup(code))} == labels


def test_a_claim_missing_its_histopathology_and_photograph_is_not_cleared(world, tools, tmp_path):
    """F-55/F-56: a same-day partial gastrectomy 'explained' by a LAMA form, with no specimen report and no photograph.
    The live crew asked for exactly these; the rules desk, checking three categories, released the claim."""
    s3 = world[0]["S3"].claim
    p = tools.hbp_lookup("SG003B")
    docs = [d for d in s3.documents if d.doc_type not in ("intra_procedure_photograph",)]
    claim = _at(s3, tools.registry_lookup(s3.hospital_ref), p, "CLM-F56-GASTRECTOMY").model_copy(
        update={"documents": docs, "discharge_ts": s3.discharge_ts, "submitted_ts": s3.submitted_ts})
    d = process(claim, triggers.ClaimStore([claim]), tools, out_dir=tmp_path)
    desk = d.findings[0]
    assert d.trigger_id == "T2" and d.action is Action.FIELD_AUDIT and desk.supports_fraud is None
    assert "histopathology report" in desk.conclusion and "clinical photograph" in desk.conclusion


def test_every_corpus_claim_files_every_category_its_package_requires(tools):
    """The evaluation's claims arrive complete, as the claim system requires: a stricter desk blocks none of them."""
    from datetime import datetime

    from crew.investigate import missing_mandatory
    from generate.corpus import CorpusBuilder
    admitted = datetime(2026, 1, 1)
    for p in tools.packages():
        docs = [Document(doc_type="discharge_summary", text=f"Discharge summary for {p.package_code}."),
                *CorpusBuilder._mandatory("CLM-F56", p)]
        claim = Claim(claim_id="CLM-F56", hospital_ref="HOSP-00001", district_code=1, package_code=p.package_code,
                      beneficiary_ref="BEN-F56", admission_ts=admitted, discharge_ts=admitted + timedelta(days=2),
                      amount_claimed=0, documents=docs, submitted_ts=admitted + timedelta(days=2, hours=5))
        assert missing_mandatory(claim, p) == [], p.package_code
