"""The simulated year of flags (generate/corpus.py) and the measurements over it (metrics/run.py)."""
from collections import Counter

import pytest

from crew.run import process
from crew.schemas import Channel
from generate.corpus import CorpusConfig, World, build_corpus
from metrics import run as metrics
from rules import policy, triggers


@pytest.fixture(scope="module")
def world(tools):
    return World(tools)


def test_corpus_is_reproducible(tools, world):
    a, _, _ = build_corpus(tools, CorpusConfig(n_flagged=150), world)
    b, _, _ = build_corpus(tools, CorpusConfig(n_flagged=150), world)
    key = lambda cs: [(c.claim.claim_id, c.claim.hospital_ref, c.claim.package_code, c.trigger, c.fraud) for c in cs]
    assert key(a) == key(b)


def test_every_case_fires_exactly_its_intended_trigger(tools, world):
    """Otherwise the corpus measures something other than what it says."""
    cases, store, _ = build_corpus(tools, CorpusConfig(n_flagged=400, seed=7), world)
    wrong = [(c.trigger, process(c.claim, store, tools, write=False).triggers_fired) for c in cases]
    wrong = [w for w in wrong if w[1] != [w[0]]]
    assert not wrong, wrong[:5]


def test_fraud_share_follows_the_base_rate_arithmetic(tools, world):
    cfg = CorpusConfig(n_flagged=1000)
    cases, _, _ = build_corpus(tools, cfg, world)
    assert abs(sum(c.fraud for c in cases) / len(cases) - cfg.fraud_share_of_flags) < 0.005
    assert 0.13 < cfg.fraud_share_of_flags < 0.15          # ~86% of flags innocent (BRD)


def test_field_reports_use_common_random_numbers(tools, world):
    """A channel's report must not depend on which other channels were ordered, or the ablation
    compares variants on different luck instead of different designs."""
    cases, _, b = build_corpus(tools, CorpusConfig(n_flagged=60), world)
    for c in cases[:20]:
        alone = b.field_reports(c, [Channel.BENEFICIARY_VISIT])[0]
        paired = b.field_reports(c, [Channel.BENEFICIARY_CALL, Channel.BENEFICIARY_VISIT])[1]
        assert (alone.supports_fraud, alone.confidence) == (paired.supports_fraud, paired.confidence)


def test_claims_follow_official_state_volumes(tools, world):
    cases, _, _ = build_corpus(tools, CorpusConfig(n_flagged=1500, seed=11), world)
    top = [s for s, _ in Counter(c.state for c in cases).most_common(6)]
    busiest = sorted(world.state_weight, key=world.state_weight.get, reverse=True)[:8]
    assert len(set(top) & set(busiest)) >= 5


@pytest.fixture(scope="module")
def exposed(tools):
    return metrics._exposed(tools)


def test_structural_sweep_trades_escalations_for_access(exposed):
    sweep = metrics.structural_sweep(exposed)
    assert sweep.iloc[0].cells_losing_only_provider == 0            # 0 km protects every sole provider
    assert sweep.iloc[0].hospitals_suspended_removing_only_provider == 0
    assert sweep.hospitals_escalated.is_monotonic_decreasing
    assert sweep.cells_losing_only_provider.is_monotonic_increasing


def test_claim_mass_is_a_probability_distribution(tools, world, exposed):
    weights, by_state, by_type = metrics.claim_weights(tools, world, exposed)
    assert sum(by_state.values()) == pytest.approx(1.0, abs=1e-9)
    assert sum(by_type.values()) == pytest.approx(1.0, abs=1e-9)
    assert 0 < sum(w + w_phantom for w, w_phantom, *_ in weights) < 1


def test_the_exact_gate_is_the_policy_gate(tools, exposed):
    """The evaluation collapses the billed specialty into the hospital's outcome; policy.decide must agree for
    every billed specialty of a sample of exposed hospitals, at the chosen threshold."""
    from crew.schemas import AgentFinding, Severity, TriggerHit
    fraud = [AgentFinding(agent="desk_audit", supports_fraud=True, confidence=0.95, conclusion="c", citation="x")]
    hit = TriggerHit(trigger_id="T6", name="t", severity=Severity.EGREGIOUS, evidence="e", field_channels=[],
                     checklist=[], source="s")
    for ref in sorted(exposed)[::40]:
        _h, network = exposed[ref]
        hospital_gate, _, _ = policy.suspension_gate(None, network)
        for billed in network:
            v = policy.decide([], hit, fraud, billed, network=network)
            want = "phantom" if metrics._phantom(billed) else hospital_gate
            assert v.gate == want, (ref, billed.specialty)


def test_gate_mass_counts_other_specialties(tools, world, exposed):
    """F-22: most of the claim mass the hospital-level gate protects was clear on the billed specialty alone."""
    weights, _, _ = metrics.claim_weights(tools, world, exposed)
    g = metrics.weighted_gate(weights, policy.DISTANCE_MATERIAL_KM)
    assert g["protect"] > 0.08 and g["phantom"] > 0


def test_ablation_restores_the_current_behaviour(tools, world):
    before = (metrics.rules_path.desk_audit, dict(triggers.SPECS))
    with metrics.as_first_built(register_is_proof=True, one_field_channel=True):
        assert triggers.SPECS["T10"].field_channels == (Channel.BENEFICIARY_VISIT,)
    assert (metrics.rules_path.desk_audit, dict(triggers.SPECS)) == before
    assert len(triggers.SPECS["T10"].field_channels) == 2


def test_a_t5_claim_is_placed_only_where_a_far_provider_exists(tools, world, monkeypatch):
    """F-31: with no provider of the specialty far enough away, the surgeon's second district was drawn from an
    empty list and the whole corpus build crashed."""
    from generate.corpus import CorpusBuilder
    real, calls = CorpusBuilder._far_districts, []

    def none_at_first(self, spec, h):
        calls.append(spec)
        return [] if len(calls) == 1 else real(self, spec, h)
    monkeypatch.setattr(CorpusBuilder, "_far_districts", none_at_first)
    case = CorpusBuilder(tools, CorpusConfig(n_flagged=10), world).case(1, "T5", True)
    assert case.claim.surgeon_reg_no and len(calls) >= 3            # rejected, re-placed, then used
