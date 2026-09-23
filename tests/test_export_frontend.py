"""The front end's export against the code it describes.

`scripts/export_frontend.py` is standard-library only, so that a teammate can run it in a bare
checkout with nothing installed. The price is that it restates constants the pipeline already owns —
the roster, the confidence floor, the distance threshold, the action names. A restated constant
drifts silently: the export keeps working and starts describing a system that no longer exists.

These tests pin each copy to its source, so the drift fails here instead of on screen.
"""
import importlib.util
import re
from pathlib import Path

import pytest

from crew.schemas import AGENT_TITLES, Action
from rules import policy

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "export_frontend.py"


@pytest.fixture(scope="module")
def export():
    spec = importlib.util.spec_from_file_location("export_frontend", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_roster_is_the_one_the_crew_actually_has(export):
    """Same agents, same titles, same order — the order is how the front end lays out the roster."""
    assert export.AGENT_TITLES == AGENT_TITLES
    assert list(export.AGENT_TITLES) == list(AGENT_TITLES)


def test_every_agent_has_a_stated_question(export):
    assert sorted(export.AGENT_OWNS) == sorted(AGENT_TITLES)


def test_the_confidence_floor_is_the_policy_floor(export):
    assert export.CONFIDENCE_FLOOR == policy.CONFIDENCE_FLOOR


def test_the_distance_threshold_is_the_policy_threshold(export):
    assert export.DISTANCE_MATERIAL_KM == policy.DISTANCE_MATERIAL_KM


def test_every_action_is_grouped_and_labelled(export):
    actions = {a.value for a in Action}
    assert set(export.ACTION_GROUP) == actions
    assert set(export.ACTION_LABELS) == actions
    assert set(export.ACTION_GROUP.values()) == {"released", "deferred", "enforced", "refused"}


def test_only_the_release_is_grouped_as_released(export):
    """The grouping is what the case list filters on: an enforcement must never fall under 'released'."""
    released = {k for k, v in export.ACTION_GROUP.items() if v == "released"}
    assert released == {Action.RELEASE_CLAIM.value}


def test_every_gate_state_the_gate_can_return_has_a_label(export):
    assert set(export.GATE_LABELS) == {"clear", "protect", "phantom", "unknown"}


def test_every_track_the_router_can_open_names_a_real_agent(export):
    from crew.crew_llm import TRACKS

    assert set(export.TRACK_AGENT) == set(TRACKS)
    assert set(export.TRACK_AGENT.values()) <= set(AGENT_TITLES)


def test_every_reading_is_attributed_to_an_agent_on_the_roster(export):
    assert set(export.READING_AGENT.values()) <= set(AGENT_TITLES)
    assert sorted(export.READING_AGENT) == sorted(export.READING_TITLES)


def test_nothing_still_points_at_the_layout_from_before_the_move():
    """The front end arrived nested under a duplicate `access-gate/` tree and was flattened into the repo.

    Three files carried `--out ../frontend/static/data`, which was right when `frontend/` was a sibling of
    `access-gate/` and is wrong now that it is inside it. One of them was the error page, which is only ever
    seen when something is already broken — exactly where a wrong instruction does the most harm.
    """
    stale = re.compile(r"\.\./frontend/static/data|access-gate/scripts/export_frontend")
    hits = []
    for path in list((ROOT / "frontend/src").rglob("*")) + list((ROOT / "scripts").glob("*.py")) + [
        ROOT / "frontend/README.md", ROOT / "README.md"
    ]:
        if not path.is_file() or path.suffix not in (".ts", ".svelte", ".py", ".md", ".json", ".css"):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if stale.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{i}")
    assert hits == [], f"pre-move paths still referenced: {hits}"


def test_the_export_needs_nothing_installed():
    """Standard library only. A third-party import here breaks the bare-checkout promise in the README."""
    imports = re.findall(r"^(?:import|from) ([\w.]+)", SCRIPT.read_text(encoding="utf-8"), re.M)
    allowed = {"__future__", "argparse", "csv", "json", "re", "sys", "datetime", "pathlib"}
    assert set(imports) <= allowed, f"not standard library: {sorted(set(imports) - allowed)}"


def test_the_committed_demo_data_matches_the_run_it_claims():
    """out_live/ is committed so a clone has the real crewed run; the exported JSON must be that run.

    This is the check that would have caught the front end shipping the rules path: every case
    degraded, no agents, while the log on disk held a nine-agent run.
    """
    import csv
    import json

    data = ROOT / "frontend" / "static" / "data"
    log = ROOT / "out_live" / "decision_log.csv"
    if not data.exists() or not log.exists():
        pytest.skip("front end not exported in this checkout")

    cases = {c["claim_id"]: c for c in json.loads((data / "cases.json").read_text(encoding="utf-8"))}
    rows = {r["claim_id"]: r for r in csv.DictReader(log.open(newline="", encoding="utf-8"))}
    assert set(cases) == set(rows)
    for claim_id, row in rows.items():
        assert cases[claim_id]["action"] == row["action"], claim_id
        assert float(cases[claim_id]["confidence"]) == float(row["confidence"]), claim_id
        assert cases[claim_id]["degraded"] is (row["degraded"] == "True"), claim_id

    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    assert meta["counts"]["cases"] == len(rows)
    assert meta["counts"]["degraded"] == sum(r["degraded"] == "True" for r in rows.values())


def test_the_committed_demo_run_is_the_crews_work_and_not_the_rules():
    """The guard on the mistake that shipped the front end with no agents in it.

    `out_live/` is committed because a crewed run cannot be reproduced without a key. Re-running
    `crew.run --scenarios --out out_live` without one overwrites it with the rules path, where every
    case is degraded and no agent ran — and the demo silently becomes a list of decisions nobody made.
    S9 is the one case that degrades by design ("Sole provider case with the crew unavailable"): it
    exists to prove the fallback, so exactly one degraded case is right and ten is the accident.
    """
    import csv

    log = ROOT / "out_live" / "decision_log.csv"
    if not log.exists():
        pytest.skip("no committed live run in this checkout")
    rows = list(csv.DictReader(log.open(newline="", encoding="utf-8")))

    models = {r["model"] for r in rows if r["model"]}
    assert models, "out_live records no model: this is a rules run, not the crew's"
    degraded = [r["claim_id"] for r in rows if r["degraded"] == "True"]
    assert degraded == ["CLM-S09"], f"expected only S9 to degrade, got {degraded}"
    assert any(r["agents"] for r in rows), "no case lists any agent"
    assert any(r["tools_called"] for r in rows), "no case records a single tool call"


def _exported(name):
    import json

    path = ROOT / "frontend" / "static" / "data" / name
    if not path.exists():
        pytest.skip("front end not exported in this checkout")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_exported_evaluation_is_the_committed_one():
    """Re-running metrics.run without re-exporting would leave the front end quoting last month's numbers."""
    import json

    published = json.loads((ROOT / "results" / "metrics.json").read_text(encoding="utf-8"))
    exported = _exported("evaluation.json")
    assert exported["summary"] == published["summary"]
    assert exported["gate_at_chosen_km"] == published["gate_at_chosen_km"]
    assert exported["distance_material_km"] == published["distance_material_km"]
    assert exported["config"] == published["config"]


def test_the_exported_benchmark_is_the_committed_one():
    import json

    published = json.loads((ROOT / "results" / "agent_benchmark.json").read_text(encoding="utf-8"))
    exported = _exported("benchmark.json")
    assert set(exported["series"]) == set(published["series"])
    for key, series in published["series"].items():
        assert exported["series"][key]["overall"] == series["overall"], key


def test_the_exported_constants_are_the_policy_constants():
    """The front end prints the floor beside every confidence; it must be the floor the policy used."""
    assert _exported("evaluation.json")["confidence_floor"] == policy.CONFIDENCE_FLOOR
    assert _exported("meta.json")["confidence_floor"] == policy.CONFIDENCE_FLOOR
    assert _exported("meta.json")["distance_material_km"] == policy.DISTANCE_MATERIAL_KM


# ── the nearest alternative, recovered from prose ────────────────────────────
# `crew/actions.py` flattens the adequacy record into a sentence for the artefact, and the district
# name it names is not in the decision log. The exporter reads it back out so the viewer's map can
# draw the journey. That makes the exporter depend on a sentence's shape, which is exactly the kind
# of coupling that rots quietly, so both halves are pinned here.


def test_the_nearest_alternative_prose_is_still_the_shape_the_exporter_parses(export):
    """Built with the real renderer, not a hand-typed copy of what it used to emit."""
    from crew.actions import alternative
    from crew.schemas import Adequacy

    travelled = Adequacy(district_code=1, district="Bahraich", state="Uttar Pradesh",
                         specialty="cardiology", specialty_name="Cardiology", hospital_listed=True,
                         n_providers=1, km_to_alternative=83.3, nearest_alternative="Gonda",
                         population=4_156_731, aspirational=True, capability_ok=True,
                         state_convention=False, capability_reason="")
    phrase = alternative(travelled)
    assert phrase == "Gonda, 83 km away"
    assert export.nearest_district(phrase) == "Gonda"


def test_an_alternative_inside_the_district_names_no_district(export):
    """No journey to draw, which is a None rather than a value the map should guess at."""
    from crew.actions import alternative
    from crew.schemas import Adequacy

    local = Adequacy(district_code=2, district="Ahmedabad", state="Gujarat", specialty="cardiology",
                     specialty_name="Cardiology", hospital_listed=True, n_providers=101,
                     km_to_alternative=None, nearest_alternative=None, population=9_406_861,
                     aspirational=False, capability_ok=True, state_convention=False,
                     capability_reason="")
    assert alternative(local) == "in the district (100 other providers)"
    assert export.nearest_district(alternative(local)) is None


def test_nearest_district_survives_the_other_phrases_actions_can_write(export):
    for phrase in ("none listed",
                   "none in the district (distances are computed for sole-provider districts only)",
                   "", None):
        assert export.nearest_district(phrase) is None


def test_every_exported_case_agrees_with_its_own_prose(export):
    """Whatever the run produced, the extracted district and the sentence cannot disagree."""
    import re

    pattern = re.compile(r"^(?P<district>[^|]+?),\s*[\d.]+\s*km away$")
    for case in _exported("cases.json"):
        access = case.get("access") or {}
        prose = access.get("nearest_alternative")
        match = pattern.match(prose.strip()) if prose else None
        expected = match.group("district").strip() if match else None
        assert access.get("nearest_alternative_district") == expected, case["claim_id"]
