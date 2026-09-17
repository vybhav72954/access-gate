"""The agents' tools, called directly: what each returns, what each refuses, and what the trail records."""
import pytest

from crew import guardrails
from crew.agent_tools import (ACTION_TOOLS, MAX_DRAFTS, CaseFile, ToolRefusal, field_tools, investigator_tools,
                              liaison_tools, medical_tools, officer_tools, reviewer_tools)
from crew.investigate import investigate as rules_investigate
from crew.schemas import Action
from generate.benchmark import build_benchmark
from rules import policy, triggers
from tests.fakes import GOOD_BRIEF, GOOD_DRAFT


def case_file(tools, claim, store, decided=False):
    hospital, package = tools.registry_lookup(claim.hospital_ref), tools.hbp_lookup(claim.package_code)
    hit = triggers.primary(triggers.evaluate(claim, hospital, package, store, tools))
    billed = triggers.billed_specialty(package, hospital)
    case = CaseFile(claim=claim, hit=hit, hospital=hospital, package=package, store=store, tools=tools,
                    adequacy=tools.district_adequacy(hospital, billed), network=tools.hospital_network(hospital))
    if decided:
        case.findings = rules_investigate(claim, hit, hospital, package, store)
        case.verdict = policy.decide([], hit, case.findings, case.adequacy,
                                     frozenset(r.channel for r in claim.field_reports), network=case.network)
    return case


def tool(tools_list, name):
    return next(t for t in tools_list if t.name == name)


def test_the_history_lists_the_other_admissions_and_opens_them_to_reading(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S7"].claim, store)
    kit = investigator_tools(case)
    with pytest.raises(ToolRefusal, match="neither this claim"):
        tool(kit, "read_document").run(claim_id="CLM-S07P1", doc_type="clinical_notes")
    listed = tool(kit, "beneficiary_claim_history").run()
    assert "CLM-S07P1: admitted 17 May 2026 (24 days before this admission)" in listed and "CLM-S07P2" in listed
    assert "MG001A - Acute febrile illness" in listed and "documents: clinical_notes, discharge_summary" in listed
    text = tool(kit, "read_document").run(claim_id="clm-s07p1", doc_type="Clinical Notes")    # ids and types normalised
    assert "fever for two days with body ache" in text
    assert [(t.tool, t.refused) for t in case.trail] == [("read_document", True), ("beneficiary_claim_history", False),
                                                         ("read_document", False)]


def test_a_claim_outside_the_case_is_refused_whatever_it_is_called(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S7"].claim, store)
    kit = investigator_tools(case)
    tool(kit, "beneficiary_claim_history").run()
    for cid in ("CLM-S01", "CLM-S08P1", "../CLM-S07P1", ""):
        with pytest.raises(ToolRefusal):
            tool(kit, "read_document").run(claim_id=cid, doc_type="discharge_summary")
    with pytest.raises(ToolRefusal, match="has no operative_note"):
        tool(kit, "read_document").run(claim_id="CLM-S07P1", doc_type="operative_note")


def test_shared_documents_name_the_claim_and_whether_the_beneficiary_differs(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S5"].claim, store)
    text = tool(investigator_tools(case), "documents_shared_with_other_claims").run()
    assert "is also on CLM-S05A: a different beneficiary, BEN-20051" in text and "CLM-S05A" in case.related


def test_the_surgeon_tool_places_the_other_claim_without_naming_its_hospital(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S6"].claim, store)
    text = tool(investigator_tools(case), "surgeon_same_day_claims").run()
    assert "CLM-S06A" in text and "km from this hospital's district" in text
    assert not guardrails.OTHER_HOSPITAL.search(text)                       # EC-1: no other hospital's identifier


def test_compare_documents_shows_copied_records_for_what_they_are(tools):
    cases, store = build_benchmark(tools)
    copied = next(c for c in cases if c.case_id == "BCH-T7-01")
    distinct = next(c for c in cases if c.case_id == "BCH-T7-04")
    out = {}
    for c in (copied, distinct):
        case = case_file(tools, c.claim, store)
        kit = investigator_tools(case)
        tool(kit, "beneficiary_claim_history").run()
        out[c.case_id] = tool(kit, "compare_documents").run(claim_a=f"{c.case_id}P1", doc_type_a="clinical_notes",
                                                            claim_b=c.case_id, doc_type_b="clinical_notes")
    both = next(line for line in out["BCH-T7-01"].splitlines() if line.startswith("numbers in both:"))
    assert {"101.2", "98", "120/80", "97%", "14"} <= set(both.removeprefix("numbers in both: ").split(", "))
    similarity = {k: float(v.split("text similarity: ")[1][:4]) for k, v in out.items()}
    assert similarity["BCH-T7-01"] > 0.9 > similarity["BCH-T7-04"]


EVIDENCE_KITS = (investigator_tools, medical_tools, field_tools, reviewer_tools)


def test_no_evidence_tool_reveals_access_in_the_district(world, tools):
    """Blindness to adequacy is structural: nothing an agent reading the evidence can call says who would lose access."""
    scenarios, store = world
    case = case_file(tools, scenarios["S2"].claim, store)
    for kit in EVIDENCE_KITS:
        assert {t.name for t in kit(case)}.isdisjoint({"access_impact", "enforcement_decision", "file_committee_brief"})
    kit = investigator_tools(case)
    outputs = " ".join(tool(kit, n).run() for n in ("beneficiary_claim_history", "documents_shared_with_other_claims",
                                                    "surgeon_same_day_claims"))
    for word in ("provider", "aspirational", "population", "at stake"):
        assert word not in outputs.lower()


def test_each_kit_is_its_agents_and_the_trail_says_whose_call_it_was(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S7"].claim, store)
    names = {k.__name__: sorted(t.name for t in k(case)) for k in (medical_tools, field_tools, liaison_tools)}
    assert names == {"medical_tools": ["beneficiary_claim_history", "read_document"], "field_tools": ["read_document"],
                     "liaison_tools": ["access_impact", "enforcement_decision", "file_committee_brief"]}
    tool(medical_tools(case), "beneficiary_claim_history").run()
    tool(reviewer_tools(case), "read_document").run(claim_id="CLM-S07P1", doc_type="clinical_notes")
    assert [(c.agent, c.tool) for c in case.trail] == [("medical_auditor", "beneficiary_claim_history"),
                                                       ("audit_reviewer", "read_document")]
    # a listing opens the related claims to every agent on the case, but counts as examined only for the one that listed
    assert case.listed == {"medical_auditor": {"CLM-S07P1", "CLM-S07P2"}}


def test_an_action_tool_executes_only_the_policys_decision(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S2"].claim, store, decided=True)
    assert case.verdict.action is Action.ESCALATE_SEC
    kit = officer_tools(case)
    for t in kit[2:]:
        if t.action is not Action.ESCALATE_SEC:
            with pytest.raises(ToolRefusal, match="decided escalate_to_sec"):
                t.run(**{"explanation": GOOD_DRAFT, **({"documents_requested": ["The cath lab register"]}
                                                       if t.name == "issue_show_cause_notice" else {}),
                         **({"questions_for_field_team": ["Was the patient admitted?"]}
                            if t.name == "order_field_audit" else {})})
    assert case.committed is None
    assert "Executed" in tool(kit, "escalate_to_state_committee").run(explanation=GOOD_DRAFT)
    assert case.committed.action is Action.ESCALATE_SEC and case.committed.explanation == GOOD_DRAFT
    with pytest.raises(ToolRefusal, match="Already executed"):
        tool(kit, "escalate_to_state_committee").run(explanation=GOOD_DRAFT)
    assert len(ACTION_TOOLS) == len(Action) - 1                            # every action but refusal has its tool


def test_a_rejected_draft_is_counted_and_never_reaches_the_trail(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S2"].claim, store, decided=True)
    escalate = tool(officer_tools(case), "escalate_to_state_committee")
    bad = "HOSP-26104 has been suspended, leaving Bahraich without healthcare. " + GOOD_DRAFT
    for _ in range(MAX_DRAFTS):
        with pytest.raises(ToolRefusal, match="cannot be published"):
            escalate.run(explanation=bad)
    with pytest.raises(ToolRefusal, match="drafts have been rejected"):
        escalate.run(explanation=GOOD_DRAFT)
    recorded = " ".join(f"{t.arguments} {t.outcome}" for t in case.trail)
    assert "Bahraich without healthcare" not in recorded and "has been suspended" not in recorded
    assert case.trail[0].arguments == {"explanation": f"{len(bad.split())} words"}
    assert "draft rejected: overstates the access loss; says the hospital is suspended" in case.trail[0].outcome


def test_the_officer_reads_nothing_before_the_policy_has_decided(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S2"].claim, store)
    for t in officer_tools(case)[:2] + liaison_tools(case)[:2]:
        with pytest.raises(ToolRefusal, match="has not decided"):
            t.run()


def test_the_committee_brief_is_filed_once_only_on_a_referral_and_only_when_it_holds(world, tools):
    scenarios, store = world
    show_cause = case_file(tools, scenarios["S4"].claim, store, decided=True)
    assert show_cause.verdict.action is Action.SHOW_CAUSE
    with pytest.raises(ToolRefusal, match="refers nothing to the Committee"):
        tool(liaison_tools(show_cause), "file_committee_brief").run(**GOOD_BRIEF)

    case = case_file(tools, scenarios["S2"].claim, store, decided=True)
    brief = tool(liaison_tools(case), "file_committee_brief")
    bad = {**GOOD_BRIEF, "recommendation": "Suspend now: Bahraich would lose its only healthcare facility either way, "
                                           "so the access consequence changes nothing."}
    with pytest.raises(ToolRefusal, match="cannot be published"):
        brief.run(**bad)
    assert case.rejected_briefs == 1 and case.brief is None
    assert "only healthcare facility" not in " ".join(f"{c.arguments} {c.outcome}" for c in case.trail)
    assert "Filed" in brief.run(**GOOD_BRIEF)
    assert case.brief.summary == GOOD_BRIEF["summary"] and [o.option for o in case.brief.options] == [
        "Suspend with a transition window", "Recovery and penalty without suspension"]
    with pytest.raises(ToolRefusal, match="Already filed"):
        brief.run(**GOOD_BRIEF)
    assert case.trail[-1].arguments == {"summary": f"{len(GOOD_BRIEF['summary'].split())} words",
                                        "options": "2 option(s)",
                                        "recommendation": f"{len(GOOD_BRIEF['recommendation'].split())} words"}


def test_briefs_rejected_too_often_stop_being_accepted(world, tools):
    scenarios, store = world
    case = case_file(tools, scenarios["S2"].claim, store, decided=True)
    brief = tool(liaison_tools(case), "file_committee_brief")
    for _ in range(MAX_DRAFTS):
        with pytest.raises(ToolRefusal, match="cannot be published"):
            brief.run(**{**GOOD_BRIEF, "question": "Suspended?"})
    with pytest.raises(ToolRefusal, match="briefs have been rejected"):
        brief.run(**GOOD_BRIEF)
    assert case.brief is None


def test_arguments_that_do_not_fit_a_tool_are_recorded_as_refused(world, tools):
    """Benchmark BCH-R2-04, live: an officer kept sending malformed action calls, and the trail showed none of them."""
    scenarios, store = world
    case = case_file(tools, scenarios["S4"].claim, store, decided=True)
    notice = tool(officer_tools(case), "issue_show_cause_notice")
    with pytest.raises(ValueError, match="arguments validation failed"):
        notice.run(explanation=GOOD_DRAFT, documents_requested="the cath lab register")     # a string, not a list
    assert case.trail[-1].refused and "arguments do not fit" in case.trail[-1].outcome
    assert case.trail[-1].arguments["documents_requested"] == "a str, not a list" and case.committed is None


def test_no_tool_answers_from_a_cache(world, tools):
    """A cached answer skipped the action checks and hid an agent calling one tool again and again."""
    scenarios, store = world
    case = case_file(tools, scenarios["S2"].claim, store, decided=True)
    kit = [t for k in (*EVIDENCE_KITS, liaison_tools, officer_tools) for t in k(case)]
    assert all(t.cache_function({}, "result") is False for t in kit)
