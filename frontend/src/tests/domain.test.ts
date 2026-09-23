import { describe, expect, it } from 'vitest';
import {
	ACTION_GROUPS,
	CONFIDENCE_FLOOR,
	actionGroup,
	findingState,
	formatDistance,
	formatInt,
	gateNotApplicable,
	gateSpec,
	readConfidence,
	splitRoster,
	summarise,
	NO_FILTER,
	arrangeCases,
	compareCases,
	filterIsActive,
	matchesFilter,
	triggersPresent,
	districtsPresent,
	recordSections
} from '$lib/domain';
import type { ActionKey, Case, Finding, RosterEntry } from '$lib/types';

const finding = (over: Partial<Finding> = {}): Finding => ({
	reading: 'desk_audit',
	reading_title: 'Desk audit',
	agent_key: 'desk_investigator',
	agent_title: 'Desk Investigator',
	channel: null,
	stance: 'supports',
	confidence: 0.9,
	conclusion: 'x',
	citation: 'y',
	disputed: false,
	dispute_refused: false,
	...over
});

const agent = (key: RosterEntry['key'], over: Partial<RosterEntry> = {}): RosterEntry => ({
	key,
	title: key,
	owns: '',
	ran: false,
	order: null,
	mandatory: false,
	opened_by_router: false,
	router_reason: null,
	...over
});

const testCase = (over: Partial<Case> = {}): Case =>
	({
		case_id: 'CASE-X',
		claim_id: 'X',
		action: 'release_claim',
		action_group: 'released',
		gate: null,
		confidence: 0.9,
		below_floor: false,
		degraded: false,
		...over
	}) as Case;

describe('action grouping', () => {
	it('places all eight actions in exactly one group', () => {
		const actions = ACTION_GROUPS.flatMap((group) => group.actions);
		expect(actions).toHaveLength(8);
		expect(new Set(actions).size).toBe(8);
	});

	it('groups each action the way the brief does', () => {
		const expected: Record<ActionKey, string> = {
			release_claim: 'released',
			order_field_audit: 'deferred',
			no_action_review: 'deferred',
			show_cause_notice: 'enforced',
			suspend_hospital: 'enforced',
			escalate_to_sec: 'enforced',
			delist_specialty: 'enforced',
			refuse_malformed: 'refused'
		};
		for (const [action, group] of Object.entries(expected)) {
			expect(actionGroup(action as ActionKey)).toBe(group);
		}
	});

	it('keeps de-listing under enforced, not deferred', () => {
		// It is an action against the hospital, even though a Committee acts on it.
		expect(actionGroup('delist_specialty')).toBe('enforced');
	});

	it('gives every group a shape as well as a colour', () => {
		for (const group of ACTION_GROUPS) expect(group.mark.trim().length).toBeGreaterThan(0);
	});
});

describe('the access gate', () => {
	it('names all four states', () => {
		for (const key of ['clear', 'protect', 'phantom', 'unknown'] as const) {
			expect(gateSpec(key)?.key).toBe(key);
		}
	});

	it('treats a null gate as absent, never as clear', () => {
		expect(gateSpec(null)).toBeNull();
		expect(gateSpec(null)).not.toEqual(gateSpec('clear'));
	});

	it('explains a missing gate differently for a refused claim', () => {
		expect(gateNotApplicable('refuse_malformed')).toMatch(/malformed/);
		expect(gateNotApplicable('show_cause_notice')).toMatch(/removes no provider/);
	});

	it('marks protect and phantom as the emphatic states', () => {
		expect(gateSpec('protect')?.emphatic).toBe(true);
		expect(gateSpec('phantom')?.emphatic).toBe(true);
		expect(gateSpec('clear')?.emphatic).toBe(false);
		expect(gateSpec('unknown')?.emphatic).toBe(false);
	});

	it('distinguishes phantom from protect in words', () => {
		expect(gateSpec('phantom')?.summary).not.toBe(gateSpec('protect')?.summary);
		expect(gateSpec('phantom')?.mark).not.toBe(gateSpec('protect')?.mark);
	});
});

describe('confidence against the floor', () => {
	it('uses the fixed 0.70 floor', () => {
		expect(CONFIDENCE_FLOOR).toBe(0.7);
	});

	it('reports a value below the floor as below it', () => {
		const reading = readConfidence(0.46);
		expect(reading.belowFloor).toBe(true);
		expect(reading.text).toBe('0.46, below the 0.70 floor');
	});

	it('treats a value exactly at the floor as not below it', () => {
		const reading = readConfidence(0.7);
		expect(reading.belowFloor).toBe(false);
		expect(reading.text).toBe('0.70, at or above the 0.70 floor');
	});

	it('always names the floor, so a bare number never stands alone', () => {
		for (const value of [0, 0.31, 0.7, 0.95, 1]) {
			expect(readConfidence(value).text).toContain('0.70 floor');
		}
	});

	it('places the value and the floor on the same 0–1 track', () => {
		const reading = readConfidence(0.31);
		expect(reading.fraction).toBeCloseTo(0.31);
		expect(reading.floorFraction).toBeCloseTo(0.7);
	});

	it('clamps impossible values rather than drawing off the track', () => {
		expect(readConfidence(1.4).fraction).toBe(1);
		expect(readConfidence(-2).fraction).toBe(0);
		expect(readConfidence(Number.NaN).value).toBe(0);
	});
});

describe('finding states', () => {
	it('is "stands" when nothing was disputed', () => {
		expect(findingState(finding())).toBe('stands');
	});

	it('is "disputed" when the reviewer set it aside', () => {
		expect(findingState(finding({ disputed: true }))).toBe('disputed');
	});

	it('is "dispute-refused" when the reviewer was overruled', () => {
		expect(findingState(finding({ dispute_refused: true }))).toBe('dispute-refused');
	});

	it('never reads a refused dispute as a dispute', () => {
		// They are different arrays with opposite meanings.
		expect(findingState(finding({ dispute_refused: true }))).not.toBe('disputed');
	});
});

describe('the roster', () => {
	const roster: RosterEntry[] = [
		agent('case_router', { ran: true, order: 0, mandatory: true }),
		agent('desk_investigator', { ran: true, order: 1, mandatory: true }),
		agent('billing_analyst'),
		agent('provider_advocate', {
			ran: true,
			order: 3,
			opened_by_router: true,
			router_reason: 'the file bears an innocent account worth testing'
		}),
		agent('medical_auditor', {
			ran: true,
			order: 2,
			opened_by_router: true,
			router_reason: 'clinical'
		}),
		agent('field_evidence_analyst'),
		agent('audit_reviewer', { ran: true, order: 4, mandatory: true }),
		agent('committee_liaison'),
		agent('enforcement_officer', { ran: true, order: 5, mandatory: true })
	];

	it('splits mandatory from router-opened', () => {
		const split = splitRoster(roster);
		expect(split.mandatory.map((a) => a.key)).toEqual([
			'case_router',
			'desk_investigator',
			'audit_reviewer',
			'enforcement_officer'
		]);
		expect(split.openedByRouter.map((a) => a.key)).toEqual([
			'provider_advocate',
			'medical_auditor'
		]);
	});

	it('orders the agents that worked by the order they worked', () => {
		expect(splitRoster(roster).worked.map((a) => a.key)).toEqual([
			'case_router',
			'desk_investigator',
			'medical_auditor',
			'provider_advocate',
			'audit_reviewer',
			'enforcement_officer'
		]);
	});

	it('keeps agents that did not run, rather than hiding them', () => {
		const split = splitRoster(roster);
		expect(split.absent.map((a) => a.key)).toEqual([
			'billing_analyst',
			'field_evidence_analyst',
			'committee_liaison'
		]);
		expect(split.worked.length + split.absent.length).toBe(9);
	});

	it('never counts an agent as both mandatory and router-opened', () => {
		const split = splitRoster(roster);
		const overlap = split.mandatory.filter((a) => split.openedByRouter.includes(a));
		expect(overlap).toHaveLength(0);
	});

	it('handles a degraded case, where no agent ran', () => {
		const split = splitRoster(roster.map((a) => agent(a.key)));
		expect(split.worked).toHaveLength(0);
		expect(split.absent).toHaveLength(9);
	});
});

describe('formatting', () => {
	it('renders a null distance as "none listed", not as a gap', () => {
		expect(formatDistance(null)).toEqual({
			text: 'none listed',
			noneListed: true
		});
		expect(formatDistance(83.3)).toEqual({
			text: '83.3 km',
			noneListed: false
		});
		expect(formatDistance(0)).toEqual({ text: '0.0 km', noneListed: false });
	});

	it('renders a missing number as an em dash rather than zero', () => {
		expect(formatInt(null)).toBe('—');
		expect(formatInt(undefined)).toBe('—');
		expect(formatInt(0)).toBe('0');
		expect(formatInt(4156731)).toBe('4,156,731');
	});
});

describe('the list summary', () => {
	it('counts each group and the cases the gate diverted', () => {
		const summary = summarise([
			testCase({ action: 'release_claim', action_group: 'released' }),
			testCase({
				action: 'escalate_to_sec',
				action_group: 'enforced',
				gate: 'protect'
			}),
			testCase({
				action: 'delist_specialty',
				action_group: 'enforced',
				gate: 'phantom'
			}),
			testCase({
				action: 'suspend_hospital',
				action_group: 'enforced',
				gate: 'clear'
			}),
			testCase({
				action: 'no_action_review',
				action_group: 'deferred',
				confidence: 0.31,
				below_floor: true
			}),
			testCase({
				action: 'refuse_malformed',
				action_group: 'refused',
				degraded: true
			})
		]);
		expect(summary.total).toBe(6);
		expect(Object.fromEntries(summary.byGroup.map((g) => [g.spec.key, g.count]))).toEqual({
			released: 1,
			deferred: 1,
			enforced: 3,
			refused: 1
		});
		// A clear gate is not a diversion; protect and phantom are.
		expect(summary.divertedByGate).toBe(2);
		expect(summary.belowFloor).toBe(1);
		expect(summary.degraded).toBe(1);
	});
});

// ── the case list ────────────────────────────────────────────────────────────
// The landing screen. A wrong predicate here does not throw, it just quietly drops a case from the
// demo, so each branch is pinned.

describe('the case list filter', () => {
	const rows = [
		testCase({ claim_id: 'A', action_group: 'enforced', gate: 'protect', trigger_id: 'T10' }),
		testCase({ claim_id: 'B', action_group: 'released', gate: null, trigger_id: 'T2' }),
		testCase({
			claim_id: 'C',
			action_group: 'enforced',
			gate: 'clear',
			trigger_id: 'T10',
			degraded: true
		}),
		testCase({ claim_id: 'D', action_group: 'refused', gate: null, trigger_id: null })
	];

	const ids = (f: Parameters<typeof matchesFilter>[1]) =>
		rows.filter((c) => matchesFilter(c, f)).map((c) => c.claim_id);

	it('passes everything through when nothing is set', () => {
		expect(ids(NO_FILTER)).toEqual(['A', 'B', 'C', 'D']);
		expect(filterIsActive(NO_FILTER)).toBe(false);
	});

	it('filters by action group', () => {
		expect(ids({ ...NO_FILTER, group: 'enforced' })).toEqual(['A', 'C']);
		expect(filterIsActive({ ...NO_FILTER, group: 'enforced' })).toBe(true);
	});

	it('filters by gate state', () => {
		expect(ids({ ...NO_FILTER, gate: 'protect' })).toEqual(['A']);
	});

	it("treats 'none' as the cases the gate never reached, not as a missing value", () => {
		expect(ids({ ...NO_FILTER, gate: 'none' })).toEqual(['B', 'D']);
	});

	it('filters by trigger, and a case with no trigger is not swept in', () => {
		expect(ids({ ...NO_FILTER, trigger: 'T10' })).toEqual(['A', 'C']);
	});

	it('filters to the degraded cases only', () => {
		expect(ids({ ...NO_FILTER, degradedOnly: true })).toEqual(['C']);
	});

	it('combines filters as AND, not OR', () => {
		expect(ids({ ...NO_FILTER, group: 'enforced', gate: 'clear' })).toEqual(['C']);
		expect(ids({ ...NO_FILTER, group: 'released', gate: 'protect' })).toEqual([]);
	});
});

describe('the case list sort', () => {
	const rows = [
		testCase({ claim_id: 'CLM-S02', confidence: 0.71, decided_ts: '2026-06-14T19:00:00' }),
		testCase({ claim_id: 'CLM-S01', confidence: 0.95, decided_ts: '2026-06-11T19:00:00' }),
		testCase({ claim_id: 'CLM-S03', confidence: 0.0, decided_ts: '2026-06-13T09:00:00' })
	];
	const order = (sort: Parameters<typeof compareCases>[2], descending: boolean) =>
		arrangeCases(rows, NO_FILTER, sort, descending).map((c) => c.claim_id);

	it('sorts by claim id by default', () => {
		expect(order('claim', false)).toEqual(['CLM-S01', 'CLM-S02', 'CLM-S03']);
	});

	it('sorts by confidence, lowest first, and reverses', () => {
		expect(order('confidence', false)).toEqual(['CLM-S03', 'CLM-S02', 'CLM-S01']);
		expect(order('confidence', true)).toEqual(['CLM-S01', 'CLM-S02', 'CLM-S03']);
	});

	it('sorts by decision time', () => {
		expect(order('decided', false)).toEqual(['CLM-S01', 'CLM-S03', 'CLM-S02']);
	});

	it('never mutates the cases it was given', () => {
		const before = rows.map((c) => c.claim_id);
		arrangeCases(rows, NO_FILTER, 'confidence', true);
		expect(rows.map((c) => c.claim_id)).toEqual(before);
	});

	it('a zero confidence still sorts as a number, not as missing', () => {
		expect(order('confidence', false)[0]).toBe('CLM-S03');
	});
});

describe('triggersPresent', () => {
	it('lists each trigger once, sorted, and leaves out the cases with none', () => {
		expect(
			triggersPresent([
				testCase({ trigger_id: 'T2' }),
				testCase({ trigger_id: 'T10' }),
				testCase({ trigger_id: 'T2' }),
				testCase({ trigger_id: null })
			])
		).toEqual(['T10', 'T2']);
	});
});

// ── the districts the map marks ──────────────────────────────────────────────
// The map draws one shape per district but a district holds several cases, so these decide what a
// single marker is allowed to say about them. Getting it wrong would have the map contradict the
// table beside it.

const placed = (district: string | null, state: string | null, over: Partial<Case> = {}): Case =>
	testCase({
		access:
			district === null
				? null
				: ({
						district,
						state,
						specialty: 'cardiology',
						specialty_name: 'Cardiology',
						hospital_listed: true,
						n_providers: 1,
						km_to_alternative: 83.3,
						nearest_alternative: 'Gonda, 83 km away',
						nearest_alternative_district: 'Gonda',
						population: 1,
						aspirational: true,
						capability_ok: true,
						capability_reason: ''
					} as Case['access']),
		...over
	});

describe('districtsPresent', () => {
	it('groups the cases by district and counts them', () => {
		const found = districtsPresent([
			placed('Ahmedabad', 'Gujarat'),
			placed('Ahmedabad', 'Gujarat'),
			placed('Bahraich', 'Uttar Pradesh')
		]);
		expect(found.map((d) => [d.district, d.cases])).toEqual([
			['Ahmedabad', 2],
			['Bahraich', 1]
		]);
	});

	it('leaves out a case with no district rather than inventing one', () => {
		// A claim refused as malformed never reaches the gate and carries no access record.
		expect(
			districtsPresent([placed(null, null), placed('Bahraich', 'Uttar Pradesh')])
		).toHaveLength(1);
	});

	it('shows the most consequential gate a district actually reached', () => {
		const found = districtsPresent([
			placed('Bahraich', 'Uttar Pradesh', { gate: 'clear' }),
			placed('Bahraich', 'Uttar Pradesh', { gate: 'protect' })
		]);
		expect(found[0]?.gate).toBe('protect');
	});

	it('never reports a gate no case in the district reached', () => {
		const found = districtsPresent([
			placed('Ahmedabad', 'Gujarat', { gate: null }),
			placed('Ahmedabad', 'Gujarat', { gate: 'clear' })
		]);
		expect(found[0]?.gate).toBe('clear');
		expect(districtsPresent([placed('X', 'Y', { gate: null })])[0]?.gate).toBeNull();
	});

	it('counts only the diversions, not every case in the district', () => {
		const found = districtsPresent([
			placed('Barwani', 'Madhya Pradesh', { gate: 'phantom' }),
			placed('Barwani', 'Madhya Pradesh', { gate: 'clear' }),
			placed('Barwani', 'Madhya Pradesh', { gate: null })
		]);
		expect(found[0]?.diverted).toBe(1);
		expect(found[0]?.cases).toBe(3);
	});

	it('treats two districts of the same name in different states as two places', () => {
		const found = districtsPresent([
			placed('Bilaspur', 'Chhattisgarh'),
			placed('Bilaspur', 'Himachal Pradesh')
		]);
		expect(found).toHaveLength(2);
	});
});

describe('the district filter', () => {
	const rows = [
		placed('Ahmedabad', 'Gujarat', { claim_id: 'A' }),
		placed('Bahraich', 'Uttar Pradesh', { claim_id: 'B' }),
		placed(null, null, { claim_id: 'C' })
	];

	it('keeps only the cases in the chosen district', () => {
		const kept = arrangeCases(rows, { ...NO_FILTER, district: 'Bahraich' }, 'claim', false);
		expect(kept.map((c) => c.claim_id)).toEqual(['B']);
	});

	it('does not match a case that has no district at all', () => {
		expect(matchesFilter(rows[2] as Case, { ...NO_FILTER, district: 'Ahmedabad' })).toBe(false);
	});

	it('counts as an active filter, so the clear-filters control appears', () => {
		expect(filterIsActive({ ...NO_FILTER, district: 'Ahmedabad' })).toBe(true);
		expect(filterIsActive(NO_FILTER)).toBe(false);
	});

	it('combines with the other filters rather than replacing them', () => {
		const kept = arrangeCases(
			[
				placed('Ahmedabad', 'Gujarat', { claim_id: 'A', action_group: 'enforced' }),
				placed('Ahmedabad', 'Gujarat', { claim_id: 'B', action_group: 'released' })
			],
			{ ...NO_FILTER, district: 'Ahmedabad', group: 'enforced' },
			'claim',
			false
		);
		expect(kept.map((c) => c.claim_id)).toEqual(['A']);
	});
});

// ── the index beside a case record ───────────────────────────────────────────
// The index is a list of links into the page. A link to a heading that is not rendered is a dead
// anchor, so what decides the list has to be the same thing that decides the page.

const withOrders = (over: Partial<Case> = {}): Case =>
	testCase({
		field_channels_ordered: [],
		field_questions: [],
		documents_requested: [],
		...over
	});

describe('recordSections', () => {
	it('lists every unconditional section, in the order the page renders them', () => {
		const ids = recordSections(withOrders()).map((s) => s.id);
		expect(ids).toEqual([
			'trigger-h',
			'roster-h',
			'findings-h',
			'defence-h',
			'review-h',
			'gate-heading',
			'trail-h',
			'artefact-h'
		]);
	});

	it('leaves out "what was ordered" when the decision ordered nothing', () => {
		expect(recordSections(withOrders()).map((s) => s.id)).not.toContain('field-h');
	});

	it.each([
		['a field channel', { field_channels_ordered: ['hospital_visit'] }],
		['a question', { field_questions: ['Was the beneficiary present?'] }],
		['a document', { documents_requested: ['discharge_summary'] }]
	])('includes it when the decision ordered %s', (_what, over) => {
		const sections = recordSections(withOrders(over as Partial<Case>));
		expect(sections.map((s) => s.id)).toContain('field-h');
		// Between the gate and the trail, which is where the page puts it.
		expect(sections.findIndex((s) => s.id === 'field-h')).toBe(
			sections.findIndex((s) => s.id === 'gate-heading') + 1
		);
	});

	it('marks the gate, and only the gate, as the emphatic one', () => {
		const emphatic = recordSections(withOrders()).filter((s) => s.emphatic);
		expect(emphatic.map((s) => s.id)).toEqual(['gate-heading']);
	});

	it('carries the gate’s own mark rather than a second glyph for the same thing', () => {
		const gate = recordSections(withOrders()).find((s) => s.id === 'gate-heading');
		expect(gate?.mark).toBe(gateSpec('protect')?.mark);
	});

	it('gives every section a mark and a label, since the index shows both', () => {
		for (const section of recordSections(withOrders({ field_questions: ['q'] }))) {
			expect(section.mark.trim().length).toBeGreaterThan(0);
			expect(section.label.trim().length).toBeGreaterThan(0);
		}
	});
});
