"""The three numbers beyond pass/fail (docs/05-TEST-PLAN.md §4), measured.

    python -m metrics.run                     # 5,000 flagged cases, ablation, sensitivity (~15 min)
    python -m metrics.run --n 1000 --quick    # a trial: smaller, no ablation or sensitivity
    python -m metrics.run --doc-only          # rewrite docs/09-EVALUATION.md from results/

Only the full default run writes results/ and docs/09-EVALUATION.md. A trial (--quick, or any other --n) writes
to results/trial-n<N>[-quick]/, so checking that the script runs can never replace the published evaluation
with a smaller, noisier one (F-43).

Writes  results/metrics.json            headline numbers, by trigger, by state, the config used
        results/threshold_sweep.csv     DISTANCE_MATERIAL_KM from 0 to 200 km
        results/threshold_sweep.svg     the chart for the report
        results/disparate_impact.csv    outcomes by hospital type
        results/ablation.csv            the two design changes this evaluation forced, before and after
        results/sensitivity.csv         one-at-a-time sweeps of every simulation assumption
        docs/09-EVALUATION.md           the write-up, generated from the numbers above

Each flagged case runs through the real pipeline (crew.run.process, rules path).
A case ordered to the field re-enters with simulated field reports -- the same
re-entry the S6 and S8 fixtures exercise -- and its second decision is final.

Gate outcomes are rare events (a few percent of a few percent), so where the
access gate is concerned the numbers are computed exactly from the allocation
model rather than counted from a sample.
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from crew import investigate as rules_path
from crew.run import process
from crew.schemas import HUMAN_ACTIONS, Action, Adequacy, AgentFinding, Channel, Decision, Hospital
from crew.tools import Tools
from generate.corpus import Case, CorpusConfig, World, build_corpus
from rules import policy, triggers

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data/reference"
OGD = ROOT / "data/external/ogd"
RESULTS = ROOT / "results"
DOC = ROOT / "docs/09-EVALUATION.md"

SWEEP_KM = list(range(0, 205, 5))
ADVERSE = {Action.SHOW_CAUSE, Action.SUSPEND}
ENFORCED = ADVERSE | {Action.ESCALATE_SEC, Action.DELIST_SPECIALTY}
TYPES = ["Public", "Private(For Profit)", "Private(Not For Profit)", "GOI"]


@dataclass
class Outcome:
    case: Case
    first: Decision
    final: Decision

    @property
    def field_audited(self) -> bool:
        return self.first.action is Action.FIELD_AUDIT


def adjudicate(cases: list[Case], store, tools: Tools, builder) -> list[Outcome]:
    out = []
    for c in cases:
        first = process(c.claim, store, tools, write=False)
        final = first
        if first.action is Action.FIELD_AUDIT:
            reports = builder.field_reports(c, first.field_channels_ordered)
            final = process(c.claim.model_copy(update={"field_reports": reports}), store, tools, write=False)
        out.append(Outcome(c, first, final))
    return out


def share(xs, pred) -> float:
    xs = list(xs)
    return round(sum(1 for x in xs if pred(x)) / len(xs), 4) if xs else float("nan")


def is_innocent(o: Outcome) -> bool:
    return not o.case.fraud and not o.case.noncompliant


# ── headline ───────────────────────────────────────────────────────────────

def summarise(outcomes: list[Outcome], cfg: CorpusConfig, official: dict) -> dict:
    flags_per_year = official["admissions"] * (cfg.base_rate * cfg.recall + (1 - cfg.base_rate) * cfg.fpr)
    innocent = [o for o in outcomes if is_innocent(o)]
    fraud = [o for o in outcomes if o.case.fraud]
    human = lambda o: o.final.action in HUMAN_ACTIONS                                   # noqa: E731
    per_year = lambda s: round(s * flags_per_year)                                     # noqa: E731
    finals = Counter(o.final.action.value for o in outcomes)
    return {
        "flagged_cases": len(outcomes),
        "fraud_share_of_flags": round(sum(o.case.fraud for o in outcomes) / len(outcomes), 4),
        "auto_resolution_share": share(outcomes, lambda o: not human(o)),
        "resolved_at_desk_share": share(outcomes, lambda o: not o.field_audited and not human(o)),
        "field_audit_share": share(outcomes, lambda o: o.field_audited),
        "human_share": share(outcomes, human),
        "final_actions": {k: round(v / len(outcomes), 4) for k, v in finals.most_common()},
        "innocent": {
            "cases": len(innocent),
            "released": share(innocent, lambda o: o.final.action is Action.RELEASE_CLAIM),
            "show_cause": share(innocent, lambda o: o.final.action is Action.SHOW_CAUSE),
            "suspended": share(innocent, lambda o: o.final.action is Action.SUSPEND),
            "to_human": share(innocent, human),
        },
        "fraud": {
            "cases": len(fraud),
            "enforced": share(fraud, lambda o: o.final.action in ENFORCED),
            "released": share(fraud, lambda o: o.final.action is Action.RELEASE_CLAIM),
            "to_human": share(fraud, human),
        },
        "confirmed_egregious_share": share(outcomes, lambda o: o.final.gate is not None),
        "per_year": {
            "flags": round(flags_per_year),
            "flags_per_day": round(flags_per_year / 365),
            "field_audits": per_year(share(outcomes, lambda o: o.field_audited)),
            "human_reviews": per_year(share(outcomes, human)),
            "confirmed_egregious": per_year(share(outcomes, lambda o: o.final.gate is not None)),
            "suspensions": per_year(share(outcomes, lambda o: o.final.action is Action.SUSPEND)),
            "wrongful_suspensions": per_year(share(outcomes, lambda o: o.final.action is Action.SUSPEND
                                                   and not o.case.fraud)),
            "show_cause_notices": per_year(share(outcomes, lambda o: o.final.action is Action.SHOW_CAUSE)),
            "show_cause_to_innocent": per_year(share(outcomes, lambda o: o.final.action is Action.SHOW_CAUSE
                                                     and is_innocent(o))),
        },
    }


def by_trigger(outcomes: list[Outcome]) -> list[dict]:
    rows = []
    for t in triggers.SPECS:
        grp = [o for o in outcomes if o.case.trigger == t]
        if not grp:
            continue
        c = Counter(o.final.action.value for o in grp)
        rows.append({"trigger": t, "cases": len(grp), "fraud_share": share(grp, lambda o: o.case.fraud),
                     "field_audited": share(grp, lambda o: o.field_audited),
                     **{a.value: round(c.get(a.value, 0) / len(grp), 3) for a in Action if a is not Action.REFUSE}})
    return rows


def disparate_impact(outcomes: list[Outcome], figures: dict, gate_by_type: dict) -> pd.DataFrame:
    rows = []
    for t in TYPES:
        grp = [o for o in outcomes if o.case.hospital.hospital_type == t]
        inn = [o for o in grp if is_innocent(o)]
        fair = figures["fairness"].get(t, {})
        rows.append({
            "hospital_type": t, "network_pct": fair.get("network_pct"), "sole_provider_pct": fair.get("sole_pct"),
            "cases": len(grp),
            "auto_resolution": share(grp, lambda o: o.final.action not in HUMAN_ACTIONS),
            "to_human": share(grp, lambda o: o.final.action in HUMAN_ACTIONS),
            "suspended": share(grp, lambda o: o.final.action is Action.SUSPEND),
            "show_cause": share(grp, lambda o: o.final.action is Action.SHOW_CAUSE),
            "released": share(grp, lambda o: o.final.action is Action.RELEASE_CLAIM),
            "innocent_cases": len(inn),
            "innocent_adverse": share(inn, lambda o: o.final.action in ADVERSE),
            "egregious_escalated_exact": round(gate_by_type.get(t, {}).get("protect", float("nan")), 4),
            "egregious_delisted_exact": round(gate_by_type.get(t, {}).get("phantom", float("nan")), 4),
        })
    return pd.DataFrame(rows)


# ── the access gate: exact, not sampled ────────────────────────────────────

def _exposed(tools: Tools) -> dict[str, tuple[Hospital, tuple[Adequacy, ...]]]:
    """Every hospital the gate could ever stop, with the adequacy of every specialty it is empanelled for.

    A suspension removes a hospital from all its specialties, so the gate looks at the hospital, not at the billed
    cell (F-22). Only a cell with <= 2 providers can protect or be phantom; a hospital with none is always 'clear'.
    """
    out = {}
    for h in sorted(tools.hospitals(), key=lambda h: h.hospital_ref):
        network = tuple(tools.hospital_network(h))
        if any(a.n_providers <= policy.SOLE_PROVIDER + policy.ASPIRATIONAL_SHIFT for a in network):
            out[h.hospital_ref] = (h, network)
    return out


def _sole_real(network) -> list[Adequacy]:
    """The specialties this hospital is its district's only plausible provider of."""
    return [a for a in network if a.n_providers <= policy.SOLE_PROVIDER and a.capability_ok]


def _phantom(a: Adequacy) -> bool:
    return a.n_providers <= policy.SOLE_PROVIDER and not a.capability_ok


def structural_sweep(exposed: dict) -> pd.DataFrame:
    """Every real hospital the gate could stop, unweighted: what the gate would do at each threshold if it were
    confirmed to have committed an egregious fraud in a specialty it can plausibly deliver. No simulation."""
    rows = []
    for km in SWEEP_KM:
        escalated = suspended = lost = 0
        loss_districts: dict[int, int] = {}
        for _h, network in exposed.values():
            gate, _, _ = policy.suspension_gate(None, network, distance_km=km)
            sole = _sole_real(network)
            if gate == "protect":
                escalated += 1
            elif sole:
                suspended += 1
                lost += len(sole)
                loss_districts[sole[0].district_code] = sole[0].population
        rows.append({"km": km, "hospitals_escalated": escalated, "hospitals_suspended_removing_only_provider": suspended,
                     "cells_losing_only_provider": lost, "districts_losing_a_specialty": len(loss_districts),
                     "population_M_of_those_districts": round(sum(loss_districts.values()) / 1e6, 1),
                     "phantom_cells": sum(_phantom(a) for _h, network in exposed.values() for a in network)})
    return pd.DataFrame(rows)


def claim_weights(tools: Tools, world: World, exposed: dict) -> tuple[list, dict[str, float], dict[str, float]]:
    """Where a claim lands, exactly, under the corpus allocation model: specialty ~ official volume;
    state ~ admissions; district ~ sqrt(providers) x sqrt(population); hospital uniform in the district.

    Returns, for each exposed hospital, (claim mass on specialties it can plausibly deliver, claim mass on its
    implausible sole listings, hospital, network, state) -- every other hospital is always 'clear' -- and the total
    claim mass by state and by hospital type, the denominators for conditional shares."""
    specs = [s for s in world.specialty_weight
             if any((st, s) in world.providers for st in world.by_state_district)]
    total = sum(world.specialty_weight[s] for s in specs)
    mass: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    state_of: dict[str, str] = {}
    by_state, by_type = defaultdict(float), defaultdict(float)
    for s in specs:
        frames = {st: world.providers[(st, s)] for st in world.by_state_district if (st, s) in world.providers}
        states = [st for st in frames if world.state_weight.get(st, 0) > 0 and any(frames[st].values())]
        sw = sum(world.state_weight[st] for st in states)
        for st in states:
            dist = {d: hs for d, hs in frames[st].items() if hs}
            dw = {d: math.sqrt(len(hs)) * math.sqrt(max(world.pop.get(d, 0), 1.0)) for d, hs in dist.items()}
            tot = sum(dw.values())
            for d, hs in dist.items():
                p_cell = world.specialty_weight[s] / total * world.state_weight[st] / sw * dw[d] / tot
                by_state[st] += p_cell
                for h in hs:
                    by_type[h.hospital_type] += p_cell / len(hs)
                    if h.hospital_ref in exposed:
                        billed = next(a for a in exposed[h.hospital_ref][1] if a.specialty == s)
                        mass[h.hospital_ref][1 if _phantom(billed) else 0] += p_cell / len(hs)
                        state_of[h.hospital_ref] = st
    out = [(m[0], m[1], *exposed[ref], state_of[ref]) for ref, m in sorted(mass.items())]
    return out, dict(by_state), dict(by_type)


def weighted_gate(weights: list, km: float) -> dict[str, float]:
    """Gate shares of all claim mass. A billed specialty whose listing is implausible is a de-listing referral
    whatever else the hospital provides; otherwise the gate outcome is the hospital's, whichever specialty billed."""
    p = defaultdict(float)
    for w, w_phantom, _h, network, _st in weights:
        p["phantom"] += w_phantom
        gate, _, _ = policy.suspension_gate(None, network, distance_km=km)
        if gate == "clear" and _sole_real(network):
            gate = "loss"
        p[gate] += w
    return dict(p)


def weighted_sweep(weights: list, egregious_per_year: int) -> pd.DataFrame:
    rows = []
    for km in SWEEP_KM:
        p = weighted_gate(weights, km)
        rows.append({"km": km, "p_escalated": round(p.get("protect", 0), 5),
                     "p_removes_only_provider": round(p.get("loss", 0), 5),
                     "sec_escalations_per_year": round(p.get("protect", 0) * egregious_per_year),
                     "suspensions_removing_only_provider_per_year": round(p.get("loss", 0) * egregious_per_year)})
    return pd.DataFrame(rows)


def gate_breakdown(weights: list, km: float, key) -> dict[str, dict[str, float]]:
    """Gate claim mass by a grouping (state, hospital type); divide by the group's mass for a conditional share."""
    p = defaultdict(lambda: defaultdict(float))
    for w, w_phantom, h, network, st in weights:
        gate, _, _ = policy.suspension_gate(None, network, distance_km=km)
        p[key(h, st)][gate] += w
        p[key(h, st)]["phantom"] += w_phantom
    return p


def sweep_svg(df: pd.DataFrame, chosen: float) -> str:
    W, H, L, R, T, B = 720, 360, 64, 160, 24, 48
    xs = lambda km: L + (W - L - R) * km / 200                                          # noqa: E731
    top = max(df.hospitals_escalated.max(), df.hospitals_suspended_removing_only_provider.max()) * 1.1
    ys = lambda v: T + (H - T - B) * (1 - v / top)                                      # noqa: E731

    def line(col, colour):
        pts = " ".join(f"{xs(r.km):.1f},{ys(getattr(r, col)):.1f}" for r in df.itertuples())
        return f'<polyline fill="none" stroke="{colour}" stroke-width="2.5" points="{pts}"/>'
    ticks = "".join(f'<line x1="{xs(k)}" y1="{H-B}" x2="{xs(k)}" y2="{H-B+5}" stroke="#555"/>'
                    f'<text x="{xs(k)}" y="{H-B+20}" font-size="12" text-anchor="middle">{k}</text>'
                    for k in range(0, 201, 25))
    step = 500 if top > 2000 else 250
    yt = "".join(f'<line x1="{L-5}" y1="{ys(v):.1f}" x2="{W-R}" y2="{ys(v):.1f}" stroke="#eee"/>'
                 f'<text x="{L-9}" y="{ys(v)+4:.1f}" font-size="12" text-anchor="end">{int(v):,}</text>'
                 for v in np.arange(0, top, step))
    last = df.iloc[-1]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui, sans-serif">
<rect width="{W}" height="{H}" fill="#fff"/>{yt}
<line x1="{L}" y1="{H-B}" x2="{W-R}" y2="{H-B}" stroke="#555"/><line x1="{L}" y1="{T}" x2="{L}" y2="{H-B}" stroke="#555"/>{ticks}
<line x1="{xs(chosen)}" y1="{T}" x2="{xs(chosen)}" y2="{H-B}" stroke="#888" stroke-dasharray="4 4"/>
<text x="{xs(chosen)+6}" y="{T+14}" font-size="12" fill="#555">chosen: {chosen:g} km</text>
{line("hospitals_escalated", "#1f6feb")}{line("hospitals_suspended_removing_only_provider", "#d1242f")}
<text x="{W-R+8}" y="{ys(last.hospitals_escalated)+4:.1f}" font-size="12" fill="#1f6feb">escalated to the SEC</text>
<text x="{W-R+8}" y="{ys(last.hospitals_suspended_removing_only_provider)-10:.1f}" font-size="12" fill="#d1242f">suspended: a district</text>
<text x="{W-R+8}" y="{ys(last.hospitals_suspended_removing_only_provider)+4:.1f}" font-size="12" fill="#d1242f">loses an only provider</text>
<text x="{(L+W-R)/2}" y="{H-8}" font-size="13" text-anchor="middle">DISTANCE_MATERIAL_KM: a sole provider is protected when its nearest alternative is at least this far</text>
<text x="16" y="{(T+H-B)/2}" font-size="13" text-anchor="middle" transform="rotate(-90 16 {(T+H-B)/2})">real hospitals</text>
</svg>"""


# ── calibration evidence for the district allocation weight ───────────────

# Published district names that are not today's spelling. Renamed districts must be named: fuzzy matching sent
# Faizabad (now Ayodhya) to Firozabad, a different district, and dropped Allahabad (now Prayagraj) (F-47).
DISTRICT_ALIASES = {"allahabad": "prayagraj", "faizabad": "ayodhya", "dohad": "dahod", "ahmadabad": "ahmedabad",
                    "dang": "dangs", "maharajganj": "mahrajganj", "santkabeernagar": "santkabirnagar",
                    "shravasti": "shrawasti"}


def _norm_district(name: str) -> str:
    return re.sub(r"[^a-z]", "", str(name).lower())


def match_districts(names: list[str], frame: pd.DataFrame) -> list[int]:
    """District codes for published names, in order: exact, then a named alias, then a close spelling that no other
    name has claimed. Every name must match exactly one district, or the run fails rather than fit on a wrong pair."""
    keys = dict(zip(frame.district.map(_norm_district), frame.district_code.astype(int)))
    codes: list[int | None] = []
    for name in names:
        k = _norm_district(name)
        k = k if k in keys else DISTRICT_ALIASES.get(k, k)
        codes.append(keys.get(k))
    for i, name in enumerate(names):
        if codes[i] is None:
            free = [k for k, c in keys.items() if c not in codes]
            close = difflib.get_close_matches(_norm_district(name), free, n=1, cutoff=0.85)
            codes[i] = keys[close[0]] if close else None
    unmatched = [n for n, c in zip(names, codes) if c is None]
    twice = sorted({c for c in codes if c is not None and codes.count(c) > 1})
    if unmatched or twice:
        raise ValueError(f"district names unmatched {unmatched} or matched twice {twice}: extend DISTRICT_ALIASES")
    return [int(c) for c in codes]


def allocation_fit(tools: Tools) -> list[dict]:
    pts = pd.read_csv(REF / "district_points.csv", keep_default_na=False)
    pts["pop"] = pd.to_numeric(pts.pop_now, errors="coerce")
    pop = dict(zip(pts.district_code.astype(int), pts["pop"]))
    hosp = Counter(h.district_code for h in tools.hospitals())
    out = []
    for state, slug, col, vintage in [
            ("Uttar Pradesh", "admissions_district_up_2021_22", "No. of Authorized Hospital Admissions", "2021-22"),
            ("Gujarat", "admissions_district_gujarat_2023", "Number of Authorized Hospital Admissions", "to Aug 2023")]:
        t = pd.read_csv(OGD / f"{slug}.csv", keep_default_na=False)
        t = t[~t.District.str.contains("Outside|Total", case=False)]
        codes = match_districts(t.District.tolist(), pts[pts.state.str.upper() == state.upper()])
        rows = [(c, float(v)) for c, v in zip(codes, t[col])]
        m = pd.DataFrame(rows, columns=["dc", "adm"]).groupby("dc").adm.sum().to_frame()
        m["hosp"] = m.index.map(lambda d: hosp.get(d, 0))
        m["pop"] = m.index.map(pop)
        m = m[(m.hosp > 0) & (m["pop"] > 0) & (m.adm > 0)]
        y = np.log(m.adm.to_numpy())

        def fit(*cols, m=m, y=y):
            X = np.column_stack([np.ones(len(m))] + [np.log(m[c].to_numpy(dtype=float)) for c in cols])
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            return 1 - ((y - X @ beta) ** 2).sum() / ((y - y.mean()) ** 2).sum(), beta
        r2h, _ = fit("hosp")
        r2p, _ = fit("pop")
        r2b, beta = fit("hosp", "pop")
        used = np.log(np.sqrt(m.hosp.to_numpy(dtype=float)) * np.sqrt(m["pop"].to_numpy(dtype=float)))
        out.append({"state": state, "vintage": vintage, "districts": len(m), "districts_published": len(t),
                    "r2_hospitals": round(float(r2h), 3), "r2_population": round(float(r2p), 3),
                    "r2_both": round(float(r2b), 3), "beta_hospitals": round(float(beta[1]), 2),
                    "beta_population": round(float(beta[2]), 2),
                    "r2_sqrt_sqrt_weight": round(float(np.corrcoef(used, y)[0, 1]) ** 2, 3)})
    return out


# ── runs, ablation, sensitivity ────────────────────────────────────────────

def run_config(tools: Tools, world: World, cfg: CorpusConfig, official: dict) -> tuple[list[Outcome], dict]:
    cases, store, builder = build_corpus(tools, cfg, world)
    outcomes = adjudicate(cases, store, tools, builder)
    return outcomes, summarise(outcomes, cfg, official)


@contextmanager
def as_first_built(register_is_proof: bool, one_field_channel: bool):
    """Temporarily restore the behaviour this evaluation replaced, to measure what the change bought."""
    desk, specs = rules_path.desk_audit, dict(triggers.SPECS)

    def old_desk(claim, hit, package, store):
        if hit.trigger_id == "T10" and triggers.died_before_admission(claim):
            days = (claim.admission_ts.date() - claim.beneficiary_death_ts.date()).days
            return AgentFinding(agent="desk_audit", channel=Channel.DESK_AUDIT, supports_fraud=True, confidence=0.95,
                                conclusion=f"Admission recorded {days} days after the registered death.",
                                citation="beneficiary death registry")
        return desk(claim, hit, package, store)
    try:
        if register_is_proof:
            rules_path.desk_audit = old_desk
        if one_field_channel:
            triggers.SPECS["T6"] = replace(specs["T6"], field_channels=(Channel.HOSPITAL_VISIT,))
            triggers.SPECS["T10"] = replace(specs["T10"], field_channels=(Channel.BENEFICIARY_VISIT,))
        yield
    finally:
        rules_path.desk_audit = desk
        triggers.SPECS.clear()
        triggers.SPECS.update(specs)


def ablation(tools: Tools, world: World, cfg: CorpusConfig, official: dict) -> pd.DataFrame:
    variants = [
        ("As first built: a death-register entry is proof; one field channel", True, True),
        ("E-1 fixed: a register entry without a certificate is a lead", False, True),
        ("E-1 + E-2 fixed: egregious triggers order two field channels (current)", False, False),
    ]
    rows = []
    for label, register_is_proof, one_channel in variants:
        with as_first_built(register_is_proof, one_channel):
            outcomes, s = run_config(tools, world, cfg, official)
        t10 = [o for o in outcomes if o.case.trigger == "T10" and is_innocent(o)]
        rows.append({"variant": label, "auto_resolution": s["auto_resolution_share"],
                     "to_human": s["human_share"], "innocent_suspended": s["innocent"]["suspended"],
                     "innocent_t10_suspended": share(t10, lambda o: o.final.action is Action.SUSPEND),
                     "fraud_enforced": s["fraud"]["enforced"],
                     "wrongful_suspensions_per_year": s["per_year"]["wrongful_suspensions"],
                     "human_reviews_per_year": s["per_year"]["human_reviews"]})
        print(f"    {label[:60]:<60} wrongful suspensions/yr {s['per_year']['wrongful_suspensions']:,}")
    return pd.DataFrame(rows)


SENSITIVITY = [
    ("fpr", [0.005, 0.02, 0.05]),
    ("p_explained", [0.30, 0.90]),
    ("field_accuracy", [0.70, 0.95]),
    ("field_conf", [(0.50, 0.85), (0.75, 0.95)]),
    ("p_certificate", [0.50, 1.00]),
]


def sensitivity(tools: Tools, world: World, base: CorpusConfig, official: dict, base_summary: dict) -> pd.DataFrame:
    def row(name, value, s):
        return {"parameter": name, "value": str(value), "fraud_share_of_flags": s["fraud_share_of_flags"],
                "auto_resolution": s["auto_resolution_share"], "resolved_at_desk": s["resolved_at_desk_share"],
                "field_audit": s["field_audit_share"], "to_human": s["human_share"],
                "innocent_show_cause": s["innocent"]["show_cause"], "innocent_suspended": s["innocent"]["suspended"],
                "fraud_enforced": s["fraud"]["enforced"], "fraud_released": s["fraud"]["released"],
                "flags_per_year": s["per_year"]["flags"], "human_reviews_per_year": s["per_year"]["human_reviews"],
                "wrongful_suspensions_per_year": s["per_year"]["wrongful_suspensions"]}
    rows = [row("baseline", "-", base_summary)]
    for name, values in SENSITIVITY:
        for v in values:
            _, s = run_config(tools, world, replace(base, **{name: v}), official)
            rows.append(row(name, v, s))
            print(f"    {name}={v}: auto {s['auto_resolution_share']:.1%}, human {s['human_share']:.1%}")
    return pd.DataFrame(rows)


# ── the write-up ───────────────────────────────────────────────────────────

def pct(x: float, d: int = 1) -> str:
    return "–" if x != x else f"{x * 100:.{d}f}%"


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(lines)


def write_doc(m: dict, cfg: CorpusConfig, sweep: pd.DataFrame, wsweep: pd.DataFrame, di: pd.DataFrame,
              abl: pd.DataFrame | None, sens: pd.DataFrame | None, fit: list[dict], figures: dict,
              doc: Path = DOC, chart: str = "../results/threshold_sweep.svg") -> None:
    o, s = figures["official"], m["summary"]
    py = s["per_year"]
    g = m["gate_at_chosen_km"]
    chosen = policy.DISTANCE_MATERIAL_KM
    at = lambda df, km: df[df.km == km].iloc[0]                                        # noqa: E731
    sw0, swc, sw100, sw200 = at(sweep, 0), at(sweep, int(chosen)), at(sweep, 100), at(sweep, 200)
    wc = at(wsweep, int(chosen))

    trig = pd.DataFrame(m["by_trigger"])
    trig_view = pd.DataFrame({
        "Trigger": trig.trigger, "Cases": trig.cases, "Fraud": trig.fraud_share.map(pct),
        "Field audit": trig.field_audited.map(pct), "Released": trig.release_claim.map(pct),
        "Show-cause": trig.show_cause_notice.map(pct), "Suspended": trig.suspend_hospital.map(pct),
        "Escalated": trig.escalate_to_sec.map(pct), "De-listing referral": trig.delist_specialty.map(pct),
        "Human review": trig.no_action_review.map(pct)})
    di_view = pd.DataFrame({
        "Hospital type": di.hospital_type, "Network share": di.network_pct.map(lambda v: f"{v}%"),
        "Sole-provider share": di.sole_provider_pct.map(lambda v: f"{v}%"), "Cases": di.cases,
        "Auto-resolved": di.auto_resolution.map(pct), "To a human": di.to_human.map(pct),
        "Suspended": di.suspended.map(pct), "Show-cause": di.show_cause.map(pct),
        "Innocent flags given an adverse action": di.innocent_adverse.map(pct),
        "Egregious findings escalated (exact)": di.egregious_escalated_exact.map(lambda v: pct(v, 2))})
    fit_view = pd.DataFrame([{"State": f["state"], "Admissions data": f["vintage"],
                              "Districts matched": f"{f['districts']} of {f['districts_published']}",
                              "R² hospitals only": f["r2_hospitals"], "R² population only": f["r2_population"],
                              "R² both (fitted)": f["r2_both"],
                              "Fitted exponents (hospitals, population)": f"{f['beta_hospitals']}, {f['beta_population']}",
                              "R² of the √×√ weight used": f["r2_sqrt_sqrt_weight"]} for f in fit])
    # Stated from the numbers, not asserted: the fixed prose here ("similar exponents in both states") stopped
    # being true once the district matching was corrected (F-47).
    both = [f"R² {f['r2_both']} against {f['r2_hospitals']} (hospitals) and {f['r2_population']} (population) in "
            f"{f['state']}" for f in fit]
    exps = [f"{f['beta_hospitals']} and {f['beta_population']} in {f['state']}" for f in fit]
    sqrt = [f"{f['r2_sqrt_sqrt_weight']} in {f['state']}" for f in fit]
    better = all(f["r2_both"] >= max(f["r2_hospitals"], f["r2_population"]) for f in fit)
    fit_prose = (f"{'In every state, hospitals and population together explain' if better else 'Hospitals and population together do not always explain'} "
                 f"district admissions {'better than either alone' if better else 'better than one of them alone'} "
                 f"({'; '.join(both)}). The fitted exponents (hospitals, population) are {'; '.join(exps)}. "
                 f"The generator is fitted to neither state: it uses the square-root weight (0.5, 0.5), whose R² is "
                 f"{'; '.join(sqrt)}.")
    rows = sweep[sweep.km.isin([0, 25, 50, 75, 100, 150, 200])].merge(wsweep, on="km")
    sweep_view = pd.DataFrame({
        "km": rows.km, "Hospitals escalated": rows.hospitals_escalated.map("{:,}".format),
        "Hospitals suspended, removing an only provider": rows.hospitals_suspended_removing_only_provider.map("{:,}".format),
        "Cells losing their only provider": rows.cells_losing_only_provider.map("{:,}".format),
        "Districts losing a specialty": rows.districts_losing_a_specialty,
        "Population of those districts (M)": rows.population_M_of_those_districts,
        "SEC escalations / year": rows.sec_escalations_per_year.map("{:,}".format),
        "Suspensions removing an only provider / year": rows.suspensions_removing_only_provider_per_year.map("{:,}".format)})
    state_view = pd.DataFrame(m["by_state"]).rename(columns={
        "state": "State", "sec_escalations_per_year": "SEC escalations / year",
        "share_of_egregious_escalated": "Share of the state's egregious findings escalated"})
    if len(state_view):
        state_view["Share of the state's egregious findings escalated"] = \
            state_view["Share of the state's egregious findings escalated"].map(lambda v: pct(v))

    abl_block = "*Not run (`--quick`).*"
    if abl is not None:
        av = abl.copy()
        for c in ("auto_resolution", "to_human", "innocent_suspended", "innocent_t10_suspended", "fraud_enforced"):
            av[c] = av[c].map(pct)
        for c in ("wrongful_suspensions_per_year", "human_reviews_per_year"):
            av[c] = av[c].map("{:,}".format)
        abl_block = md_table(av.rename(columns={
            "variant": "Variant", "auto_resolution": "Auto-resolved", "to_human": "To a human",
            "innocent_suspended": "Innocent flags suspended", "innocent_t10_suspended": "Innocent T10 flags suspended",
            "fraud_enforced": "Frauds enforced", "wrongful_suspensions_per_year": "Wrongful suspensions / year",
            "human_reviews_per_year": "Human reviews / year"}))
        first, now = abl.iloc[0], abl.iloc[-1]
        abl_block += (f"\n\nTogether the two changes cut wrongful suspensions from **{first.wrongful_suspensions_per_year:,}** "
                      f"to **{now.wrongful_suspensions_per_year:,} a year**, while frauds enforced moved from "
                      f"{pct(first.fraud_enforced)} to {pct(now.fraud_enforced)} and human reviews from "
                      f"{first.human_reviews_per_year:,} to {now.human_reviews_per_year:,}. That is the trade: fewer "
                      f"innocent hospitals suspended, paid for in human review.")

    sens_block = "*Not run (`--quick`).*"
    if sens is not None:
        sv = sens.copy()
        for c in ("fraud_share_of_flags", "auto_resolution", "resolved_at_desk", "field_audit", "to_human",
                  "innocent_show_cause", "innocent_suspended", "fraud_enforced", "fraud_released"):
            sv[c] = sv[c].map(lambda v, c=c: pct(v, 2 if c == "innocent_suspended" else 1))
        for c in ("flags_per_year", "human_reviews_per_year", "wrongful_suspensions_per_year"):
            sv[c] = sv[c].map("{:,}".format)
        sens_block = md_table(sv.rename(columns={
            "parameter": "Assumption", "value": "Value", "fraud_share_of_flags": "Fraud share of flags",
            "auto_resolution": "Auto-resolved", "resolved_at_desk": "At desk", "field_audit": "Field audit",
            "to_human": "To a human", "innocent_show_cause": "Innocent: notice",
            "innocent_suspended": "Innocent: suspended", "fraud_enforced": "Fraud enforced",
            "fraud_released": "Fraud released", "flags_per_year": "Flags / yr", "human_reviews_per_year": "Human / yr",
            "wrongful_suspensions_per_year": "Wrongful susp. / yr"}))
        varied = sens[sens.parameter != "baseline"]
        spread = lambda col: varied.groupby("parameter")[col].agg(lambda v: v.max() - v.min()).idxmax()  # noqa: E731
        lo_w, hi_w = varied.loc[varied.wrongful_suspensions_per_year.idxmin()], varied.loc[varied.wrongful_suspensions_per_year.idxmax()]
        sens_block += (f"\n\nAcross every sweep, auto-resolution stays between **{pct(sens.auto_resolution.min())}** and "
                       f"**{pct(sens.auto_resolution.max())}**; the assumption that moves it most is `{spread('auto_resolution')}`, "
                       f"because field reports near the {policy.CONFIDENCE_FLOOR} confidence floor go to a human. "
                       f"**Wrongful suspensions are most sensitive to `{spread('wrongful_suspensions_per_year')}`**: "
                       f"{int(lo_w.wrongful_suspensions_per_year):,} a year at `{lo_w.parameter}={lo_w.value}`, "
                       f"{int(hi_w.wrongful_suspensions_per_year):,} at `{hi_w.parameter}={hi_w.value}`. The safety of "
                       f"automatic suspension rests on the quality of field verification more than on anything in "
                       f"this code — which is why two independent channels are required.")

    dep = o["deempanelled_2018_25"]
    rng = o["nonadmissible_share_range_pct_states_over_1000cr"]
    priv_sole = figures["fairness"]["Private(For Profit)"]["sole_pct"] + figures["fairness"]["Private(Not For Profit)"]["sole_pct"]

    text = f"""# 09 · Evaluation — the numbers beyond pass/fail

> **Generated by `python -m metrics.run` — do not edit by hand.** Re-run it after any change to the
> policy, triggers or data, and commit the result with the code that produced it.
> Corpus: {s['flagged_cases']:,} flagged cases, seed {cfg.seed}. Every case ran through the real pipeline
> (`crew.run.process`, rules path); cases ordered to the field re-entered with simulated field reports.

The ten scenarios (docs/05-TEST-PLAN.md §2) prove each path works. This document measures **how often**
each path is taken when flags arrive in realistic proportions, what that means at national scale, what the
evaluation forced us to change, and how much rests on assumptions.

**What this does not measure: the agents.** The corpus documents are neutral by design, so every desk reading here
is the rules path's. They measure the policy and the access gate, which decide identically on both paths. Whether the
crew reads documents better or worse than the rules is measured in
[10-AGENT-EVALUATION](10-AGENT-EVALUATION.md), on 40 cases whose truth is in the text.

---

## 1. Headline

| | |
|---|---|
| **Auto-resolution share** — flags that reached a final action with no human | **{pct(s['auto_resolution_share'])}** |
| Resolved at the desk, no field work | {pct(s['resolved_at_desk_share'])} |
| Needed a field audit first | {pct(s['field_audit_share'])} |
| Went to a human (SEC escalation, de-listing referral, or review) | {pct(s['human_share'])} |
| Innocent flags released | {pct(s['innocent']['released'])} |
| Innocent flags given a show-cause notice (reversible; 5 days to answer) | {pct(s['innocent']['show_cause'])} |
| **Innocent flags suspended** | **{pct(s['innocent']['suspended'], 2)}** |
| Frauds enforced against (notice, suspension, escalation or de-listing referral) | {pct(s['fraud']['enforced'])} |
| Frauds released | {pct(s['fraud']['released'])} |

**At national scale** — {o['fy']}: {o['admissions']:,} admissions; {cfg.base_rate:.2%} fraud, {cfg.fpr:.0%} flag
false-positive rate, {cfg.recall:.0%} recall → **{py['flags']:,} flags a year, {py['flags_per_day']:,} a day**:

| Per year | |
|---|---|
| Field audits ordered | {py['field_audits']:,} |
| Human reviews, all kinds | {py['human_reviews']:,} |
| Confirmed egregious findings (reach the access gate) | {py['confirmed_egregious']:,} |
| → escalated to State Empanelment Committees (exact, §4) | {int(wc.sec_escalations_per_year):,} |
| Suspensions | {py['suspensions']:,} |
| → of innocent hospitals | {py['wrongful_suspensions']:,} |
| Show-cause notices | {py['show_cause_notices']:,} (to innocent hospitals: {py['show_cause_to_innocent']:,}) |

All counts are **claim-level**. Several flagged claims from one hospital become one hospital case, so
hospital-level workload is lower.

### The thesis, measured

Of confirmed egregious findings, **{pct(g['protect'], 2)}** land on a hospital whose suspension would leave its
district without a real provider of some specialty — suspension removes a hospital from every specialty it is
empanelled for, so the gate checks them all — and are escalated; **{pct(g['phantom'], 2)}** are billed under a sole
listing whose facility tier makes it implausible (de-listing referral). Without the gate, every one of those
**{pct(g['protect'] + g['phantom'], 2)}** would have been suspended automatically, about
**{round((g['protect'] + g['phantom']) * py['confirmed_egregious']):,} a year**. A further **{pct(g['removes_only_provider'], 2)}**
are suspended although the hospital is its district's only provider of something, because another provider is within
{chosen:g} km (§4). Computed exactly from the allocation model (§2), not sampled.

Where those escalations land ({chosen:g} km):

{md_table(state_view)}

---

## 2. The corpus — what is real and what is assumed

| Element | Source | Status |
|---|---|---|
| State of each claim | Admissions {o['fy']} by state (Rajya Sabha) | **Real** |
| Specialty | Authorised admissions by specialty, answer of 30-06-2024 (4 specialties from the 2021 answer, scaled ×{o['specialty_2021_scale']}) | **Real** |
| District within the state | weight = √(providers of the specialty) × √(population) | **Calibrated on real data** (below) |
| Hospital | Uniform among the district's registry providers of the specialty | Real frame, assumed rule |
| Package and amount | Published HBP package master | **Real** |
| Fraud share of flags ({pct(cfg.fraud_share_of_flags)}) | 0.18% base rate (PIB PRID 1847423), {cfg.fpr:.0%} FPR, {cfg.recall:.0%} recall | Real rate; FPR and recall assumed |
| Which trigger a fraud or a false flag fires | Uniform — NHA publishes no breakdown | **Assumption** |
| Innocent explanation on file at the desk | {cfg.p_explained:.0%} | **Assumption** — swept |
| Death certificate on file when billing after death is real | {cfg.p_certificate:.0%} | **Assumption** — swept |
| Field report points the right way | {cfg.field_accuracy:.0%} | **Assumption** — swept |
| Field report confidence | uniform {cfg.field_conf[0]:.2f}–{cfg.field_conf[1]:.2f} | **Assumption** — swept |

**Nothing is trained on this corpus.** It is input data for measurement only.

### Why that district weight

District admissions in the two states that publish them, regressed (log-log) on registry hospitals and population:

{md_table(fit_view)}

{fit_prose}

---

## 3. Outcomes by trigger

{md_table(trig_view)}

The desk settles what documents can settle: T4, R1–R3, and T10 when a death certificate dates the death before
admission. What only a person on the ground can resolve (T5, T7, T2/T3 with no discharge explanation, T10 on a
register entry alone) goes to the field first — and so does T6: the desk finds the reused document, but suspension
waits for a hospital visit and a beneficiary call, because at the desk reuse and a clerical upload error look the
same. That is the branching the design promised, at volume.

---

## 4. Threshold sweep — why {chosen:g} km

`DISTANCE_MATERIAL_KM` decides when a sole provider is protected: **protected if its nearest alternative is at
least this far away**. At 0 km every sole provider is protected; raising the threshold protects fewer.

The left columns take **every real hospital that is its district's only provider of some specialty, or one of two in
a NITI aspirational district**, and ask what the gate would do if it were confirmed to have committed egregious fraud
in a specialty it can plausibly deliver — no simulation. Suspension removes a hospital from every specialty it is
empanelled for, so a hospital is escalated if losing it would strip **any** of its specialties of their only real
provider. The right columns weight the same question by where claims actually arrive (§2) and scale it to confirmed
egregious findings per year.

![threshold sweep]({chart})

{md_table(sweep_view)}

- At **0 km**, {int(sw0.hospitals_escalated):,} hospitals would be escalated and no suspension would remove a district's only provider.
- At **{chosen:g} km**, {int(swc.hospitals_escalated):,} are escalated; {int(swc.hospitals_suspended_removing_only_provider):,} would be
  suspended, removing the only provider of {int(swc.cells_losing_only_provider):,} district × specialty cells in
  {int(swc.districts_losing_a_specialty)} districts — each with an alternative within {chosen:g} km.
- At **100 km**, escalations fall to {int(sw100.hospitals_escalated):,}, and suspensions would remove the only provider of
  {int(sw100.cells_losing_only_provider):,} cells in {int(sw100.districts_losing_a_specialty)} districts ({sw100.population_M_of_those_districts} M people).
- Even at **200 km**, {int(sw200.hospitals_escalated):,} hospitals stay protected: the aspirational-district rule and sole
  providers with no alternative anywhere are protected at every threshold.

**The operating point is a policy choice, not a statistical one.** Each kilometre added to the threshold
protects fewer districts and saves Committee time; this table is the price list. {chosen:g} km is the median distance
to an alternative (50.7 km), so it splits sole-provider cells roughly in half — a defensible default for a
Committee to move, not a finding.

---

## 5. Disparate-impact audit

{md_table(di_view)}

Claims are placed by where admissions happen, not by hospital type, so differences between types come from
**where each type sits in the network**. Public hospitals and not-for-profits hold more sole-provider slots than
their network share, so more of their egregious findings are escalated rather than suspended (last column, exact).
R3 (government-reserved packages) can only fire on private hospitals, which raises their show-cause share.
**The gate is ownership-blind; its effects are not.** We report both.

**Official context.** De-empanelments from 2018-19 to 2024-25 fell on **{dep['private']:,} private and {dep['public']:,}
public hospitals ({dep['public_share_pct']}% public)**, although public hospitals are 55% of the network.
Private hospitals also hold {priv_sole:.1f}% of sole-provider slots. The published de-empanelment guidance we
reviewed sets no check on what a de-empanelment does to access; the gate adds one that applies to every owner.

---

## 6. What the evaluation forced us to change

The first run of this corpus found two ways the system suspended innocent hospitals. Both are fixed in the
code; the table re-runs the same corpus with each fix switched off.

- **E-1 · A register entry treated as proof.** The desk read a date in the death register that preceded
  admission as 0.95-confidence fraud. A register error therefore produced a suspension with no one checking.
  NHA's own checklist for trigger 10 verifies the mismatch by asking the relatives and for the death certificate.
  **Now:** with a certificate on file the desk still decides (S1, S2 and S9 are unchanged); without one, the case goes to the field.
- **E-2 · One field report could suspend a hospital.** Egregious triggers ordered a single field channel, so one
  wrong report above the confidence floor was enough. **Now:** every egregious trigger orders two independent
  channels (the guidebook lists both for T6 and T10); a disagreement falls below the floor and goes to a human.
  Locked by `test_no_single_field_report_can_suspend_a_hospital`.

{abl_block}

---

## 7. Sensitivity — how much rests on the assumptions

One assumption varied at a time; everything else at baseline; same seed.

{sens_block}

**Known limitations**

1. **T6 is assumed never to fire innocently.** A clerical mix-up that filed one patient's discharge summary
   under another reads as document reuse. It is no longer suspended at the desk, but it costs a hospital visit
   and a beneficiary call, and two wrong field reports could still suspend.
2. **Uniform trigger mixes.** NHA publishes no breakdown of fraud or false flags by trigger. A different mix
   moves the auto-resolution share; §3 shows which way.
3. **Field reports are modelled, not observed.** Their confidence relative to the {policy.CONFIDENCE_FLOOR} floor sets
   the human-review share almost directly.
4. **Frauds the triggers never flag ({1 - cfg.recall:.0%} by assumption) are outside this system and these numbers.**
5. **Claim-level, not hospital-level.** Repeat offenders collapse into single hospital cases in practice.
6. **R2's desk rule accepts an implant invoice as the explanation for any package.** The package master has no clean
   implant flag to restrict it to (its "barcode" requirements mean drug barcodes as often as implants), so an
   invoice on a package that uses no implant would still clear an amount above the rate.

---

## 8. Official context used above

| Figure | Value | Source |
|---|---|---|
| Hospital admissions {o['fy']} | {o['admissions']:,} (Rs {o['amount_cr']:,} crore; average Rs {o['avg_claim_rs']:,}) | Rajya Sabha answer |
| Expected confirmed frauds a year at 0.18% | {o['expected_confirmed_fraud']:,} ({o['expected_confirmed_fraud_per_day']} a day) | PIB 1847423 × admissions |
| Innocent share of flags at 1% FPR, 90% recall | {o['innocent_share_of_flags_pct']}% | arithmetic |
| Empanelled hospitals, 01-03-2025 | {o['hospitals_official_2025_03']:,} | Rajya Sabha answer |
| Our registry export, PMJAY scope | {o['hospitals_registry_pmjay_scope']:,} ({o['registry_vs_official_pct']:+}%; {o['states_exact_match']} of {o['states_compared']} states exact, {o['states_within_10']} within 10) | hospitals.pmjay.gov.in |
| De-empanelled 2018-19 to 2024-25 | {dep['private']:,} private, {dep['public']:,} public | Rajya Sabha answer |
| Largest single state-year | {o['deempanelled_peak_state_year']['hospitals']} hospitals, {o['deempanelled_peak_state_year']['state']}, {o['deempanelled_peak_state_year']['year']} | Rajya Sabha answer |
| Hospitals that opted out, 2019-20 to 2024-25 | {o['opted_out_2019_25']:,} | Rajya Sabha answer |
| Claims found non-admissible for abuse, misuse or incorrect entries, private hospitals, to 14-01-2025 | Rs {o['nonadmissible_private_cr_as_on_2025_01_14']:,} crore across {o['nonadmissible_states_reporting']} states; {', '.join(o['nonadmissible_top3'])} = {o['nonadmissible_top3_share_pct']}% | Rajya Sabha answer |
| …as a share of claim value, states over Rs 1,000 crore | {rng['min']}% ({rng['min_state']}) to {rng['max']}% ({rng['max_state']}) | derived |
| Beneficiaries linked to a single mobile number, 2018-21 | {o['single_mobile_beneficiaries_2018_21']:,} | Rajya Sabha answer |
| Bahraich admissions, 2021-22 | {o['demo_districts']['bahraich_admissions_2021_22']:,} (UP district median {o['demo_districts']['up_district_admissions_2021_22_median']:,}) | Rajya Sabha answer |
"""
    doc.write_text(text, encoding="utf-8")


def rewrite_doc(figures: dict) -> None:
    """Rebuild the write-up from saved results, so prose can change without a full re-run."""
    missing = [n for n in ("metrics.json", "threshold_sweep.csv", "disparate_impact.csv") if not (RESULTS / n).exists()]
    if missing:
        raise SystemExit(f"--doc-only needs a full run's results; missing in {RESULTS}: {', '.join(missing)}")
    metrics = json.loads((RESULTS / "metrics.json").read_text(encoding="utf-8"))
    cfg_fields = dict(metrics["config"], field_conf=tuple(metrics["config"]["field_conf"]))
    both = pd.read_csv(RESULTS / "threshold_sweep.csv")
    sweep = both[["km", "hospitals_escalated", "hospitals_suspended_removing_only_provider",
                  "cells_losing_only_provider", "districts_losing_a_specialty", "population_M_of_those_districts",
                  "phantom_cells"]]
    wsweep = both[["km", "p_escalated", "p_removes_only_provider", "sec_escalations_per_year",
                   "suspensions_removing_only_provider_per_year"]]
    optional = lambda name: pd.read_csv(RESULTS / name) if (RESULTS / name).exists() else None  # noqa: E731
    write_doc(metrics, CorpusConfig(**cfg_fields), sweep, wsweep, pd.read_csv(RESULTS / "disparate_impact.csv"),
              optional("ablation.csv"), optional("sensitivity.csv"), metrics["allocation_fit"], figures)


def destination(n: int, quick: bool) -> tuple[Path, Path, bool]:
    """(results directory, write-up path, published?). Only the full default run is the published evaluation."""
    if not quick and n == CorpusConfig.n_flagged:
        return RESULTS, DOC, True
    trial = RESULTS / f"trial-n{n}{'-quick' if quick else ''}"
    return trial, trial / "09-EVALUATION.md", False


def _cases(value: str) -> int:
    n = int(value)
    if n < 50:
        raise argparse.ArgumentTypeError("--n must be at least 50: fewer leaves most triggers without a case")
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Measure the Access Gate on a simulated year of flags")
    ap.add_argument("--n", type=_cases, default=CorpusConfig.n_flagged)
    ap.add_argument("--quick", action="store_true", help="skip the ablation and sensitivity runs")
    ap.add_argument("--doc-only", action="store_true", help="regenerate docs/09-EVALUATION.md from results/")
    args = ap.parse_args()
    if args.doc_only and (args.quick or args.n != CorpusConfig.n_flagged):
        ap.error("--doc-only rewrites the published evaluation from results/; it takes no --n or --quick")

    t0 = time.time()
    out, doc, published = destination(args.n, args.quick)
    out.mkdir(parents=True, exist_ok=True)
    figures = json.loads((REF / "figures.json").read_text(encoding="utf-8"))
    official = figures["official"]
    if args.doc_only:
        rewrite_doc(figures)
        print(f"  written                    docs/09-EVALUATION.md from results/ in {time.time() - t0:.0f} s")
        return
    tools = Tools()
    world = World(tools)
    cfg = CorpusConfig(n_flagged=args.n)

    print(f"  corpus + adjudication      {cfg.n_flagged:,} flagged cases")
    outcomes, summary = run_config(tools, world, cfg, official)

    print("  access gate, exact         exposed hospitals + claim weights")
    exposed = _exposed(tools)
    weights, state_mass, type_mass = claim_weights(tools, world, exposed)
    chosen = policy.DISTANCE_MATERIAL_KM
    sweep = structural_sweep(exposed)
    egregious_yr = summary["per_year"]["confirmed_egregious"]
    wsweep = weighted_sweep(weights, egregious_yr)
    g = weighted_gate(weights, chosen)
    gate_at = {"protect": g.get("protect", 0.0), "phantom": g.get("phantom", 0.0),
               "removes_only_provider": g.get("loss", 0.0)}

    by_state = sorted(({"state": st.title(),
                        "sec_escalations_per_year": round(p.get("protect", 0) * egregious_yr),
                        "share_of_egregious_escalated": round(p.get("protect", 0) / state_mass[st], 4)}
                       for st, p in gate_breakdown(weights, chosen, lambda h, st: st).items()),
                      key=lambda r: -r["sec_escalations_per_year"])[:10]
    gate_by_type = {t: {k: v / type_mass[t] for k, v in p.items()}
                    for t, p in gate_breakdown(weights, chosen, lambda h, st: h.hospital_type).items()}
    di = disparate_impact(outcomes, figures, gate_by_type)
    fit = allocation_fit(tools)

    abl = sens = None
    if not args.quick:
        print("  ablation                   3 variants")
        abl = ablation(tools, world, cfg, official)
        abl.to_csv(out / "ablation.csv", index=False)
        print(f"  sensitivity                {sum(len(v) for _, v in SENSITIVITY)} configurations")
        sens = sensitivity(tools, world, cfg, official, summary)
        sens.to_csv(out / "sensitivity.csv", index=False)

    metrics = {"config": asdict(cfg), "summary": summary, "gate_at_chosen_km": gate_at,
               "distance_material_km": chosen, "by_trigger": by_trigger(outcomes), "by_state": by_state,
               "allocation_fit": fit}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    sweep.merge(wsweep, on="km").to_csv(out / "threshold_sweep.csv", index=False)
    (out / "threshold_sweep.svg").write_text(sweep_svg(sweep, chosen), encoding="utf-8")
    di.to_csv(out / "disparate_impact.csv", index=False)
    write_doc(metrics, cfg, sweep, wsweep, di, abl, sens, fit, figures, doc=doc,
              chart="../results/threshold_sweep.svg" if published else "threshold_sweep.svg")

    s = summary
    print(f"\n  auto-resolution            {pct(s['auto_resolution_share'])}  (desk {pct(s['resolved_at_desk_share'])}, "
          f"field {pct(s['field_audit_share'])}, human {pct(s['human_share'])})")
    print(f"  innocent                   released {pct(s['innocent']['released'])}, notice {pct(s['innocent']['show_cause'])}, "
          f"suspended {pct(s['innocent']['suspended'], 2)}")
    print(f"  fraud                      enforced {pct(s['fraud']['enforced'])}, released {pct(s['fraud']['released'])}")
    print(f"  gate at {chosen:g} km             escalated {pct(gate_at['protect'], 2)}, de-listing {pct(gate_at['phantom'], 2)} "
          f"of confirmed egregious findings")
    where = "results/ and docs/09-EVALUATION.md" if published else f"{out.relative_to(ROOT)} (a trial: the published evaluation is untouched)"
    print(f"  written                    {where} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
