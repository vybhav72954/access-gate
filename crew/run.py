"""Orchestration entry point (docs/02-LLD.md §7).

    python -m crew.run --scenarios                            # the ten test scenarios, deterministic
    python -m crew.run --scenarios --llm --provider gemini    # with the crew on Gemini (demo)
    python -m crew.run --scenarios --llm --provider claude    # with the crew on Claude (final presentation)

Three properties this shape guarantees:
1. decide() is pure -- unit-testable with no LLM, no I/O -- and it decides on the crew path too, between the agents;
2. the degraded path is a real code path (NFR-1): a crew that fails before the policy decides falls back to the
   rules, and a decision the officer does not execute is executed with the template explanation;
3. side effects are isolated in crew/actions.py: the officer's action tool commits an action, and only
   crew/actions.py writes it.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from crew import actions, providers
from crew import investigate as rules_path
from crew.guardrails import scrub_identifiers
from crew.schemas import AGENT_TITLES, Action, Adequacy, AgentFinding, Channel, Claim, Decision, TriggerHit
from crew.tools import Tools
from rules import policy, triggers
from rules.policy import Verdict
from rules.triggers import ClaimStore

log = logging.getLogger("access_gate")


def intake(claim: Claim, tools: Tools):
    """Refuse rather than guess (NFR-6). A fabricated district silently corrupts the access gate."""
    problems: list[str] = []
    hospital = tools.registry_lookup(claim.hospital_ref)
    package = tools.hbp_lookup(claim.package_code)
    if hospital is None:
        problems.append("UNKNOWN_HOSPITAL")
    if package is None:
        problems.append("UNKNOWN_PACKAGE")
    if claim.discharge_ts is None:
        problems.append("MISSING_DISCHARGE")
    elif claim.discharge_ts < claim.admission_ts:
        # Otherwise los_days clamps to 0 and a data error reads as a zero-length-of-stay fraud trigger.
        problems.append("DISCHARGE_BEFORE_ADMISSION")
    elif claim.submitted_ts < claim.discharge_ts:
        problems.append("SUBMITTED_BEFORE_DISCHARGE")

    district = claim.district_code
    named = tools.resolve_district(claim.district_name) if claim.district_name else []
    if district is None:
        if len(named) > 1:
            problems.append("AMBIGUOUS_DISTRICT")
        elif not named:
            problems.append("UNKNOWN_DISTRICT")
        else:
            district = named[0]
    elif named and district not in named:
        # A code and a name that point at different districts: which one is wrong cannot be known, and the
        # access gate reads the district (F-40).
        problems.append("DISTRICT_NAME_CODE_MISMATCH")
    if hospital is not None and district is not None and district != hospital.district_code:
        problems.append("HOSPITAL_DISTRICT_MISMATCH")

    # Field evidence is one report per field channel. A "field" report on the desk channel, or a channel reported
    # twice, would be counted as independent evidence it is not -- three copies of one report outweighed a
    # contradicting one (F-41).
    channels = [r.channel for r in claim.field_reports]
    if Channel.DESK_AUDIT in channels:
        problems.append("FIELD_REPORT_ON_DESK_CHANNEL")
    if len(set(channels)) < len(channels):
        problems.append("DUPLICATE_FIELD_REPORT")
    return problems, hospital, package


def explain(v: Verdict, hit: TriggerHit | None, a: Adequacy | None, network: Sequence[Adequacy] = ()) -> str:
    """The template explanation. `a` is the billed specialty's adequacy; `network` every empanelled specialty's."""
    if v.action is Action.REFUSE:
        return "The claim cannot be processed as submitted: " + ", ".join(v.reason_codes) + ". Nothing was inferred."
    if v.action is Action.RELEASE_CLAIM:
        return "The evidence on file explains what the trigger flagged. The withheld claim is released."
    channels = ", ".join(c.value for c in v.field_channels)
    if v.action is Action.FIELD_AUDIT:
        if "FIELD_VERIFICATION_REQUIRED" in v.reason_codes:
            return (f"The documents point to an egregious finding under trigger {hit.trigger_id}, but for this trigger "
                    f"a desk finding alone does not suspend a hospital: at the desk it cannot be told apart from an "
                    f"honest clerical error. A field audit is ordered on: {channels}. The claim stays withheld meanwhile.")
        if "FIELD_EVIDENCE_INCOMPLETE" in v.reason_codes:
            return (f"The field evidence on file points to an egregious finding under trigger {hit.trigger_id}, but "
                    f"not every channel the trigger requires has reported. No single report may suspend a hospital, "
                    f"so a field audit is ordered on: {channels}. The claim stays withheld meanwhile.")
        if "BELOW_CONFIDENCE_FLOOR" in v.reason_codes:
            return (f"The evidence on file bears on trigger {hit.trigger_id} but falls below the confidence floor "
                    f"({policy.CONFIDENCE_FLOOR}). A field audit is ordered on: {channels}. The claim stays withheld "
                    "meanwhile.")
        return (f"The documents cannot settle trigger {hit.trigger_id}. A field audit is ordered on: {channels}. "
                "The claim stays withheld meanwhile.")
    if v.action is Action.NO_ACTION:
        if "DESK_INCONCLUSIVE" in v.reason_codes:
            ordered = ("every field channel it orders has already reported" if hit.field_channels
                       else "it orders no field channel")
            return (f"No evidence on file settles trigger {hit.trigger_id}, and {ordered}. No enforcement action is "
                    "taken; the case goes to a human.")
        return ("The evidence on file falls below the confidence floor "
                f"({policy.CONFIDENCE_FLOOR}). No enforcement action is taken; the case goes to a human.")
    if v.action is Action.SHOW_CAUSE:
        return (f"Trigger {hit.trigger_id} is confirmed at {v.confidence:.2f} confidence. The finding is not "
                "egregious, so the proportionate action is a show-cause notice with five days to respond.")
    ref = a or (network[0] if network else None)
    where = f"{ref.district}, {ref.state}" if ref else "the district"
    if v.action is Action.SUSPEND:
        km = f"{policy.DISTANCE_MATERIAL_KM:g} km"
        billed = ""
        if a is not None and not a.hospital_listed:
            billed = f" It is not empanelled for {a.specialty_name}, the specialty it billed."
        elif a is not None and a.n_providers > policy.SOLE_PROVIDER:
            billed = f" {where} has {a.n_providers} empanelled providers of {a.specialty_name}, the specialty it billed."
        sole = [x for x in network if x.n_providers <= policy.SOLE_PROVIDER and x.capability_ok]
        implausible = [x for x in network if x.n_providers <= policy.SOLE_PROVIDER and not x.capability_ok]
        caveats = ""
        if sole:
            caveats += (f" It is the district's only provider of {', '.join(x.specialty_name for x in sole)}, but another "
                        f"is within {km} of each (nearest: "
                        f"{', '.join(f'{x.nearest_alternative}, {x.km_to_alternative:.0f} km' for x in sole)}).")
        if implausible:
            caveats += (f" Its sole listings for {', '.join(x.specialty_name for x in implausible)} are implausible for "
                        "its facility tier, so they protect nothing.")
        scope = f"all {len(network)} specialties" if len(network) != 1 else "the one specialty"
        return (f"Egregious finding confirmed at {v.confidence:.2f}. Suspension takes the hospital out of the scheme for "
                f"{scope} it is empanelled for, and none would lose its only real provider beyond reach."
                f"{billed}{caveats} Suspended and referred to the State Empanelment Committee.")
    if v.action is Action.ESCALATE_SEC and v.gate == "unknown":
        why = ("the registry lists no specialties for this hospital (data defect D-4)" if a is not None
               else "network adequacy could not be computed for this district")
        return (f"Egregious finding confirmed at {v.confidence:.2f}, but {why}, so what suspending it would do to "
                "access in the district cannot be established. Not suspended: escalated to the State Empanelment "
                "Committee to decide with that information.")
    if v.action is Action.ESCALATE_SEC:
        stake = v.at_stake
        names = ", ".join(x.specialty_name for x in stake)
        return (f"Egregious finding confirmed at {v.confidence:.2f}, but in {where} - {ref.population:,} people"
                f"{', a NITI aspirational district' if ref.aspirational else ''} - this hospital is "
                f"{'; '.join(actions.stake_phrase(x) for x in stake)}. Suspension takes a hospital out of the scheme "
                f"for every specialty it is empanelled for, so it would remove cashless access to {names} there. "
                "Not suspended: escalated to the State Empanelment Committee.")
    if v.action is Action.DELIST_SPECIALTY:
        also = ""
        if v.at_stake:
            also = (" The referral removes that one listing. If the Committee also considered suspension, it would take "
                    f"the hospital out of every specialty, and in {where} it is "
                    f"{'; '.join(actions.stake_phrase(x) for x in v.at_stake)}.")
        return (f"Egregious finding confirmed at {v.confidence:.2f}. The hospital is the only listed provider of "
                f"{a.specialty_name} in {where}, but its capability flag reads: {a.capability_reason}. The access at "
                "stake may not exist. Referred for specialty de-listing and the district flagged as uncovered, pending "
                f"site verification.{also}")
    return ""


def process(claim: Claim, store: ClaimStore, tools: Tools, llm=None, out_dir: Path = actions.OUT,
            write: bool = True, clock: datetime | None = None) -> Decision:
    # Validate again: `model_copy(update=...)` builds a Claim without running a single validator, so a claim id
    # like "..\..\x" or an aware timestamp could otherwise reach the file system and the comparisons (F-37).
    claim = Claim.model_validate(claim.model_dump())
    problems, hospital, package = intake(claim, tools)
    hits: list[TriggerHit] = []
    hit = adequacy = verdict = case = None
    network: list[Adequacy] = []
    findings: list[AgentFinding] = []
    degraded = llm is None

    if not problems:
        hits = triggers.evaluate(claim, hospital, package, store, tools)
        hit = triggers.primary(hits)
        if hit is not None:
            billed = triggers.billed_specialty(package, hospital)
            adequacy = tools.district_adequacy(hospital, billed) if billed else None
            network = tools.hospital_network(hospital)          # what a suspension would remove: every specialty
            if llm is not None:
                from crew import crew_llm
                from crew.agent_tools import CaseFile
                case = CaseFile(claim=claim, hit=hit, hospital=hospital, package=package, store=store, tools=tools,
                                adequacy=adequacy, network=network)
                try:
                    crew_llm.run_case(case, llm)
                except Exception as exc:                       # noqa: BLE001 - any crew failure is handled below
                    stage = "before the policy decided: the rules investigate" if case.verdict is None else \
                        "after the policy decided: the decision stands"
                    log.warning("crew failed on %s %s (%s)", claim.claim_id, stage, exc)
                if case.verdict is not None:                   # the reviewer reported; the policy decided
                    findings, verdict = case.findings, case.verdict
                else:
                    degraded = True
            if verdict is None:
                findings = rules_path.investigate(claim, hit, hospital, package, store)

    if verdict is None:
        verdict = policy.decide(problems, hit, findings, adequacy, frozenset(r.channel for r in claim.field_reports),
                                network=network)

    # The officer executes the decision through its action tool; when it did not (a rules run, an outage, or an
    # officer that never acted), the orchestrator executes the same decision with the template explanation.
    text, acted_by, questions, requested = explain(verdict, hit, adequacy, network), "orchestrator", [], []
    committed = case.committed if case is not None and not degraded else None
    if committed is not None and committed.action is verdict.action:
        text, acted_by = committed.explanation, "enforcement_officer"
        questions, requested = list(committed.field_questions), list(committed.documents_requested)
    elif case is not None and not degraded:
        log.warning("the enforcement officer did not execute %s on %s; the orchestrator executes it with the template "
                    "explanation", verdict.action.value, claim.claim_id)

    # What the agents established counts only when their decision stands: a degraded case was decided by the rules.
    crewed = case is not None and not degraded
    decision = Decision(
        case_id=f"CASE-{claim.claim_id}", claim_id=claim.claim_id, hospital_ref=claim.hospital_ref,
        trigger_id=hit.trigger_id if hit else None, triggers_fired=[h.trigger_id for h in hits],
        severity=hit.severity if hit else None,
        channels_used=rules_path.channels_used(claim) if hit else [],
        field_channels_ordered=list(verdict.field_channels), findings=findings, conflicts=list(verdict.conflicts),
        adequacy=adequacy, access_at_stake=list(verdict.at_stake),
        gate=verdict.gate, action=verdict.action, claim_withheld=verdict.claim_withheld,
        reason_codes=list(verdict.reason_codes), confidence=verdict.confidence, degraded=degraded,
        human_required=verdict.human_required, explanation=text,
        # The model only when the crew actually ran on this case: refused and untriggered claims never reach it.
        model=(getattr(llm, "model_in_use", None) or getattr(llm, "model", None)) if llm is not None and hit else None,
        # Decision clock = the 24-hour trigger SLA after submission, so runs are reproducible (AC-7).
        decided_ts=clock or claim.submitted_ts + timedelta(hours=24),
        acted_by=acted_by, trail=list(case.trail) if case is not None else [],
        field_questions=questions, documents_requested=requested,
        agents=list(case.agents) if crewed else [],
        review=(scrub_identifiers(case.review.summary, claim.hospital_ref) or None) if crewed and case.review else None,
        disputed=list(case.disputed) if crewed else [],
        disputes_refused=list(case.disputes_refused) if crewed else [],
        opened_by_router=list(case.opened) if crewed else [],
        router_reasons=list(case.plan_reasons) if crewed else [],
        defence=(case.defence.explanation if crewed and case.defence is not None else None),
        defence_excluded=(case.defence.excluded if crewed and case.defence is not None else None),
        committee_brief=case.brief if crewed else None)       # the brief tool files one only on a referral

    if write:
        actions.check_log(out_dir)
        decision.artefact_path = actions.execute(decision, claim, hit, package, out_dir)
        actions.append_log(decision, out_dir)
    return decision


AGENT_LETTERS = {"case_router": "T", "desk_investigator": "D", "medical_auditor": "M", "billing_analyst": "B",
                 "field_evidence_analyst": "F", "provider_advocate": "A", "audit_reviewer": "R",
                 "committee_liaison": "C", "enforcement_officer": "O"}


def agent_letters(agents: Sequence[str]) -> str:
    """The agents that completed their tasks, one letter each, in the order they worked."""
    return "".join(AGENT_LETTERS.get(a, "?") for a in agents) or "-"


class OutageClient:
    """Stands in for the model API being unreachable (scenario S9): Anthropic and Gemini shapes alike."""

    class beta:
        class messages:
            @staticmethod
            def create(**_):
                raise ConnectionError("simulated API outage")

            parse = create

    class models:
        @staticmethod
        def generate_content(**_):
            raise ConnectionError("simulated API outage")


RUN_OUTPUTS = frozenset({"decision_log.csv", "field_audit_queue.jsonl", "artefacts"})
ARTEFACT_NAME = re.compile(rf"^(?:{'|'.join(a.value for a in Action)})_CASE-[A-Za-z0-9._-]+\.md$")


def clear_previous_run(out: Path) -> str | None:
    """Empty `out` of what an earlier run wrote. Returns why it refused, or None.

    `--out` can name any directory, so nothing is deleted unless everything in it is this program's own output:
    `--out results` once meant `shutil.rmtree(results)`, before the arguments were even checked.
    """
    if not out.exists():
        return None
    if not out.is_dir():
        return f"--out {out} exists and is not a directory"
    foreign = sorted(p.name for p in out.iterdir() if p.name not in RUN_OUTPUTS)
    artefacts = out / "artefacts"
    if artefacts.is_dir():   # the artefacts folder is emptied too, so it must hold nothing but artefacts
        foreign += sorted(f"artefacts/{p.name}" for p in artefacts.iterdir()
                          if not (p.is_file() and ARTEFACT_NAME.match(p.name)))
    if foreign:
        return (f"--out {out} holds files this program did not write ({', '.join(foreign[:5])}); "
                "name a new or empty directory")
    for p in out.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="The Access Gate")
    ap.add_argument("--scenarios", action="store_true", help="run the ten test scenarios")
    ap.add_argument("--llm", action="store_true", help="run the agent crew on a model (needs that provider's key)")
    ap.add_argument("--provider", choices=providers.PROVIDERS, default=None,
                    help="claude or gemini; default LLM_PROVIDER from .env, else claude")
    ap.add_argument("--only", default=None, help="comma-separated scenario ids to run, e.g. S2,S3")
    ap.add_argument("--out", default=str(actions.OUT))
    args = ap.parse_args()

    os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    for stream in (sys.stdout, sys.stderr):              # a Windows console cannot print every name a model writes
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # CrewAI's agent loop logs a failed model call as ERROR before re-raising it; process() reports the same failure,
    # with what it did about it, as one warning.
    logging.getLogger("crewai.flow.runtime").setLevel(logging.CRITICAL)

    if not args.scenarios:
        ap.error("nothing to do: pass --scenarios")
    if args.provider and not args.llm:
        # Without --llm the rules decide, whatever provider is named: a run meant for Claude would not use it (F-52).
        ap.error(f"--provider {args.provider} does nothing without --llm; add --llm to run the crew on it")

    llm = provider = None
    if args.llm:
        from dotenv import load_dotenv
        load_dotenv()
        provider = args.provider or providers.default_provider()
        if provider not in providers.PROVIDERS:
            ap.error(f"LLM_PROVIDER={provider!r} is not one of {', '.join(providers.PROVIDERS)}")
        key = providers.missing_key(provider)
        if key:
            ap.error(f"--llm on {provider} needs {key} in .env")
        try:
            llm = providers.make_llm(provider)
        except ValueError as exc:                       # a bad setting in .env, e.g. GEMINI_MAX_RPM=ten
            ap.error(str(exc))

    from generate.fixtures import build_scenarios
    tools = Tools()
    scenarios, store = build_scenarios(tools)
    if args.only is not None:
        wanted = [s.strip().upper() for s in args.only.split(",") if s.strip()]
        unknown = sorted(set(wanted) - {s.sid for s in scenarios})
        if unknown or not wanted:
            ap.error(f"unknown scenario ids: {', '.join(unknown)}" if unknown else "--only names no scenario")
        scenarios = [s for s in scenarios if s.sid in wanted]

    out = Path(args.out)
    refused = clear_previous_run(out)          # only once every argument is known to be good
    if refused:
        ap.error(refused)
    outage = None
    if llm is not None:
        outage = providers.make_llm(provider, OutageClient())
        print(f"crew: {provider} / {llm.model}")
        print()
    print(f"{'#':<4}{'scenario':<46}{'expected':<20}{'actual':<20}{'gate':<9}{'conf':>5}  deg  {'agents':<8}"
          f"{'acted by':<9}tools  ok")
    passed, fell_back, served, not_acted = 0, [], [], []
    for s in scenarios:
        use = outage if (s.simulate_llm_outage and llm is not None) else llm
        d = process(s.claim, store, tools, llm=use, out_dir=out)
        if d.model and not d.degraded and d.model not in served:
            served.append(d.model)
        # The outage scenario proves degradation, so on a model run it must actually degrade.
        ok = d.action is s.expected and (llm is None or not s.simulate_llm_outage or d.degraded)
        if llm is not None and not s.simulate_llm_outage and d.degraded:
            fell_back.append(s.sid)
        elif llm is not None and not d.degraded and d.trigger_id and d.acted_by != "enforcement_officer":
            not_acted.append(s.sid)
        passed += ok
        by = "officer" if d.acted_by == "enforcement_officer" else "rules" if d.degraded else "template"
        print(f"{s.sid:<4}{s.title[:45]:<46}{s.expected.value:<20}{d.action.value:<20}{(d.gate or '-'):<9}"
              f"{d.confidence:>5.2f}  {'yes' if d.degraded else 'no ':<4} {agent_letters(d.agents):<8}{by:<9}"
              f"{len(d.trail):>5}  "
              f"{'PASS' if ok else 'FAIL'}", flush=True)
    print(f"\n{passed}/{len(scenarios)} scenarios produced the expected action. Log: {out / 'decision_log.csv'}")
    if llm is not None:
        print("Agents: " + ", ".join(f"{AGENT_LETTERS.get(k, '?')} {title}" for k, title in AGENT_TITLES.items()))
    if served:
        # The header names the configured model; a rate-limited primary hands over to the backup mid-run, and the
        # recording should say which model actually decided the findings.
        print(f"The crew ran on: {', '.join(served)}")
    if not_acted:
        print(f"Note: the Enforcement Officer did not execute the decision on {', '.join(not_acted)}; the orchestrator "
              "executed the same decision with the template explanation (the log above says why).")
    if fell_back:
        # A bad key or a spent quota degrades every case to the rules, and every row can still say PASS. A run meant
        # to show the crew must not look like one that did (F-46).
        print(f"\nWARNING: {len(fell_back)} scenario(s) ran on the rules, not on {provider} ({', '.join(fell_back)}). "
              "Check the API key and quota (the log above says why) before presenting this run as the crew's.")
        return 2
    return 0 if passed == len(scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
