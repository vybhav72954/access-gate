"""The agent benchmark's construction and scoring (generate/benchmark.py, metrics/benchmark.py)."""
import csv
from collections import Counter

import pytest

from crew.schemas import Action
from generate.benchmark import READINGS, build_benchmark
from metrics import benchmark
from rules import triggers


@pytest.fixture(scope="module")
def bench(tools):
    return build_benchmark(tools)


def test_every_case_fires_exactly_its_intended_trigger(tools, bench):
    cases, store = bench
    for c in cases:
        hospital, package = tools.registry_lookup(c.claim.hospital_ref), tools.hbp_lookup(c.claim.package_code)
        assert [h.trigger_id for h in triggers.evaluate(c.claim, hospital, package, store, tools)] == [c.trigger], \
            c.case_id


def test_the_benchmark_is_balanced_and_covers_every_reading(bench):
    cases, _ = bench
    assert len(cases) == 40 and sum(c.fraud for c in cases) == 20
    assert set(Counter(c.reading for c in cases)) == set(READINGS)
    assert len({c.case_id for c in cases}) == len(cases)
    # the rules must have cases they should win, or the comparison proves nothing
    assert sum(c.reading == "keyword" for c in cases) >= 8


def test_no_benchmark_case_reaches_the_access_gate(bench):
    cases, _ = bench
    assert {c.trigger for c in cases} <= {t for t, s in triggers.SPECS.items() if s.severity < triggers.Severity.EGREGIOUS}


@pytest.mark.parametrize("fraud, action, expected", [
    (False, Action.RELEASE_CLAIM, "correct"), (False, Action.FIELD_AUDIT, "costly"),
    (False, Action.NO_ACTION, "costly"), (False, Action.SHOW_CAUSE, "wrong"), (False, Action.SUSPEND, "wrong"),
    (True, Action.SHOW_CAUSE, "correct"), (True, Action.ESCALATE_SEC, "correct"), (True, Action.FIELD_AUDIT, "acceptable"),
    (True, Action.RELEASE_CLAIM, "wrong"),
])
def test_scoring_is_asymmetric_like_the_costs(fraud, action, expected):
    assert benchmark.score(fraud, action) == expected


def test_a_refusal_is_a_broken_case_not_a_score():
    with pytest.raises(ValueError):
        benchmark.score(True, Action.REFUSE)


def test_the_rules_path_reads_the_keyword_cases_right_and_the_report_builds(tools, bench, tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(benchmark, "OUT", tmp_path / "out")
    monkeypatch.setattr(benchmark, "DOC", tmp_path / "10.md")
    assert benchmark.run("rules", fresh=True) == 0
    rows = benchmark.read_rows(benchmark.results_path("rules"))
    assert len(rows) == 40 and all(r["score"] == "correct" for r in rows if r["reading"] == "keyword")
    data = benchmark.report()
    assert set(data["series"]) == {"rules"} and data["series"]["rules"]["overall"]["n"] == 40
    assert "## 3. Every case" in (tmp_path / "10.md").read_text(encoding="utf-8")


def test_the_report_keeps_the_first_run_beside_a_corrected_one(tools, tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(benchmark, "OUT", tmp_path / "out")
    monkeypatch.setattr(benchmark, "DOC", tmp_path / "10.md")
    assert benchmark.run("rules", fresh=True) == 0
    rules = benchmark.read_rows(benchmark.results_path("rules"))

    def write(name, rows, fields=benchmark.FIELDS):
        with (tmp_path / "results" / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    crew = [{**r, "degraded": "False", "acted_by": "enforcement_officer", "model": "m", "llm_calls": "4"} for r in rules]
    two_agents = [{k: v for k, v in r.items() if k not in benchmark.SIX_AGENT_FIELDS} for r in crew]
    write("agent_benchmark_gemini_first_run.csv", two_agents, [f for f in benchmark.FIELDS
                                                               if f not in benchmark.SIX_AGENT_FIELDS])
    write("agent_benchmark_gemini.csv", two_agents, [f for f in benchmark.FIELDS if f not in benchmark.SIX_AGENT_FIELDS])
    labels = [s["label"] for s in benchmark.report()["series"].values()]
    assert labels == ["Rules", "Crew (gemini), first run: two agents, before the changes in §6"]
    six = [{**r, "agents": "desk_investigator|medical_auditor|audit_reviewer|enforcement_officer",
            "disputed": "desk_audit" if r["trigger"] == "T4" else ""} for r in crew[:-1]]      # one case short
    write("agent_benchmark_gemini.csv", six)
    data = benchmark.report()
    assert [s["label"] for s in data["series"].values()] == [
        "Rules", "Crew (gemini), first run: two agents, before the changes in §6", "Crew (gemini), final run: nine agents"]
    doc = (tmp_path / "10.md").read_text(encoding="utf-8")
    assert "Rules, on the same 39 cases" in doc
    assert data["series"]["gemini"]["crew"]["disputes"] == {"desk_audit": 10}
    assert "disputed a reading on 10 (by reading: desk_audit 10)" in doc
    assert "The Medical Auditor worked" not in doc.split("How the crew worked: Crew (gemini), first run")[1].split(
        "###")[0]


# ── the resume paths (F-65) ─────────────────────────────────────────────────
# A live run is interrupted often enough that these matter: a spent quota stops it, and on 18 September the operating
# system killed one for memory at case 23. Each of these was previously untested.

def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(benchmark, "OUT", tmp_path / "out")
    monkeypatch.setattr(benchmark, "DOC", tmp_path / "10.md")
    return benchmark.results_path("rules")


def test_resuming_a_finished_run_adds_nothing(tools, tmp_path, monkeypatch):
    """The resume after an interruption is the same command without --fresh; running it again must be a no-op."""
    path = _isolate(tmp_path, monkeypatch)
    assert benchmark.run("rules", fresh=True) == 0
    before = benchmark.read_rows(path)
    assert benchmark.run("rules") == 0
    after = benchmark.read_rows(path)
    assert len(after) == len(before) == 40
    assert len({r["case_id"] for r in after}) == 40          # nothing duplicated
    assert [r["action"] for r in after] == [r["action"] for r in before]


def test_a_partial_run_resumes_at_the_case_it_stopped_on(tools, tmp_path, monkeypatch):
    """What a quota stop leaves behind: some cases scored, the rest not."""
    path = _isolate(tmp_path, monkeypatch)
    assert benchmark.run("rules", fresh=True) == 0
    rows = benchmark.read_rows(path)[:12]                    # pretend it stopped after twelve
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=benchmark.FIELDS)
        w.writeheader()
        w.writerows(rows)
    assert benchmark.run("rules") == 0
    after = benchmark.read_rows(path)
    assert len(after) == 40 and len({r["case_id"] for r in after}) == 40
    assert [r["case_id"] for r in after[:12]] == [r["case_id"] for r in rows]   # the kept rows stay put


def test_redo_runs_named_cases_again_without_losing_the_file(tools, tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    assert benchmark.run("rules", fresh=True) == 0
    before = {r["case_id"]: r["action"] for r in benchmark.read_rows(path)}
    assert benchmark.run("rules", redo={"BCH-T2-01", "BCH-R2-06"}) == 0
    after = benchmark.read_rows(path)
    assert len(after) == 40 and len({r["case_id"] for r in after}) == 40
    assert {r["case_id"]: r["action"] for r in after} == before     # the rules are deterministic


def test_redo_of_an_unknown_case_id_changes_nothing(tools, tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    assert benchmark.run("rules", fresh=True) == 0
    before = benchmark.read_rows(path)
    assert benchmark.run("rules", redo={"NOT-A-CASE"}) == 0
    assert benchmark.read_rows(path) == before


def test_retry_degraded_re_runs_only_the_degraded_cases(tools, tmp_path, monkeypatch, capsys):
    """The rules path marks every row degraded (there is no model), so the mixed file a crew run leaves behind has to
    be built by hand: one case the crew could not run, the rest it could."""
    path = _isolate(tmp_path, monkeypatch)
    assert benchmark.run("rules", fresh=True) == 0
    rows = benchmark.read_rows(path)
    for r in rows:
        r["degraded"] = "False"
    rows[3]["degraded"] = "True"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=benchmark.FIELDS)
        w.writeheader()
        w.writerows(rows)
    capsys.readouterr()
    assert benchmark.run("rules", retry_degraded=True) == 0
    assert "39 case(s) already scored, 1 to run" in capsys.readouterr().out
    after = benchmark.read_rows(path)
    assert len(after) == 40 and len({r["case_id"] for r in after}) == 40


def test_only_runs_the_named_case_and_no_other(tools, tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    assert benchmark.run("rules", only={"BCH-T2-01"}) == 0
    rows = benchmark.read_rows(path)
    assert len(rows) == 1 and rows[0]["case_id"] == "BCH-T2-01"
