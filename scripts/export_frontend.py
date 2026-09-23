"""Export the decided run and the published results as static JSON for the front end.

    python -m scripts.export_frontend --out frontend/static/data

Nothing here decides anything. It reads what the pipeline already wrote -- the decision log, the
artefacts it issued, the field-audit queue and the committed evaluation results -- and re-serialises
it. Where a field exists only in the artefact Markdown (the Audit Reviewer's summary, the Provider
Advocate's answer, the investigation trail, the nearest alternative provider), it is parsed back out
of the artefact rather than recomputed: the artefact is the record, and a second derivation of a
number is a second chance to disagree with it.

Standard library only, so it runs in the same environment as the tests with nothing installed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
REFERENCE = ROOT / "data" / "reference"

CONFIDENCE_FLOOR = 0.70          # rules/policy.py CONFIDENCE_FLOOR -- shown beside every confidence
DISTANCE_MATERIAL_KM = 50.0      # rules/policy.py DISTANCE_MATERIAL_KM

# The nine agents in the order they work a case (crew/schemas.py AGENT_TITLES).
AGENT_TITLES = {
    "case_router": "Case Router",
    "desk_investigator": "Desk Investigator",
    "billing_analyst": "Billing & Tariff Analyst",
    "provider_advocate": "Provider Advocate",
    "medical_auditor": "Medical Auditor",
    "field_evidence_analyst": "Field Evidence Analyst",
    "audit_reviewer": "Audit Reviewer",
    "committee_liaison": "Committee Liaison",
    "enforcement_officer": "Enforcement Officer",
}

AGENT_OWNS = {
    "case_router": "which optional investigations this file raises; holds no tools by design",
    "desk_investigator": "are the documents present, consistent, genuine, uncopied",
    "billing_analyst": "is the amount claimed accounted for",
    "provider_advocate": "what innocent account the file bears, and whether evidence excludes it",
    "medical_auditor": "was the care clinically needed",
    "field_evidence_analyst": "what each field report is worth",
    "audit_reviewer": "does each reading hold against what it cites",
    "committee_liaison": "what the Committee must decide, and on what facts",
    "enforcement_officer": "executes the decided action and explains it",
}

# The optional tracks the Case Router may open (crew/crew_llm.py TRACKS) -> the agent that runs them.
TRACK_AGENT = {
    "medical": "medical_auditor",
    "field": "field_evidence_analyst",
    "billing": "billing_analyst",
    "advocate": "provider_advocate",
}

# A finding is named for the reading, not the agent (crew/crew_llm.py FINDING_NAMES). This maps a
# reading back to the agent that owns it, so a finding card can be attributed.
READING_AGENT = {
    "desk_audit": "desk_investigator",
    "medical_audit": "medical_auditor",
    "billing_audit": "billing_analyst",
    "advocacy": "provider_advocate",
    "field_review": "field_evidence_analyst",
    "registry_verification": "desk_investigator",
}

READING_TITLES = {
    "desk_audit": "Desk audit",
    "medical_audit": "Medical audit",
    "billing_audit": "Billing audit",
    "advocacy": "Advocacy",
    "field_review": "Field review",
    "registry_verification": "Registry verification",
}

ACTION_GROUP = {
    "release_claim": "released",
    "order_field_audit": "deferred",
    "no_action_review": "deferred",
    "show_cause_notice": "enforced",
    "suspend_hospital": "enforced",
    "escalate_to_sec": "enforced",
    "delist_specialty": "enforced",
    "refuse_malformed": "refused",
}

ACTION_LABELS = {
    "release_claim": "Claim released",
    "order_field_audit": "Field audit ordered",
    "no_action_review": "Referred for human review",
    "show_cause_notice": "Show-cause notice",
    "suspend_hospital": "Hospital suspended",
    "escalate_to_sec": "Escalated to the Committee",
    "delist_specialty": "Specialty de-listing referral",
    "refuse_malformed": "Refused: malformed claim",
}

GATE_LABELS = {
    "clear": "Clear",
    "protect": "Protected",
    "phantom": "Phantom provider",
    "unknown": "Unknown",
}

# Caveats the source docs attach to each figure. Quoted, not paraphrased, so the page cannot overstate
# what the evaluation measured (docs/09-EVALUATION.md, docs/10-AGENT-EVALUATION.md).
CAVEATS = {
    "corpus": ("Corpus: 5,000 flagged cases, seed 20260916. Every case ran through the real pipeline "
               "(crew.run.process, rules path); cases ordered to the field re-entered with simulated "
               "field reports."),
    "not_agents": ("What this does not measure: the agents. The corpus documents are neutral by design, so "
                   "every desk reading here is the rules path's. They measure the policy and the access "
                   "gate, which decide identically on both paths."),
    "threshold": ("The left columns take every real hospital that is its district's only provider of some "
                  "specialty, or one of two in a NITI aspirational district, and ask what the gate would do "
                  "if it were confirmed to have committed egregious fraud in a specialty it can plausibly "
                  "deliver - no simulation. The operating point is a policy choice, not a statistical one."),
    "sensitivity": "One assumption varied at a time; everything else at baseline; same seed.",
    "ablation": ("Each row is the same 5,000-case corpus with one safeguard removed. Wrongful suspensions are "
                 "most sensitive to field_accuracy: the safety of automatic suspension rests on the quality of "
                 "field verification more than on anything in this code."),
    "disparate_impact": ("Outcomes by hospital type. Sole-provider share differs sharply by type, so the gate "
                         "does not fall evenly across them."),
    "benchmark": ("40 hand-written cases (20 fraud, 20 innocent) whose truth is stated in the documents, run "
                  "through both paths of the same pipeline, with the same policy deciding. The cases were "
                  "written to probe where free text defeats patterns, so the rules' score here is not their "
                  "error rate in production. The team that built the crew wrote the cases: a bias disclosed "
                  "here. Forty cases measure direction and failure modes, not rates."),
}

LIMITATIONS = [
    "T6 is assumed never to fire innocently. A clerical mix-up that filed one patient's discharge summary under another reads as document reuse.",
    "Uniform trigger mixes. NHA publishes no breakdown of fraud or false flags by trigger.",
    "Field reports are modelled, not observed. Their confidence relative to the 0.7 floor sets the human-review share almost directly.",
    "Frauds the triggers never flag (10% by assumption) are outside this system and these numbers.",
    "Claim-level, not hospital-level. Repeat offenders collapse into single hospital cases in practice.",
    "R2's desk rule accepts an implant invoice as the explanation for any package.",
]


# ── small helpers ────────────────────────────────────────────────────────────

def pipes(value: str) -> list[str]:
    """A pipe-separated multi-value column. An empty cell is an empty list, never [\"\"]."""
    return [part for part in (value or "").split("|") if part.strip()]


def as_bool(value: str) -> bool | None:
    text = (value or "").strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    return None


def as_int(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def as_float(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def titleise(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


# ── the artefact ─────────────────────────────────────────────────────────────

HEADING = re.compile(r"^## (.+)$", re.M)
FINDING_LINE = re.compile(
    r"^- \*\*(?P<agent>[^*]+?)\*\* — (?P<stance>supports|opposes|inconclusive) "
    r"\((?P<confidence>[\d.]+)\): (?P<conclusion>.*?)(?: \*\[(?P<citation>.*?)\]\*)?$")
TRIGGER_LINE = re.compile(r"^\*\*(?P<id>[A-Z]\d+) — (?P<name>.+?)\*\* \(severity (?P<severity>\d)\)\.\s*(?P<evidence>.*)$")
# Published package names carry their own parentheses ("Bronchial artery Embolisation (for Haemoptysis)"),
# so the name is greedy and only the final bracket is the rate.
PACKAGE_LINE = re.compile(r"^\*\*Package\*\* (?P<code>\S+) — (?P<name>.+) \((?P<rate>[^;()]+); claimed Rs (?P<claimed>[\d,]+)\)$")
TABLE_ROW = re.compile(r"^\|(.+)\|$")
# `crew/actions.py` flattens the nearest alternative to prose: "Gonda, 83 km away" when a patient
# would have to travel, "in the district (N other providers)" when they would not. The district name
# is not in the decision log, and only the first form names one, so it is recovered here -- the
# viewer needs it to draw the journey the gate is weighing. `test_export_frontend.py` pins both
# forms against the renderer that writes them.
NEAREST_DISTRICT = re.compile(r"^(?P<district>[^|]+?),\s*(?P<km>[\d.]+)\s*km away$")
TRAIL_ROW = re.compile(r"^\| (?P<n>\d+) \| (?P<agent>[^|]+?) \| `(?P<tool>[^`]+)` \| (?P<args>[^|]*?) \| (?P<result>[^|]*?) \|$")


def split_sections(markdown: str) -> tuple[str, dict[str, str]]:
    """The preamble before the first `## `, and each `## ` section by its heading."""
    matches = list(HEADING.finditer(markdown))
    if not matches:
        return markdown, {}
    preamble = markdown[: matches[0].start()]
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        sections[match.group(1).strip()] = markdown[match.end():end].strip()
    return preamble, sections


def table_rows(block: str) -> list[list[str]]:
    """Body rows of every Markdown table in the block, separators and headers dropped."""
    rows: list[list[str]] = []
    for line in block.splitlines():
        match = TABLE_ROW.match(line.strip())
        if not match:
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            rows.append([])          # separator: marks the header above it
            continue
        rows.append(cells)
    out, seen_separator = [], False
    for row in rows:
        if not row:
            seen_separator = True
            continue
        if seen_separator:
            out.append(row)
    return out


def nearest_district(text: str | None) -> str | None:
    """The bare district out of "Gonda, 83 km away", or None when nothing is named.

    "in the district (100 other providers)" names no other district because there is no journey to
    make, and that is a None here rather than a missing value."""
    if not text:
        return None
    match = NEAREST_DISTRICT.match(strip_bold(text).strip())
    return match.group("district").strip() if match else None


def strip_bold(text: str) -> str:
    return text.replace("**", "").strip()


def parse_findings(block: str) -> tuple[list[dict], list[str]]:
    findings, conflicts = [], []
    for line in block.splitlines():
        line = line.strip()
        match = FINDING_LINE.match(line)
        if match:
            agent = match.group("agent")
            reading, _, channel = agent.partition(" / ")
            reading = reading.strip()
            findings.append({
                "reading": reading,
                "reading_title": READING_TITLES.get(reading, titleise(reading)),
                "agent_key": READING_AGENT.get(reading),
                "agent_title": AGENT_TITLES.get(READING_AGENT.get(reading, ""), ""),
                "channel": channel.strip() or None,
                "stance": match.group("stance"),
                "confidence": float(match.group("confidence")),
                "conclusion": match.group("conclusion").strip(),
                "citation": (match.group("citation") or "").strip() or None,
            })
        elif line.startswith("**Conflicts:**"):
            conflicts = [c.strip() for c in line[len("**Conflicts:**"):].split(";") if c.strip()]
    return findings, conflicts


def parse_access(block: str) -> tuple[dict, list[dict]]:
    """The billed specialty's row table, then the specialties-at-stake table."""
    detail: dict[str, str] = {}
    for line in block.splitlines():
        match = TABLE_ROW.match(line.strip())
        if not match:
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if len(cells) == 2 and cells[1] and not all(set(c) <= set("-: ") for c in cells):
            detail.setdefault(strip_bold(cells[0]), strip_bold(cells[1]))
    at_stake = []
    for row in table_rows(block):
        if len(row) == 4 and row[0] not in ("Specialty at stake",) and "Billed specialty" not in row[0]:
            at_stake.append({
                "specialty_name": strip_bold(row[0]),
                "n_providers": as_int(strip_bold(row[1])),
                "nearest_alternative": strip_bold(row[2]),
                "nearest_alternative_district": nearest_district(row[2]),
                "why_protected": strip_bold(row[3]),
            })
    return detail, at_stake


def parse_trail(block: str) -> list[dict]:
    trail = []
    for line in block.splitlines():
        match = TRAIL_ROW.match(line.strip())
        if not match:
            continue
        result = match.group("result").strip()
        refused = result.startswith("**refused**")
        if refused:
            result = result[len("**refused** -"):].strip()
        arguments = []
        for pair in match.group("args").split(";"):
            key, sep, value = pair.partition("=")
            if sep:
                arguments.append({"name": key.strip(), "value": value.strip()})
        title = match.group("agent").strip()
        key = next((k for k, v in AGENT_TITLES.items() if v == title), None)
        trail.append({
            "n": int(match.group("n")),
            "agent_key": key,
            "agent_title": title,
            "tool": match.group("tool"),
            "arguments": arguments,
            "outcome": result.replace("\\|", "|"),
            "refused": refused,
        })
    return trail


def parse_review(block: str) -> dict:
    """The reviewer's disputes, the disputes that were refused, and its summary."""
    disputed, refused, summary = [], [], None
    for line in [ln.strip() for ln in block.splitlines() if ln.strip()]:
        if line.startswith("Disputed, and the dispute refused:"):
            names = line[len("Disputed, and the dispute refused:"):].split(".")[0]
            refused = [n.strip() for n in names.split(",") if n.strip()]
        elif line.startswith("Disputed, and so set aside:"):
            names = line[len("Disputed, and so set aside:"):].split(".")[0]
            disputed = [n.strip() for n in names.split(",") if n.strip()]
        elif line.startswith("No finding disputed."):
            continue
        elif not line.startswith("A disputed finding weighs nothing") and not line.startswith(
                "The reading rests on a comparison"):
            summary = line if summary is None else f"{summary} {line}"
    return {"disputed": disputed, "disputes_refused": refused, "summary": summary}


def parse_defence(block: str) -> dict | None:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return None
    excluded = None
    explanation = []
    for line in lines:
        if line.startswith("The evidence on file excludes it."):
            excluded = True
        elif line.startswith("The evidence on file does not exclude it"):
            excluded = False
        else:
            explanation.append(line)
    return {"explanation": " ".join(explanation).strip() or None, "excluded": excluded}


def parse_brief(block: str) -> dict | None:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return None
    summary, recommendation, question = None, None, None
    for line in lines:
        if line.startswith("**Recommendation.**"):
            recommendation = line[len("**Recommendation.**"):].strip()
        elif line.startswith("**Question for the Committee.**"):
            question = line[len("**Question for the Committee.**"):].strip()
        elif line.startswith("*Prepared by") or line.startswith("|"):
            continue
        elif summary is None:
            summary = line
    options = [{"option": row[0], "consequence": row[1]} for row in table_rows(block) if len(row) == 2]
    return {"summary": summary, "options": options, "recommendation": recommendation, "question": question}


def parse_router(block: str) -> list[dict]:
    opened = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        track, sep, reason = body.partition(":")
        track = track.strip().lower()
        if sep and track in TRACK_AGENT:
            opened.append({"track": track, "agent_key": TRACK_AGENT[track], "reason": reason.strip()})
        else:
            opened.append({"track": None, "agent_key": None, "reason": body})
    return opened


def bullets(block: str) -> list[str]:
    return [ln.strip()[2:].strip() for ln in block.splitlines() if ln.strip().startswith("- ")]


def paragraphs(block: str) -> str | None:
    text = " ".join(ln.strip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("|"))
    return text or None


def parse_artefact(markdown: str) -> dict:
    preamble, sections = split_sections(markdown)
    parsed: dict = {
        "title": None, "mode": None, "trigger": None, "package": None,
        "findings": [], "conflicts": [], "router_opened": [], "defence": None,
        "review": {"disputed": [], "disputes_refused": [], "summary": None},
        "trail": [], "access_detail": {}, "at_stake": [],
        "committee_question": None, "brief": None, "explanation": None,
        "channels_ordered": [], "field_questions": [], "field_checklist": [],
        "documents_requested": [],
    }
    for line in preamble.splitlines():
        line = line.strip()
        if line.startswith("# "):
            parsed["title"] = line[2:].strip()
        elif "**Mode**" in line:
            parsed["mode"] = line.split("**Mode**")[-1].strip()

    for heading, block in sections.items():
        if heading == "Trigger":
            for line in block.splitlines():
                line = line.strip()
                match = TRIGGER_LINE.match(line)
                if match:
                    parsed["trigger"] = {
                        "trigger_id": match.group("id"),
                        "name": match.group("name").strip(),
                        "severity": int(match.group("severity")),
                        "evidence": match.group("evidence").strip().rstrip("."),
                        "source": None,
                    }
                elif line.startswith("*Source:") and parsed["trigger"]:
                    parsed["trigger"]["source"] = line.strip("*").replace("Source:", "").strip()
                else:
                    pkg = PACKAGE_LINE.match(line)
                    if pkg:
                        parsed["package"] = {
                            "code": pkg.group("code"),
                            "name": pkg.group("name").strip(),
                            "published_rate": pkg.group("rate").strip(),
                            "claimed_rs": as_int(pkg.group("claimed").replace(",", "")),
                        }
        elif heading == "Findings":
            parsed["findings"], parsed["conflicts"] = parse_findings(block)
        elif heading == "Work the Case Router opened":
            parsed["router_opened"] = parse_router(block)
        elif heading.startswith("The hospital's side"):
            parsed["defence"] = parse_defence(block)
        elif heading == "Audit review":
            parsed["review"] = parse_review(block)
        elif heading == "Investigation trail":
            parsed["trail"] = parse_trail(block)
        elif heading == "Access impact":
            parsed["access_detail"], parsed["at_stake"] = parse_access(block)
        elif heading == "Question for the Committee":
            parsed["committee_question"] = paragraphs(block)
        elif heading.startswith("Brief for the State"):
            parsed["brief"] = parse_brief(block)
        elif heading == "Explanation":
            parsed["explanation"] = paragraphs(block)
        elif heading == "Channels ordered":
            parsed["channels_ordered"] = bullets(block)
        elif heading.startswith("What the field team must establish"):
            parsed["field_questions"] = bullets(block)
        elif heading.startswith("What the field team checks"):
            parsed["field_checklist"] = bullets(block)
        elif heading == "Required response":
            parsed["documents_requested"] = bullets(block)
    return parsed


# ── the roster ───────────────────────────────────────────────────────────────

def build_roster(agents: list[str], router_opened: list[dict]) -> list[dict]:
    """The nine agents: which ran, in what order, and which the Router opened rather than a rule.

    The Router may only widen the mandatory set, so an agent that ran and was not opened by the Router
    was mandatory. Agents that did not run stay in the list, marked `ran: false` -- that a Medical
    Auditor was not needed on a case is information, not an absence to hide.
    """
    opened = {entry["agent_key"]: entry.get("reason") for entry in router_opened if entry.get("agent_key")}
    order = {key: i for i, key in enumerate(agents)}
    roster = []
    for key, title in AGENT_TITLES.items():
        ran = key in order
        roster.append({
            "key": key,
            "title": title,
            "owns": AGENT_OWNS[key],
            "ran": ran,
            "order": order.get(key),
            "mandatory": ran and key not in opened,
            "opened_by_router": key in opened,
            "router_reason": opened.get(key),
        })
    return roster


# ── cases ────────────────────────────────────────────────────────────────────

def load_trigger_reference() -> dict[str, dict]:
    path = REFERENCE / "triggers.json"
    if not path.exists():
        return {}
    reference = {}
    for entry in json.loads(path.read_text(encoding="utf-8")):
        key = f"T{entry['trigger_id']}" if isinstance(entry.get("trigger_id"), int) else str(entry.get("trigger_id"))
        reference[key] = {
            "name": entry.get("name"),
            "severity": entry.get("severity"),
            "channels": entry.get("channels", []),
            "source": entry.get("source"),
            "checklist": entry.get("checklist", []),
        }
    return reference


def build_cases(run_dir: Path) -> list[dict]:
    log = run_dir / "decision_log.csv"
    if not log.exists():
        raise SystemExit(
            f"no decision log at {log}.\n"
            "Generate a run first:\n"
            "    python -m crew.run --scenarios --out out_live                        # deterministic, no key\n"
            "    python -m crew.run --scenarios --llm --provider gemini --out out_live  # with the crew")

    queue: dict[str, dict] = {}
    queue_path = run_dir / "field_audit_queue.jsonl"
    if queue_path.exists():
        for line in queue_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                queue[entry["case_id"]] = entry

    reference = load_trigger_reference()
    cases = []
    with log.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            artefact_path = (row.get("artefact_path") or "").strip()
            artefact_text = None
            if artefact_path:
                candidate = run_dir / artefact_path
                if candidate.exists():
                    artefact_text = candidate.read_text(encoding="utf-8")
            parsed = parse_artefact(artefact_text) if artefact_text else parse_artefact("")

            trigger_id = (row.get("trigger_id") or "").strip() or None
            trigger = parsed["trigger"] or (
                {"trigger_id": trigger_id,
                 "name": reference.get(trigger_id, {}).get("name"),
                 "severity": as_int(row.get("severity", "")),
                 "evidence": None,
                 "source": reference.get(trigger_id, {}).get("source")} if trigger_id else None)
            if trigger and not trigger.get("name") and trigger_id in reference:
                trigger["name"] = reference[trigger_id]["name"]

            agents = pipes(row.get("agents", ""))
            disputed = pipes(row.get("disputed", "")) or parsed["review"]["disputed"]
            disputes_refused = parsed["review"]["disputes_refused"]

            findings = []
            for finding in parsed["findings"]:
                finding = dict(finding)
                finding["disputed"] = finding["reading"] in disputed
                finding["dispute_refused"] = finding["reading"] in disputes_refused
                findings.append(finding)

            district_state = (row.get("district") or "").strip()
            district, _, state = district_state.partition(",")
            detail = parsed["access_detail"]
            specialty = (row.get("specialty") or "").strip() or None
            capability_flag = detail.get("Capability flag", "")
            capability_reason = capability_flag.split(" - ", 1)[1] if " - " in capability_flag else None

            access = None
            if district_state or specialty:
                access = {
                    "district": district.strip() or None,
                    "state": state.strip() or None,
                    "specialty": specialty,
                    "specialty_name": detail.get("Specialty") or (titleise(specialty) if specialty else None),
                    "hospital_listed": {"Yes": True, "No": False}.get(detail.get("Hospital empanelled for it")),
                    "n_providers": as_int(row.get("n_providers", "")),
                    "km_to_alternative": as_float(row.get("km_to_alternative", "")),
                    "nearest_alternative": detail.get("Nearest other provider"),
                    "nearest_alternative_district": nearest_district(detail.get("Nearest other provider")),
                    "population": as_int(row.get("population", "")),
                    "aspirational": as_bool(row.get("aspirational", "")),
                    "capability_ok": as_bool(row.get("capability_ok", "")),
                    "capability_reason": capability_reason,
                }

            action = (row.get("action") or "").strip()
            gate = (row.get("gate") or "").strip() or None
            case_id = row["case_id"]
            confidence = as_float(row.get("confidence", "")) or 0.0

            cases.append({
                "case_id": case_id,
                "claim_id": row["claim_id"],
                "hospital_ref": row["hospital_ref"],
                "trigger_id": trigger_id,
                "trigger": trigger,
                "triggers_fired": pipes(row.get("triggers_fired", "")),
                "severity": as_int(row.get("severity", "")),
                "package": parsed["package"],
                "action": action,
                "action_label": ACTION_LABELS.get(action, titleise(action)),
                "action_group": ACTION_GROUP.get(action, "refused"),
                "claim_withheld": bool(as_bool(row.get("claim_withheld", ""))),
                "human_required": bool(as_bool(row.get("human_required", ""))),
                "gate": gate,
                "gate_label": GATE_LABELS.get(gate) if gate else None,
                "confidence": confidence,
                "below_floor": confidence < CONFIDENCE_FLOOR,
                "degraded": bool(as_bool(row.get("degraded", ""))),
                "channels_used": pipes(row.get("channels_used", "")),
                "field_channels_ordered": pipes(row.get("field_channels_ordered", "")),
                "access": access,
                "specialties_at_stake": pipes(row.get("specialties_at_stake", "")),
                "at_stake": parsed["at_stake"],
                "reason_codes": pipes(row.get("reason_codes", "")),
                "conflicts": [c.strip() for c in (row.get("conflicts") or "").split(";") if c.strip()],
                "findings": findings,
                "agents": agents,
                "roster": build_roster(agents, parsed["router_opened"]),
                "router_opened": parsed["router_opened"],
                "defence": parsed["defence"],
                "review": parsed["review"]["summary"],
                "disputed": disputed,
                "disputes_refused": disputes_refused,
                "trail": parsed["trail"],
                "tools_called": pipes(row.get("tools_called", "")),
                "committee_question": parsed["committee_question"],
                "brief": parsed["brief"],
                "explanation": parsed["explanation"],
                "channels_ordered": parsed["channels_ordered"],
                "field_questions": parsed["field_questions"],
                "field_checklist": parsed["field_checklist"],
                "documents_requested": parsed["documents_requested"],
                "field_audit_queue": queue.get(case_id, {}).get("channels", []),
                "artefact_path": artefact_path or None,
                "artefact_title": parsed["title"],
                "artefact": artefact_text,
                "mode": parsed["mode"],
                "decided_ts": (row.get("decided_ts") or "").strip() or None,
                "model": (row.get("model") or "").strip() or None,
                "acted_by": (row.get("acted_by") or "").strip() or None,
            })
    return cases


# ── evaluation and benchmark ─────────────────────────────────────────────────

def read_csv_rows(path: Path) -> list[dict]:
    """Every row typed: numbers as numbers, blanks as null, so the front end never parses a string."""
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            typed: dict[str, object] = {}
            for key, value in row.items():
                text = (value or "").strip()
                if text == "" or text == "-":
                    typed[key] = None if text == "" else text
                    continue
                try:
                    number = float(text)
                except ValueError:
                    typed[key] = text
                else:
                    typed[key] = int(number) if number.is_integer() and "." not in text else number
            rows.append(typed)
    return rows


def build_evaluation() -> dict:
    metrics_path = RESULTS / "metrics.json"
    if not metrics_path.exists():
        raise SystemExit(f"missing {metrics_path}; results/ must be committed")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    return {
        "config": metrics.get("config", {}),
        "summary": metrics.get("summary", {}),
        "gate_at_chosen_km": metrics.get("gate_at_chosen_km", {}),
        "distance_material_km": metrics.get("distance_material_km", DISTANCE_MATERIAL_KM),
        "confidence_floor": CONFIDENCE_FLOOR,
        "by_trigger": metrics.get("by_trigger", []),
        "by_state": metrics.get("by_state", []),
        "allocation_fit": metrics.get("allocation_fit", []),
        "threshold_sweep": read_csv_rows(RESULTS / "threshold_sweep.csv"),
        "sensitivity": read_csv_rows(RESULTS / "sensitivity.csv"),
        "ablation": read_csv_rows(RESULTS / "ablation.csv"),
        "disparate_impact": read_csv_rows(RESULTS / "disparate_impact.csv"),
        "caveats": CAVEATS,
        "limitations": LIMITATIONS,
    }


SERIES_CSV = {
    "rules": "agent_benchmark_rules.csv",
    "gemini": "agent_benchmark_gemini.csv",
    "gemini_first_run": "agent_benchmark_gemini_first_run.csv",
}

BENCHMARK_CASE_FIELDS = (
    "case_id", "trigger", "truth", "reading", "action", "score", "confidence", "degraded",
    "acted_by", "model", "agents", "disputed", "disputes_refused", "opened_by_router",
    "desk_stance", "desk_confidence", "desk_conclusion", "medical_stance", "medical_confidence",
    "medical_conclusion", "billing_stance", "billing_confidence", "billing_conclusion",
    "defence", "defence_excluded", "review", "refused_calls", "llm_calls", "seconds",
)


def build_benchmark() -> dict:
    path = RESULTS / "agent_benchmark.json"
    if not path.exists():
        raise SystemExit(f"missing {path}; results/ must be committed")
    benchmark = json.loads(path.read_text(encoding="utf-8"))
    series = {}
    for key, entry in benchmark.get("series", {}).items():
        rows = []
        for row in read_csv_rows(RESULTS / SERIES_CSV.get(key, "")):
            case = {field: row.get(field) for field in BENCHMARK_CASE_FIELDS}
            case["degraded"] = str(case.get("degraded")).strip().lower() == "true"
            case["agents"] = pipes(str(case.get("agents") or ""))
            case["disputed"] = pipes(str(case.get("disputed") or ""))
            case["disputes_refused"] = pipes(str(case.get("disputes_refused") or ""))
            case["opened_by_router"] = pipes(str(case.get("opened_by_router") or ""))
            excluded = case.get("defence_excluded")
            case["defence_excluded"] = as_bool(str(excluded)) if excluded is not None else None
            rows.append(case)
        series[key] = {
            "key": key,
            "label": entry.get("label", key),
            "n_cases": entry.get("cases"),
            "scored": entry.get("scored"),
            "overall": entry.get("overall", {}),
            "by_reading": entry.get("by_reading", {}),
            "by_trigger": entry.get("by_trigger", {}),
            "crew": entry.get("crew"),
            "cases": rows,
        }
    return {
        "cases": benchmark.get("cases"),
        "fraud": benchmark.get("fraud"),
        "readings": ["keyword", "paraphrase", "trap", "mislabelled", "contradiction", "cross-claim"],
        "series": series,
        "caveat": CAVEATS["benchmark"],
    }


# ── typescript ───────────────────────────────────────────────────────────────

TYPES_TS = '''// Generated by scripts/export_frontend.py. Do not edit by hand.
// Re-run the export after any change to the decision log or the results, and keep this file with it:
// a mismatch between the export and these interfaces is the most likely way the build breaks.

export const CONFIDENCE_FLOOR = %(floor)s;
export const DISTANCE_MATERIAL_KM = %(km)s;

export type ActionKey =
%(actions)s;

export type ActionGroup = 'released' | 'deferred' | 'enforced' | 'refused';

/** Only computed for actions that would remove a provider. `null` is not `clear`. */
export type GateState = 'clear' | 'protect' | 'phantom' | 'unknown';

export type AgentKey =
%(agents)s;

export type Stance = 'supports' | 'opposes' | 'inconclusive';

export interface RosterEntry {
\tkey: AgentKey;
\ttitle: string;
\towns: string;
\t/** Whether this agent worked the case at all. */
\tran: boolean;
\t/** Position in the order the agents worked, or null when it did not run. */
\torder: number | null;
\t/** Ran because a rule mandated it. The Router may only widen the mandatory set. */
\tmandatory: boolean;
\topened_by_router: boolean;
\trouter_reason: string | null;
}

export interface Finding {
\t/** The reading, not the agent: desk_audit, medical_audit, field_review, ... */
\treading: string;
\treading_title: string;
\tagent_key: AgentKey | null;
\tagent_title: string;
\tchannel: string | null;
\tstance: Stance;
\tconfidence: number;
\tconclusion: string;
\tcitation: string | null;
\t/** Set aside by the Audit Reviewer: it now weighs nothing. */
\tdisputed: boolean;
\t/** The reviewer tried to dispute it and was refused: the reading repeats a measurement. */
\tdispute_refused: boolean;
}

export interface ToolCall {
\tn: number;
\tagent_key: AgentKey | null;
\tagent_title: string;
\ttool: string;
\targuments: { name: string; value: string }[];
\toutcome: string;
\trefused: boolean;
}

export interface Access {
\tdistrict: string | null;
\tstate: string | null;
\tspecialty: string | null;
\tspecialty_name: string | null;
\thospital_listed: boolean | null;
\tn_providers: number | null;
\t/** `null` means no alternative is listed at all -- a stronger signal than a large number. */
\tkm_to_alternative: number | null;
\tnearest_alternative: string | null;
\t/** Just the district, recovered from that prose, so the map can draw the journey. Null when no
\t    other district is named -- an alternative inside the same district involves no travel. */
\tnearest_alternative_district: string | null;
\tpopulation: number | null;
\taspirational: boolean | null;
\tcapability_ok: boolean | null;
\tcapability_reason: string | null;
}

export interface AtStake {
\tspecialty_name: string;
\tn_providers: number | null;
\tnearest_alternative: string;
\tnearest_alternative_district: string | null;
\twhy_protected: string;
}

export interface Trigger {
\ttrigger_id: string | null;
\tname: string | null;
\tseverity: number | null;
\tevidence: string | null;
\tsource: string | null;
}

export interface Package {
\tcode: string;
\tname: string;
\tpublished_rate: string;
\tclaimed_rs: number | null;
}

export interface Defence {
\texplanation: string | null;
\t/** false means the evidence did not exclude it, which stops an unreviewed action. */
\texcluded: boolean | null;
}

export interface CommitteeBrief {
\tsummary: string | null;
\toptions: { option: string; consequence: string }[];
\trecommendation: string | null;
\tquestion: string | null;
}

export interface RouterOpened {
\ttrack: string | null;
\tagent_key: AgentKey | null;
\treason: string;
}

export interface Case {
\tcase_id: string;
\tclaim_id: string;
\thospital_ref: string;
\ttrigger_id: string | null;
\ttrigger: Trigger | null;
\ttriggers_fired: string[];
\tseverity: number | null;
\tpackage: Package | null;
\taction: ActionKey;
\taction_label: string;
\taction_group: ActionGroup;
\tclaim_withheld: boolean;
\thuman_required: boolean;
\tgate: GateState | null;
\tgate_label: string | null;
\tconfidence: number;
\tbelow_floor: boolean;
\t/** The model was unavailable and the deterministic rules decided alone: no crew data at all. */
\tdegraded: boolean;
\tchannels_used: string[];
\tfield_channels_ordered: string[];
\taccess: Access | null;
\tspecialties_at_stake: string[];
\tat_stake: AtStake[];
\treason_codes: string[];
\tconflicts: string[];
\tfindings: Finding[];
\tagents: AgentKey[];
\troster: RosterEntry[];
\trouter_opened: RouterOpened[];
\tdefence: Defence | null;
\treview: string | null;
\tdisputed: string[];
\tdisputes_refused: string[];
\ttrail: ToolCall[];
\ttools_called: string[];
\tcommittee_question: string | null;
\tbrief: CommitteeBrief | null;
\texplanation: string | null;
\tchannels_ordered: string[];
\tfield_questions: string[];
\tfield_checklist: string[];
\tdocuments_requested: string[];
\tfield_audit_queue: string[];
\tartefact_path: string | null;
\tartefact_title: string | null;
\t/** The issued notice or order as Markdown. Released and refused claims issue none. */
\tartefact: string | null;
\tmode: string | null;
\tdecided_ts: string | null;
\tmodel: string | null;
\tacted_by: string | null;
}

export interface Meta {
\tgenerated_at: string;
\tmodel: string | null;
\tcounts: { cases: number; degraded: number; crewed: number };
\tsource_run: string;
\tconfidence_floor: number;
\tdistance_material_km: number;
}

export interface OutcomeSplit {
\tinnocent: { correct: number; costly: number; wrong: number; n: number };
\tfraud: { correct: number; acceptable: number; wrong: number; n: number };
\twrong: number;
\tcorrect: number;
\tn: number;
}

export interface BenchmarkCase {
\tcase_id: string;
\ttrigger: string;
\ttruth: 'fraud' | 'innocent';
\treading: string;
\taction: ActionKey | null;
\tscore: 'correct' | 'costly' | 'acceptable' | 'wrong' | null;
\tconfidence: number | null;
\tdegraded: boolean;
\tacted_by: string | null;
\tmodel: string | null;
\tagents: string[];
\tdisputed: string[];
\tdisputes_refused: string[];
\topened_by_router: string[];
\tdesk_stance: string | null;
\tdesk_confidence: number | null;
\tdesk_conclusion: string | null;
\tmedical_stance: string | null;
\tmedical_confidence: number | null;
\tmedical_conclusion: string | null;
\tbilling_stance: string | null;
\tbilling_confidence: number | null;
\tbilling_conclusion: string | null;
\tdefence: string | null;
\tdefence_excluded: boolean | null;
\treview: string | null;
\trefused_calls: number | null;
\tllm_calls: number | null;
\tseconds: number | null;
}

export interface BenchmarkSeries {
\tkey: string;
\tlabel: string;
\tn_cases: number | null;
\tscored: number | null;
\toverall: OutcomeSplit;
\tby_reading: Record<string, OutcomeSplit>;
\tby_trigger: Record<string, OutcomeSplit>;
\tcrew: Record<string, unknown> | null;
\tcases: BenchmarkCase[];
}

export interface Benchmark {
\tcases: number | null;
\tfraud: number | null;
\treadings: string[];
\tseries: Record<string, BenchmarkSeries>;
\tcaveat: string;
}

export interface Evaluation {
\tconfig: Record<string, unknown>;
\tsummary: {
\t\tflagged_cases: number;
\t\tfraud_share_of_flags: number;
\t\tauto_resolution_share: number;
\t\tresolved_at_desk_share: number;
\t\tfield_audit_share: number;
\t\thuman_share: number;
\t\tfinal_actions: Record<string, number>;
\t\tinnocent: Record<string, number>;
\t\tfraud: Record<string, number>;
\t\tconfirmed_egregious_share: number;
\t\tper_year: Record<string, number>;
\t};
\tgate_at_chosen_km: { protect: number; phantom: number; removes_only_provider: number };
\tdistance_material_km: number;
\tconfidence_floor: number;
\tby_trigger: Record<string, number | string>[];
\tby_state: Record<string, number | string>[];
\tallocation_fit: Record<string, number | string>[];
\tthreshold_sweep: Record<string, number | null>[];
\tsensitivity: Record<string, number | string | null>[];
\tablation: Record<string, number | string | null>[];
\tdisparate_impact: Record<string, number | string | null>[];
\tcaveats: Record<string, string>;
\tlimitations: string[];
}
'''


def render_types() -> str:
    actions = "\n".join(f"\t| '{key}'" for key in ACTION_GROUP)
    agents = "\n".join(f"\t| '{key}'" for key in AGENT_TITLES)
    return TYPES_TS % {"floor": CONFIDENCE_FLOOR, "km": DISTANCE_MATERIAL_KM,
                       "actions": actions, "agents": agents}


# ── main ─────────────────────────────────────────────────────────────────────

def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  {path}  ({path.stat().st_size / 1024:.1f} KB)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the decided run as static JSON for the front end")
    ap.add_argument("--out", default="frontend/static/data", help="where the JSON is written")
    ap.add_argument("--run", default=str(ROOT / "out_live"), help="the run directory to export")
    ap.add_argument("--types", default=None,
                    help="where to write types.ts (default: <out>/../../src/lib/types.ts)")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    run_dir = Path(args.run).resolve()

    cases = build_cases(run_dir)
    degraded = sum(1 for c in cases if c["degraded"])
    models = sorted({c["model"] for c in cases if c["model"]})

    print(f"export_frontend: {len(cases)} cases from {run_dir}")
    write_json(out / "cases.json", cases)
    write_json(out / "evaluation.json", build_evaluation())
    write_json(out / "benchmark.json", build_benchmark())
    write_json(out / "meta.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": models[0] if len(models) == 1 else (", ".join(models) or None),
        "counts": {"cases": len(cases), "degraded": degraded, "crewed": len(cases) - degraded},
        "source_run": run_dir.name,
        "confidence_floor": CONFIDENCE_FLOOR,
        "distance_material_km": DISTANCE_MATERIAL_KM,
    })

    types = Path(args.types) if args.types else out.parent.parent / "src" / "lib" / "types.ts"
    types.parent.mkdir(parents=True, exist_ok=True)
    types.write_text(render_types(), encoding="utf-8")
    print(f"  {types}")

    if degraded == len(cases) and cases:
        print("\nNOTE: every case in this run is degraded -- the rules decided alone, so there is no crew data\n"
              "      (no roster, no disputes, no advocate, no trail). For a run with the agents:\n"
              "      python -m crew.run --scenarios --llm --provider gemini --out out_live", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
