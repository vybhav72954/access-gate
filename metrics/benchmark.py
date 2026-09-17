"""Rules versus the crew on the agent benchmark (generate/benchmark.py; docs/10-AGENT-EVALUATION.md).

    python -m metrics.benchmark                             # the rules path, in seconds
    python -m metrics.benchmark --llm --provider gemini     # the crew, live; resumes where an earlier run stopped
    python -m metrics.benchmark --report                    # results/agent_benchmark.json and docs/10, from the CSVs

Writes  results/agent_benchmark_rules.csv        one row per case, the rules path
        results/agent_benchmark_<provider>.csv   one row per case, the crew on that provider
        results/agent_benchmark.json             the scores, by path, reading and trigger
        docs/10-AGENT-EVALUATION.md              the write-up, generated from the numbers
        out_benchmark/<path>/                    the decision log and artefacts of each run

Scoring (the costs are not symmetric, so neither is the scale):
    innocent  released = correct | field audit or human review = costly (delay, a field visit) | enforced = WRONG
    fraud     enforced = correct | field audit or human review = acceptable (evidence bought)  | released = WRONG

A live run writes each case as it finishes, so a spent quota loses nothing: run it again to continue. A case the crew
could not run (degraded to the rules) is kept, marked, and left out of the crew's score; --retry-degraded runs those
cases again.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from crew import providers
from crew.run import clear_previous_run, process
from crew.schemas import Action, Decision
from crew.tools import Tools
from generate.benchmark import CORRECTIONS, READINGS, BenchCase, build_benchmark

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
OUT = ROOT / "out_benchmark"
DOC = ROOT / "docs/10-AGENT-EVALUATION.md"

ENFORCED = frozenset({Action.SHOW_CAUSE, Action.SUSPEND, Action.ESCALATE_SEC, Action.DELIST_SPECIALTY})
DEFERRED = frozenset({Action.FIELD_AUDIT, Action.NO_ACTION})
FIELDS = ["case_id", "trigger", "truth", "reading", "action", "score", "confidence", "desk_stance", "desk_confidence",
          "degraded", "acted_by", "investigator_tools", "officer_tools", "refused_calls", "model", "llm_calls",
          "seconds", "desk_conclusion"]
# Added with the six-agent crew. A results file written before has none of them, and the report reads it as the
# two-agent crew's.
SIX_AGENT_FIELDS = ["agents", "medical_stance", "medical_confidence", "medical_tools", "disputed", "reviewer_tools",
                    "medical_conclusion", "review", "disputes_refused", "opened_by_router", "billing_stance",
                    "billing_confidence", "billing_tools", "billing_conclusion", "defence_excluded", "defence"]
FIELDS += SIX_AGENT_FIELDS
STOP_AFTER_DEGRADED = 3          # consecutive cases the crew could not run: the quota is spent, stop and resume later

log = logging.getLogger("access_gate.benchmark")


def score(fraud: bool, action: Action) -> str:
    if action in ENFORCED:
        return "correct" if fraud else "wrong"
    if action is Action.RELEASE_CLAIM:
        return "wrong" if fraud else "correct"
    if action in DEFERRED:
        return "acceptable" if fraud else "costly"
    raise ValueError(f"{action.value} is not a desk outcome")        # REFUSE: a malformed case, a bug in the benchmark


def _words(text: str | None, limit: int = 400) -> str:
    return " ".join((text or "").split())[:limit]


def row(case: BenchCase, d: Decision, calls: int | None, seconds: float) -> dict:
    desk = next((f for f in d.findings if f.agent == "desk_audit"), None)
    medical = next((f for f in d.findings if f.agent == "medical_audit"), None)
    billing = next((f for f in d.findings if f.agent == "billing_audit"), None)
    stance = {True: "supports", False: "opposes", None: "inconclusive"}
    return {"agents": "|".join(d.agents), "disputed": "|".join(d.disputed), "review": _words(d.review),
            "disputes_refused": "|".join(d.disputes_refused),
            "opened_by_router": "|".join(d.opened_by_router),
            "billing_stance": stance[billing.supports_fraud] if billing else "",
            "billing_confidence": f"{billing.confidence:.2f}" if billing else "",
            "billing_tools": "|".join(t.tool for t in d.trail if t.agent == "billing_analyst"),
            "billing_conclusion": _words(billing.conclusion if billing else ""),
            "defence_excluded": "" if d.defence_excluded is None else str(d.defence_excluded),
            "defence": _words(d.defence or ""),
            "medical_stance": stance[medical.supports_fraud] if medical else "",
            "medical_confidence": f"{medical.confidence:.2f}" if medical else "",
            "medical_tools": "|".join(t.tool for t in d.trail if t.agent == "medical_auditor"),
            "reviewer_tools": "|".join(t.tool for t in d.trail if t.agent == "audit_reviewer"),
            "medical_conclusion": _words(medical.conclusion if medical else ""),
            "case_id": case.case_id, "trigger": case.trigger, "truth": "fraud" if case.fraud else "innocent",
            "reading": case.reading, "action": d.action.value, "score": score(case.fraud, d.action),
            "confidence": f"{d.confidence:.2f}", "desk_stance": stance[desk.supports_fraud] if desk else "",
            "desk_confidence": f"{desk.confidence:.2f}" if desk else "", "degraded": d.degraded,
            "acted_by": d.acted_by,
            "investigator_tools": "|".join(t.tool for t in d.trail if t.agent == "desk_investigator"),
            "officer_tools": "|".join(t.tool for t in d.trail if t.agent == "enforcement_officer"),
            "refused_calls": sum(t.refused for t in d.trail), "model": d.model or "",
            "llm_calls": "" if calls is None else calls, "seconds": f"{seconds:.1f}",
            "desk_conclusion": _words(desk.conclusion if desk else "")}


# ── counting model calls: the free tier is the budget ────────────────────────

class _Counted:
    def __init__(self, target, counter: list[int]):
        self._target, self._counter = target, counter

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if name in ("generate_content", "create", "parse"):
            def counted(*a, **kw):
                self._counter[0] += 1
                return attr(*a, **kw)
            return counted
        return attr


class _CountedClient:
    """The SDK client itself stays referenced here: google-genai closes its connection when the Client is collected,
    and a wrapper holding only `client.models` lost it after a few calls ("the client has been closed")."""

    def __init__(self, client, counter: list[int]):
        self._client, self._counter = client, counter

    @property
    def models(self):
        return _Counted(self._client.models, self._counter)

    @property
    def beta(self):
        return SimpleNamespace(messages=_Counted(self._client.beta.messages, self._counter))


def counted_llm(provider: str):
    counter = [0]
    if provider == "gemini":
        from google import genai
        client = genai.Client()
    else:
        import anthropic
        client = anthropic.Anthropic()
    return providers.make_llm(provider, _CountedClient(client, counter)), counter


# ── running ──────────────────────────────────────────────────────────────────

def results_path(path: str) -> Path:
    return RESULTS / f"agent_benchmark_{path}.csv"


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run(path: str, llm=None, counter=None, fresh: bool = False, retry_degraded: bool = False,
        only: set[str] | None = None, redo: set[str] | None = None) -> int:
    tools = Tools()
    cases, store = build_benchmark(tools)
    out, csv_path = OUT / path, results_path(path)
    if fresh:
        refused = clear_previous_run(out)
        if refused:
            raise SystemExit(refused)
        csv_path.unlink(missing_ok=True)
    kept = [r for r in read_rows(csv_path)
            if not (retry_degraded and r["degraded"] == "True") and r["case_id"] not in (redo or set())]
    if redo:
        _drop_from_log(out, redo)
    done = {r["case_id"] for r in kept}
    todo = [c for c in cases if c.case_id not in done and (only is None or c.case_id in only)]
    if llm is not None and todo:
        _record_run(path, [c.case_id for c in todo], fresh=fresh)
    RESULTS.mkdir(exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:        # rewrite what is kept, then append
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(kept)
    print(f"{path}: {len(done)} case(s) already scored, {len(todo)} to run")
    in_a_row = 0
    for i, case in enumerate(todo, start=1):
        before, started = (counter[0] if counter else 0), time.monotonic()
        d = process(case.claim, store, tools, llm=llm, out_dir=out)
        r = row(case, d, (counter[0] - before) if counter else None, time.monotonic() - started)
        with csv_path.open("a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=FIELDS).writerow(r)
        print(f"[{i}/{len(todo)}] {case.case_id:11} {r['truth']:8} {r['reading']:13} {r['action']:18} {r['score']:10} "
              f"{'DEGRADED ' if (llm is not None and d.degraded) else ''}tools={len(d.trail)} calls={r['llm_calls']} {r['seconds']}s",
              flush=True)
        in_a_row = in_a_row + 1 if (llm is not None and d.degraded) else 0
        if in_a_row >= STOP_AFTER_DEGRADED:
            print(f"\nStopped: the crew could not run {in_a_row} cases in a row (quota or key; the log says why). "
                  "Run the same command again later to continue; --retry-degraded re-runs the degraded cases.")
            return 2
    return 0


def runs_path(path: str) -> Path:
    return RESULTS / f"agent_benchmark_{path}_runs.json"


def _record_run(path: str, case_ids: list[str], fresh: bool) -> None:
    """When each crew run started and which cases it ran: the report dates the results from this, not from memory."""
    from datetime import datetime
    RESULTS.mkdir(exist_ok=True)
    log_path = runs_path(path)
    runs = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    runs.append({"started": datetime.now().isoformat(timespec="seconds"), "fresh": fresh, "cases": case_ids})
    log_path.write_text(json.dumps(runs, indent=2), encoding="utf-8")


def _drop_from_log(out: Path, case_ids: set[str]) -> None:
    """Cases run again leave no earlier row in the run's decision log."""
    log_path = out / "decision_log.csv"
    if not log_path.exists():
        return
    with log_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fields = list(rows[0]) if rows else None
    if fields:
        with log_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(r for r in rows if r["claim_id"] not in case_ids)


# ── the report ───────────────────────────────────────────────────────────────

def tally(rows: list[dict]) -> dict:
    out = {}
    for truth in ("innocent", "fraud"):
        c = Counter(r["score"] for r in rows if r["truth"] == truth)
        out[truth] = {k: c.get(k, 0) for k in (("correct", "costly", "wrong") if truth == "innocent"
                                                else ("correct", "acceptable", "wrong"))}
        out[truth]["n"] = sum(c.values())
    out["wrong"] = out["innocent"]["wrong"] + out["fraud"]["wrong"]
    out["correct"] = out["innocent"]["correct"] + out["fraud"]["correct"]
    out["n"] = len(rows)
    return out


def summarise(rows: list[dict], crew: bool) -> dict:
    scored = [r for r in rows if not (crew and r["degraded"] == "True")]
    s = {"cases": len(rows), "scored": len(scored), "overall": tally(scored),
         "by_reading": {k: tally([r for r in scored if r["reading"] == k]) for k in READINGS},
         "by_trigger": {t: tally([r for r in scored if r["trigger"] == t])
                        for t in sorted({r["trigger"] for r in scored})}}
    if crew:
        calls = [int(r["llm_calls"]) for r in scored if r["llm_calls"]]
        clinical = [r for r in scored if r["trigger"] in CLINICAL]
        s["crew"] = {
            "agents": 9 if nine_agents(rows) else 6 if six_agents(rows) else 2,
            "medical_ran": sum("medical_auditor" in (r.get("agents") or "") for r in clinical),
            "clinical_cases": len(clinical),
            "disputed_cases": sum(bool(r.get("disputed")) for r in scored),
            "disputes_refused_cases": sum(bool(r.get("disputes_refused")) for r in scored),
            "router_opened_cases": sum(bool(r.get("opened_by_router")) for r in scored),
            "router_opened": dict(Counter(x for r in scored for x in (r.get("opened_by_router") or "").split("|") if x)),
            "billing_ran": sum(bool(r.get("billing_stance")) for r in scored),
            "advocate_ran": sum(r.get("defence_excluded") not in (None, "") for r in scored),
            "defence_stood": sum(r.get("defence_excluded") == "False" for r in scored),
            "disputes": dict(Counter(x for r in scored for x in (r.get("disputed") or "").split("|") if x)),
            "reviewer_used_tools": sum(bool(r.get("reviewer_tools")) for r in scored),
            "degraded": len(rows) - len(scored),
            "officer_acted": sum(r["acted_by"] == "enforcement_officer" for r in scored),
            "investigator_used_tools": sum(bool(r["investigator_tools"]) for r in scored),
            "t7_compared_documents": sum("compare_documents" in r["investigator_tools"] or
                                         "read_document" in r["investigator_tools"]
                                         for r in scored if r["trigger"] == "T7"),
            "t7_cases": sum(r["trigger"] == "T7" for r in scored),
            "refused_calls": sum(int(r["refused_calls"] or 0) for r in scored),
            "llm_calls_mean": round(sum(calls) / len(calls), 1) if calls else None,
            "llm_calls_max": max(calls) if calls else None,
            "models": sorted({r["model"] for r in scored if r["model"]}),
        }
    return s


CLINICAL = frozenset({"T2", "T3", "T4", "T7"})       # the triggers the Medical Auditor works (crew/crew_llm.py)


def six_agents(rows: list[dict]) -> bool:
    """Whether a results file was written by the six-agent crew or later: the columns it added are in its header."""
    return bool(rows) and "agents" in rows[0]


def nine_agents(rows: list[dict]) -> bool:
    """The nine-agent crew added the router's plan and the advocate's answer to every row."""
    return bool(rows) and "opened_by_router" in rows[0]


def roster(rows: list[dict]) -> str:
    return "nine agents" if nine_agents(rows) else "six agents" if six_agents(rows) else "two agents"


def report() -> dict:
    tools = Tools()
    cases, _ = build_benchmark(tools)
    rules = read_rows(results_path("rules"))
    if len(rules) != len(cases):
        raise SystemExit(f"results/agent_benchmark_rules.csv has {len(rules)} of {len(cases)} cases: run "
                         "`python -m metrics.benchmark` first")
    series = [Series("Rules", "rules", rules, crew=False)]
    for provider in providers.PROVIDERS:
        first, latest = read_rows(RESULTS / f"agent_benchmark_{provider}_first_run.csv"), read_rows(results_path(provider))
        before = f"Crew ({provider}), first run: {roster(first)}, before the changes in §6"
        if first and first != latest:
            series.append(Series(before, f"{provider}_first_run", first, crew=True))
        if latest:
            label = (before if first == latest else f"Crew ({provider}), final run: {roster(latest)}" if first
                     else f"Crew ({provider}): {roster(latest)}")
            series.append(Series(label, provider, latest, crew=True))
    data = {"cases": len(cases), "fraud": sum(c.fraud for c in cases),
            "series": {s.key: {"label": s.label, **summarise(s.rows, s.crew)} for s in series}}
    (RESULTS / "agent_benchmark.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    DOC.write_text(write_doc(cases, series, data), encoding="utf-8")
    print(f"wrote {RESULTS / 'agent_benchmark.json'} and {DOC}")
    return data


@dataclass
class Series:
    label: str
    key: str
    rows: list[dict]
    crew: bool


def _header(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |\n|" + "---|" * len(cells)


def _cell(t: dict, truth: str) -> str:
    x = t[truth]
    if truth == "innocent":
        return f"{x['correct']} released · {x['costly']} deferred · **{x['wrong']} enforced**"
    return f"{x['correct']} enforced · {x['acceptable']} deferred · **{x['wrong']} released**"


MARK = {"correct": "", "costly": " (costly)", "acceptable": " (acceptable)", "wrong": " **WRONG**"}
MEANING = {"keyword": "use the words the rules look for, and mean them",
           "paraphrase": "state the truth in words the rules' patterns do not match",
           "trap": "contain words the patterns match that mean something else",
           "mislabelled": "file the evidence under a different document type",
           "contradiction": "assert something another document on file contradicts",
           "cross-claim": "put the evidence in the beneficiary's other admissions (T7)"}


def write_doc(cases: list[BenchCase], series: list[Series], data: dict) -> str:
    n, fraud = data["cases"], data["fraud"]
    stats = data["series"]
    counts = Counter(c.reading for c in cases)
    crews = [s for s in series if s.crew]
    lines = ["# 10 · Agent evaluation: does the crew read better than the rules?", "",
             "> Generated by `python -m metrics.benchmark --report` from `results/agent_benchmark_*.csv`. Do not edit "
             "by hand: rerun the benchmark instead.", "",
             "## 1. What this measures, and what it does not", "",
             f"The 5,000-case evaluation (09-EVALUATION) measures the policy and the access gate with the rules reading "
             f"every document, because its documents are neutral by design. It cannot say whether an agent reads "
             f"documents better or worse than those rules. This benchmark can: {n} hand-written cases ({fraud} fraud, "
             f"{n - fraud} innocent) whose truth is stated in the documents, run through both paths of the same "
             f"pipeline (`crew.run.process`), with the same policy deciding.", "",
             "Each case's truth is written in one of six ways:", "",
             "| Reading | What the documents do | Cases |", "|---|---|---|"]
    lines += [f"| {k} | {MEANING[k]} | {counts[k]} |" for k in READINGS]
    lines += ["",
              "**Read the numbers with the design in mind.** The cases were written to probe where free text defeats "
              "patterns, so the rules' score here is not their error rate in production; the evaluation in "
              "09-EVALUATION is. What the benchmark can show is whether the agents recover what the patterns miss, "
              "and whether they fail somewhere the rules do not (the keyword and contradiction cases). The team that "
              "built the crew wrote the cases: a bias disclosed here, and the reason each case, its truth and every "
              "path's decision are listed in full below.", "",
              "No trigger in the benchmark is egregious, so no case reaches the access gate: a confirmed finding is a "
              "show-cause notice, and the benchmark isolates the desk reading.", "",
              "Scoring is asymmetric, like the costs: an innocent claim released is correct, deferred to a field "
              "audit or a human is costly, and enforced against is **wrong**; a fraud enforced against is correct, "
              "deferred is acceptable (evidence is bought), and released is **wrong**.", "",
              "## 2. Results", "",
              _header(["Path", "Cases scored", f"Innocent (of {n - fraud})", f"Fraud (of {fraud})", "Wrong decisions"])]
    for s in series:
        st = stats[s.key]
        scored = f"{st['scored']}" if not s.crew else f"{st['scored']} of {st['cases']}"
        lines.append(f"| {s.label} | {scored} | {_cell(st['overall'], 'innocent')} | {_cell(st['overall'], 'fraud')} | "
                     f"**{st['overall']['wrong']}** |")
        if s.crew and st["scored"] < n:
            # A comparison on unequal case sets is not one: score the rules on exactly the cases this run scored.
            ran = {x["case_id"] for x in s.rows if x["degraded"] != "True"}
            same = tally([r for r in series[0].rows if r["case_id"] in ran])
            lines.append(f"| Rules, on the same {len(ran)} cases | {len(ran)} | {_cell(same, 'innocent')} | "
                         f"{_cell(same, 'fraud')} | **{same['wrong']}** |")
    if not crews:
        lines += ["", "*The crew has not been run on this benchmark yet: `python -m metrics.benchmark --llm --provider "
                      "gemini`.*"]
    for title, key, groups in (("By reading", "by_reading", READINGS),
                               ("By trigger", "by_trigger", sorted(stats["rules"]["by_trigger"]))):
        lines += ["", f"### {title}", "", "Correct and wrong decisions.", "",
                  _header([title.split()[-1].capitalize(), "Cases"] + [f"{s.label}: correct / wrong" for s in series])]
        for g in groups:
            cells = [f"{stats[s.key][key].get(g, {'correct': 0})['correct']} / "
                     f"{stats[s.key][key].get(g, {'wrong': 0})['wrong']}" for s in series]
            size = counts[g] if key == "by_reading" else stats["rules"][key][g]["n"]
            lines.append(f"| {g} | {size} | " + " | ".join(cells) + " |")

    for s in crews:
        c = stats[s.key]["crew"]
        st = stats[s.key]
        lines += ["", f"### How the crew worked: {s.label}", "",
                  f"- Cases the crew ran: {st['scored']} of {st['cases']}"
                  + (f" ({c['degraded']} degraded to the rules when the model was unavailable, and are left out of its "
                     f"score)" if c["degraded"] else ""),
                  f"- The Desk Investigator called its tools on {c['investigator_used_tools']} cases; on the "
                  f"{c['t7_cases']} repeat-admission cases it read or compared the other admissions' documents on "
                  f"{c['t7_compared_documents']}"]
        if c["agents"] >= 9:
            opened = ", ".join(f"{k} {v}" for k, v in sorted(c.get("router_opened", {}).items())) or "none"
            lines += [f"- The Case Router opened work no rule mandated on {c.get('router_opened_cases', 0)} cases "
                      f"(by track: {opened}); it can add to the mandatory set and never take from it (B-48)",
                      f"- The Billing & Tariff Analyst worked {c.get('billing_ran', 0)} cases",
                      f"- The Provider Advocate worked {c.get('advocate_ran', 0)} cases, and on "
                      f"{c.get('defence_stood', 0)} of them the evidence on file did not exclude its explanation, "
                      f"which stops an action no human has reviewed (B-49)"]
        if c["agents"] >= 6:
            disputes = ", ".join(f"{k} {v}" for k, v in sorted(c["disputes"].items())) or "none"
            lines += [f"- The Medical Auditor worked {c['medical_ran']} of the {c['clinical_cases']} clinical cases "
                      f"(T2, T3, T4, T7)",
                      f"- The Audit Reviewer used its tools on {c['reviewer_used_tools']} cases and disputed a reading "
                      f"on {c['disputed_cases']} (by reading: {disputes}); a disputed reading weighs nothing"
                      + (f". On {c['disputes_refused_cases']} case(s) the dispute was **refused** because the reading "
                         f"repeated a comparison the claim store makes itself (B-44a, F-61), and the reading kept its "
                         f"weight" if c.get("disputes_refused_cases") else ""),
                      "- No case has field reports on file or reaches the access gate, so neither the Field Evidence "
                      "Analyst nor the Committee Liaison works in this benchmark"]
        lines += [
                  f"- The Enforcement Officer executed the policy's decision through its action tool on "
                  f"{c['officer_acted']} of {st['scored']} cases; on the rest the orchestrator executed the same "
                  f"decision with the template explanation",
                  f"- Tool calls refused (a wrong action, a rejected draft, arguments that do not fit, a claim outside "
                  f"the case): {c['refused_calls']}",
                  f"- Model requests per case, rate-limit retries included: {c['llm_calls_mean']} on average, "
                  f"{c['llm_calls_max']} at most; models that served: {', '.join(c['models']) or '-'}"]

    lines += ["", "## 3. Every case", "",
              _header(["Case", "Truth", "Reading", "What the documents say"] + [s.label for s in series])]
    by = {s.key: {r["case_id"]: r for r in s.rows} for s in series}
    for case in cases:
        cells = []
        for s in series:
            r = by[s.key].get(case.case_id)
            cells.append("not run" if r is None else "degraded" if (s.crew and r["degraded"] == "True")
                         else f"{r['action']}{MARK[r['score']]}")
        lines.append(f"| {case.case_id} | {'fraud' if case.fraud else 'innocent'} | {case.reading} | {case.note} | "
                     + " | ".join(cells) + " |")

    for s in crews:
        wrong = [x for x in s.rows if x["score"] == "wrong" and x["degraded"] != "True"]
        lines += ["", f"## 4. Where the crew was wrong: {s.label}", ""]
        if not wrong:
            lines.append("Nowhere, on the cases it ran.")
        for x in wrong:
            why = (f"- **{x['case_id']}** ({x['truth']}, {x['reading']}): {x['action']}. The investigator "
                   f"{x['desk_stance']} at {x['desk_confidence']}: \"{x['desk_conclusion']}\"")
            if x.get("medical_stance"):
                why += (f" The Medical Auditor {x['medical_stance']} at {x['medical_confidence']}: "
                        f"\"{x['medical_conclusion']}\"")
            if x.get("agents"):
                why += f" Disputed by the Audit Reviewer: {x['disputed'].replace('|', ', ')}." if x.get("disputed") \
                    else " The Audit Reviewer disputed neither reading."
            lines.append(why)

    lines += ["", "## 5. Limits", "",
              "- Forty cases measure direction and failure modes, not rates: one case moves a category by a large "
              "share, and a live model gives different readings on different days (each run is one sample).",
              "- The cases were written by the team that built the crew (section 1).",
              "- **The rules are not checked for copied paperwork across one beneficiary's own admissions.** "
              "`ClaimStore.reused` fires only when the beneficiary differs (T6), so on the T7 cases nothing "
              "deterministic reports byte-identical documents, though `ClaimStore.identical` would separate them "
              "exactly. Part of the crew's advantage on the cross-claim reading is therefore a gap in the rules "
              "rather than better reading. Declared, not closed (01-HLD \u00a710.7).",
              "- Every reading the crew makes, the desk's and the medical auditor's, meets the rules desk's "
              "evidentiary preconditions (a clearance needs the package's mandatory documents; see 02-LLD B-28, F-44). "
              "Where a precondition blocks a reading, the crew's decision shows it.",
              "- A deferred decision is scored as costly or acceptable, not as wrong, because a field audit buys "
              "evidence; its cost in field visits is measured in 09-EVALUATION.",
              "", "## 6. Changes after the first live run", "",
              "Changed after the first live run (17 September), and why. The rules path always runs on the current "
              "code and cases. The crew's first run is kept, and scored above, beside any run on the changed code.",
              ""]
    lines += [f"- {c}" for c in CORRECTIONS]
    for s in crews:
        runs_log = runs_path(s.key.removesuffix("_first_run"))
        if s.key.endswith("_first_run") or not runs_log.exists():
            continue
        runs = json.loads(runs_log.read_text(encoding="utf-8"))
        ran = {x["case_id"] for x in s.rows}
        latest = {}
        for run in runs:
            for cid in run["cases"]:
                latest[cid] = run["started"]
        dates = sorted({latest[c][:16].replace("T", " ") for c in ran if c in latest})
        if dates:
            lines += ["", f"**{s.label}** was produced by runs started {', '.join(dates)}."]
    for provider in providers.PROVIDERS:
        second = read_rows(RESULTS / f"agent_benchmark_{provider}_second_run.csv")
        first = read_rows(RESULTS / f"agent_benchmark_{provider}_first_run.csv") or read_rows(results_path(provider))
        if second and first:
            chosen = {r["case_id"]: r["action"] for r in first}
            same = sum(chosen.get(r["case_id"]) == r["action"] for r in second)
            lines += ["", f"**Consistency ({provider}).** During the first run two processes ran at once by accident, "
                          f"on the same code, and decided {len(second)} cases twice: they chose the same action on "
                          f"{same} of them. The duplicate decisions are in "
                          f"`results/agent_benchmark_{provider}_second_run.csv`."]
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="The agent benchmark: rules versus the crew")
    ap.add_argument("--llm", action="store_true", help="run the crew on a model (needs that provider's key)")
    ap.add_argument("--provider", choices=providers.PROVIDERS, default=None)
    ap.add_argument("--fresh", action="store_true", help="discard earlier results for this path and start again")
    ap.add_argument("--retry-degraded", action="store_true", help="run again the cases the crew could not run")
    ap.add_argument("--only", default=None, help="comma-separated case ids")
    ap.add_argument("--redo", default=None, help="comma-separated case ids to run again, discarding their results")
    ap.add_argument("--report", action="store_true", help="write results/agent_benchmark.json and docs/10 from the CSVs")
    args = ap.parse_args()

    os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("crewai.flow.runtime").setLevel(logging.CRITICAL)   # process() reports a failed call itself

    if args.report:
        report()
        return 0
    if args.provider and not args.llm:
        ap.error(f"--provider {args.provider} does nothing without --llm")
    only = {x.strip() for x in args.only.split(",")} if args.only else None
    if not args.llm:
        return run("rules", fresh=True)                     # instant, so always whole and current
    from dotenv import load_dotenv
    load_dotenv()
    provider = args.provider or providers.default_provider()
    if provider not in providers.PROVIDERS:
        ap.error(f"LLM_PROVIDER={provider!r} is not one of {', '.join(providers.PROVIDERS)}")
    if key := providers.missing_key(provider):
        ap.error(f"--llm on {provider} needs {key} in .env")
    llm, counter = counted_llm(provider)
    redo = {x.strip() for x in args.redo.split(",")} if args.redo else None
    return run(provider, llm=llm, counter=counter, fresh=args.fresh, retry_degraded=args.retry_degraded, only=only,
               redo=redo)


if __name__ == "__main__":
    raise SystemExit(main())
