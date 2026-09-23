/**
 * The export and these types must agree. A mismatch between `scripts/export_frontend.py` and
 * `src/lib/types.ts` is the most likely way this build breaks, and it breaks quietly — a renamed
 * field reads as `undefined` and renders as an empty state that looks deliberate. These tests read
 * the JSON that is actually committed under `static/data/` and check the invariants the UI relies
 * on, so drift fails here rather than on screen.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ACTION_GROUPS, CONFIDENCE_FLOOR, actionGroup } from '$lib/domain';
import { DISTANCE_MATERIAL_KM } from '$lib/types';
import type { ActionKey, Benchmark, Case, Evaluation, Meta } from '$lib/types';

const read = <T>(name: string): T =>
	JSON.parse(readFileSync(join(process.cwd(), 'static/data', name), 'utf8')) as T;

const cases = read<Case[]>('cases.json');
const meta = read<Meta>('meta.json');
const evaluation = read<Evaluation>('evaluation.json');
const benchmark = read<Benchmark>('benchmark.json');

const AGENT_KEYS = [
	'case_router',
	'desk_investigator',
	'billing_analyst',
	'provider_advocate',
	'medical_auditor',
	'field_evidence_analyst',
	'audit_reviewer',
	'committee_liaison',
	'enforcement_officer'
];

describe('cases.json', () => {
	it('holds at least one case', () => {
		expect(cases.length).toBeGreaterThan(0);
	});

	it('matches the case count in meta.json', () => {
		expect(meta.counts.cases).toBe(cases.length);
		expect(meta.counts.degraded).toBe(cases.filter((c) => c.degraded).length);
		expect(meta.counts.crewed + meta.counts.degraded).toBe(cases.length);
	});

	it('uses only the eight fixed action strings', () => {
		const known = new Set(ACTION_GROUPS.flatMap((g) => g.actions));
		for (const c of cases) expect(known.has(c.action as ActionKey), c.action).toBe(true);
	});

	it('agrees with the front end about which group each action is in', () => {
		for (const c of cases) expect(c.action_group, c.claim_id).toBe(actionGroup(c.action));
	});

	it('uses only the four fixed gate strings, or null', () => {
		for (const c of cases) {
			expect([null, 'clear', 'protect', 'phantom', 'unknown'], c.claim_id).toContain(c.gate);
		}
	});

	it('records below_floor consistently with the fixed floor', () => {
		// The export computes it once; the UI must never re-derive it, so the two must agree here.
		for (const c of cases) {
			expect(c.below_floor, c.claim_id).toBe(c.confidence < CONFIDENCE_FLOOR);
		}
	});

	it('gives every case all nine agents on its roster', () => {
		for (const c of cases) {
			expect(
				c.roster.map((r) => r.key),
				c.claim_id
			).toEqual(AGENT_KEYS);
		}
	});

	it('never marks a roster entry both mandatory and router-opened', () => {
		for (const c of cases) {
			for (const entry of c.roster) {
				expect(entry.mandatory && entry.opened_by_router, `${c.claim_id}/${entry.key}`).toBe(false);
				if (!entry.ran) {
					expect(entry.order, `${c.claim_id}/${entry.key}`).toBeNull();
					expect(entry.mandatory).toBe(false);
				}
			}
		}
	});

	it('keeps the roster in step with the agents column', () => {
		for (const c of cases) {
			const ran = c.roster.filter((r) => r.ran).map((r) => r.key);
			expect(new Set(ran), c.claim_id).toEqual(new Set(c.agents));
		}
	});

	it('leaves a degraded case with no crew data at all', () => {
		for (const c of cases.filter((x) => x.degraded)) {
			expect(c.agents, c.claim_id).toHaveLength(0);
			expect(c.trail).toHaveLength(0);
			expect(c.disputed).toHaveLength(0);
			expect(c.disputes_refused).toHaveLength(0);
			expect(c.defence).toBeNull();
			expect(c.review).toBeNull();
		}
	});

	it('leaves a refused claim with no trigger, no gate and no findings', () => {
		for (const c of cases.filter((x) => x.action === 'refuse_malformed')) {
			expect(c.trigger_id, c.claim_id).toBeNull();
			expect(c.gate).toBeNull();
			expect(c.findings).toHaveLength(0);
			expect(c.reason_codes.length).toBeGreaterThan(0);
		}
	});

	it('issues no artefact for a released or refused claim, and one for every other action', () => {
		for (const c of cases) {
			const issues = c.action !== 'release_claim' && c.action !== 'refuse_malformed';
			expect(Boolean(c.artefact), c.claim_id).toBe(issues);
			expect(Boolean(c.artefact_path), c.claim_id).toBe(issues);
		}
	});

	it('never produces a `[""]` from an empty pipe-separated column', () => {
		for (const c of cases) {
			for (const list of [
				c.triggers_fired,
				c.channels_used,
				c.field_channels_ordered,
				c.reason_codes,
				c.specialties_at_stake,
				c.agents,
				c.disputed,
				c.disputes_refused
			]) {
				expect(
					list.every((item) => item.trim().length > 0),
					c.claim_id
				).toBe(true);
			}
		}
	});

	it('marks a finding disputed only when the case lists that dispute', () => {
		for (const c of cases) {
			for (const f of c.findings) {
				if (f.disputed) expect(c.disputed).toContain(f.reading);
				if (f.dispute_refused) expect(c.disputes_refused).toContain(f.reading);
				expect(f.disputed && f.dispute_refused, `${c.claim_id}/${f.reading}`).toBe(false);
			}
		}
	});

	it('gives a case with a gate the access figures the panel needs', () => {
		for (const c of cases.filter((x) => x.gate !== null)) {
			expect(c.access, c.claim_id).not.toBeNull();
			expect(c.access?.district).toBeTruthy();
			expect(typeof c.access?.n_providers).toBe('number');
		}
	});

	it('keeps km_to_alternative nullable rather than defaulting it to a number', () => {
		for (const c of cases) {
			const km = c.access?.km_to_alternative;
			expect(km === null || km === undefined || typeof km === 'number', c.claim_id).toBe(true);
		}
	});
});

describe('evaluation.json', () => {
	it('carries the summary the headline reads', () => {
		for (const key of [
			'flagged_cases',
			'auto_resolution_share',
			'resolved_at_desk_share',
			'field_audit_share',
			'human_share'
		] as const) {
			expect(typeof evaluation.summary[key], key).toBe('number');
		}
	});

	it('carries every sweep the page charts', () => {
		expect(evaluation.threshold_sweep.length).toBeGreaterThan(0);
		expect(evaluation.sensitivity.length).toBeGreaterThan(0);
		expect(evaluation.ablation.length).toBeGreaterThan(0);
		expect(evaluation.by_trigger.length).toBeGreaterThan(0);
	});

	it('names the operating point the sweep marks', () => {
		expect(typeof evaluation.distance_material_km).toBe('number');
		const kms = evaluation.threshold_sweep.map((row) => row['km']);
		expect(kms).toContain(evaluation.distance_material_km);
	});

	// The threshold slider starts at the generated constant rather than at the run's own figure, so
	// the two must be the same number or it would open somewhere the run never operated.
	it('agrees with the generated constant about the operating point', () => {
		expect(evaluation.distance_material_km).toBe(DISTANCE_MATERIAL_KM);
	});

	it('sweeps a contiguous, evenly spaced range the slider can step through', () => {
		const kms = evaluation.threshold_sweep.map((row) => Number(row['km']));
		const step = (kms[1] ?? 0) - (kms[0] ?? 0);
		expect(step).toBeGreaterThan(0);
		for (let i = 1; i < kms.length; i++) {
			expect((kms[i] ?? 0) - (kms[i - 1] ?? 0)).toBe(step);
		}
	});

	it('carries a caveat for every chart, and the limitations', () => {
		for (const key of ['corpus', 'threshold', 'sensitivity', 'ablation'] as const) {
			expect(evaluation.caveats[key]?.length ?? 0, key).toBeGreaterThan(20);
		}
		expect(evaluation.limitations.length).toBeGreaterThan(0);
	});

	it('states the same confidence floor the front end uses', () => {
		expect(evaluation.confidence_floor).toBe(CONFIDENCE_FLOOR);
		expect(meta.confidence_floor).toBe(CONFIDENCE_FLOOR);
	});
});

describe('benchmark.json', () => {
	it('holds at least the rules series and one crew series', () => {
		const keys = Object.keys(benchmark.series);
		expect(keys).toContain('rules');
		expect(keys.length).toBeGreaterThan(1);
	});

	it('gives every series a per-case table matching its case count', () => {
		for (const [key, series] of Object.entries(benchmark.series)) {
			expect(series.cases.length, key).toBe(series.n_cases);
		}
	});

	it('scores each case as one of the four asymmetric outcomes', () => {
		for (const [key, series] of Object.entries(benchmark.series)) {
			for (const row of series.cases) {
				expect(
					[null, 'correct', 'costly', 'acceptable', 'wrong'],
					`${key}/${row.case_id}`
				).toContain(row.score);
				expect(['fraud', 'innocent'], `${key}/${row.case_id}`).toContain(row.truth);
			}
		}
	});

	it('never scores an innocent claim "acceptable" or a fraud "costly"', () => {
		// The asymmetry is the point: deferring an innocent claim is costly, deferring a fraud is not.
		for (const series of Object.values(benchmark.series)) {
			for (const row of series.cases) {
				if (row.truth === 'innocent') expect(row.score).not.toBe('acceptable');
				if (row.truth === 'fraud') expect(row.score).not.toBe('costly');
			}
		}
	});

	it('adds each overall split up to its own n', () => {
		for (const [key, series] of Object.entries(benchmark.series)) {
			const { innocent, fraud } = series.overall;
			expect(innocent.correct + innocent.costly + innocent.wrong, `${key}/innocent`).toBe(
				innocent.n
			);
			expect(fraud.correct + fraud.acceptable + fraud.wrong, `${key}/fraud`).toBe(fraud.n);
		}
	});

	it('has a split for every reading the page renders', () => {
		for (const series of Object.values(benchmark.series)) {
			for (const reading of benchmark.readings) {
				expect(series.by_reading[reading], reading).toBeDefined();
			}
		}
	});

	it('carries the disclosure about who wrote the cases', () => {
		expect(benchmark.caveat).toMatch(/wrote the cases/);
	});

	it('leaves a crew path’s degraded cases out of its totals', () => {
		// `metrics/benchmark.py` scores `not (crew and degraded)`. A degraded row on a crew path is
		// the rules' answer, so it counts towards neither column — and the page must not print a
		// score for it either, or the table contradicts the headline above it.
		for (const [key, series] of Object.entries(benchmark.series)) {
			if (!series.crew) continue;
			const scored = series.cases.filter((row) => !row.degraded);
			expect(series.scored, key).toBe(scored.length);
			expect(series.overall.n, key).toBe(scored.length);
			expect(series.overall.wrong, key).toBe(scored.filter((row) => row.score === 'wrong').length);
			expect(series.overall.correct, key).toBe(
				scored.filter((row) => row.score === 'correct').length
			);
		}
	});

	it('scores every case on the rules path, where degraded is the normal state', () => {
		// No model runs on the rules path, so every row is flagged degraded; excluding them would
		// score nothing at all.
		const rules = benchmark.series.rules;
		expect(rules, 'the committed benchmark has no rules series').toBeDefined();
		expect(rules!.cases.every((row) => row.degraded)).toBe(true);
		expect(rules!.scored).toBe(rules!.cases.length);
		expect(rules!.overall.n).toBe(rules!.cases.length);
	});
});
