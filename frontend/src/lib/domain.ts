/**
 * Domain vocabulary and presentation rules.
 *
 * Nothing here decides anything. The JSON is the record: the action, the gate, the confidence and
 * the aggregation were settled by `rules/policy.py` before the export ran. This module maps those
 * values to labels, shapes and groups, and formats numbers. It never recomputes confidence, never
 * re-applies the floor, and never infers an action from the findings — any disagreement between the
 * JSON and the screen is a bug in the export, not something to patch here.
 */
import type {
	ActionGroup,
	ActionKey,
	Case,
	Finding,
	GateState,
	RosterEntry,
	Stance
} from './types';
import { CONFIDENCE_FLOOR } from './types';

export { CONFIDENCE_FLOOR };

/* ── actions ──────────────────────────────────────────────────────────── */

export interface ActionGroupSpec {
	key: ActionGroup;
	label: string;
	/** What the group means, in the words the brief uses. */
	note: string;
	/** A shape, so the group survives greyscale and colour-blindness. */
	mark: string;
	actions: readonly ActionKey[];
}

export const ACTION_GROUPS: readonly ActionGroupSpec[] = [
	{
		key: 'released',
		label: 'Released',
		note: 'the evidence explains what the trigger flagged',
		mark: '○',
		actions: ['release_claim']
	},
	{
		key: 'deferred',
		label: 'Deferred',
		note: 'buys evidence or a human',
		mark: '◐',
		actions: ['order_field_audit', 'no_action_review']
	},
	{
		key: 'enforced',
		label: 'Enforced',
		note: 'an action against the hospital',
		mark: '●',
		actions: ['show_cause_notice', 'suspend_hospital', 'escalate_to_sec', 'delist_specialty']
	},
	{
		key: 'refused',
		label: 'Refused',
		note: 'a malformed claim the system declines to judge',
		mark: '×',
		actions: ['refuse_malformed']
	}
];

const GROUP_OF = new Map<ActionKey, ActionGroup>(
	ACTION_GROUPS.flatMap((group) => group.actions.map((action) => [action, group.key] as const))
);

export function actionGroup(action: ActionKey): ActionGroup {
	const group = GROUP_OF.get(action);
	if (group === undefined) throw new Error(`unknown action: ${action}`);
	return group;
}

export function groupSpec(key: ActionGroup): ActionGroupSpec {
	const spec = ACTION_GROUPS.find((g) => g.key === key);
	if (spec === undefined) throw new Error(`unknown action group: ${key}`);
	return spec;
}

export const ACTION_LABELS: Record<ActionKey, string> = {
	release_claim: 'Claim released',
	order_field_audit: 'Field audit ordered',
	no_action_review: 'Referred for human review',
	show_cause_notice: 'Show-cause notice',
	suspend_hospital: 'Hospital suspended',
	escalate_to_sec: 'Escalated to the Committee',
	delist_specialty: 'Specialty de-listing referral',
	refuse_malformed: 'Refused: malformed claim'
};

/* ── the access gate ──────────────────────────────────────────────────── */

export interface GateSpec {
	key: GateState;
	label: string;
	mark: string;
	/** One line, in the terms the policy uses. */
	summary: string;
	/** `protect` and `phantom` are the states that dominate a case screen. */
	emphatic: boolean;
}

export const GATES: Record<GateState, GateSpec> = {
	clear: {
		key: 'clear',
		label: 'Clear',
		mark: '□',
		summary: 'No specialty loses its only provider. The suspension proceeds.',
		emphatic: false
	},
	protect: {
		key: 'protect',
		label: 'Protected',
		mark: '▣',
		summary:
			'Suspending would strip the district of its only real provider of a specialty, so the case was escalated to the State Empanelment Committee instead.',
		emphatic: true
	},
	phantom: {
		key: 'phantom',
		label: 'Phantom provider',
		mark: '▨',
		summary:
			'The listed provider is not a real one. Referred for specialty de-listing, and the district flagged as uncovered.',
		emphatic: true
	},
	unknown: {
		key: 'unknown',
		label: 'Unknown',
		mark: '▦',
		summary: 'The access consequence could not be computed, so a human decides.',
		emphatic: false
	}
};

/**
 * A null gate is NOT `clear`. The gate is only computed for actions that would remove a provider,
 * so most cases have none, and rendering that absence as "clear" would claim the policy checked
 * something it never checked.
 */
export function gateSpec(gate: GateState | null): GateSpec | null {
	return gate === null ? null : (GATES[gate] ?? null);
}

export function gateNotApplicable(action: ActionKey): string {
	return action === 'refuse_malformed'
		? 'Not computed: the claim was refused as malformed, so no action against the hospital was considered.'
		: 'Not computed: this action removes no provider, so the gate was never reached.';
}

/* ── findings ─────────────────────────────────────────────────────────── */

export const STANCE_LABELS: Record<Stance, string> = {
	supports: 'Supports the trigger',
	opposes: 'Opposes the trigger',
	inconclusive: 'Inconclusive'
};

export const STANCE_MARKS: Record<Stance, string> = {
	supports: '↑',
	opposes: '↓',
	inconclusive: '–'
};

export type FindingState = 'stands' | 'disputed' | 'dispute-refused';

/**
 * `disputed` and `disputes_refused` are different arrays with opposite meanings: one set a reading
 * aside, the other tried to and was overruled because the reading repeats a measurement the claim
 * store makes itself. A finding can only be in one state, and a refused dispute is not a dispute.
 */
export function findingState(finding: Finding): FindingState {
	if (finding.disputed) return 'disputed';
	if (finding.dispute_refused) return 'dispute-refused';
	return 'stands';
}

/* ── the roster ───────────────────────────────────────────────────────── */

export interface RosterSplit {
	/** In the order the agents worked the case. */
	worked: RosterEntry[];
	mandatory: RosterEntry[];
	openedByRouter: RosterEntry[];
	absent: RosterEntry[];
}

/**
 * The Router may only widen: rules compute a mandatory set from the trigger, and the Router may add
 * to it and never take from it. Agents that did not run stay in `absent` rather than disappearing —
 * that a Medical Auditor was not needed on a case is information.
 */
export function splitRoster(roster: readonly RosterEntry[]): RosterSplit {
	const ran = roster.filter((entry) => entry.ran);
	return {
		worked: [...ran].sort((a, b) => (a.order ?? 0) - (b.order ?? 0)),
		mandatory: ran.filter((entry) => entry.mandatory),
		openedByRouter: ran.filter((entry) => entry.opened_by_router),
		absent: roster.filter((entry) => !entry.ran)
	};
}

/* ── confidence ───────────────────────────────────────────────────────── */

export interface ConfidenceReading {
	value: number;
	floor: number;
	belowFloor: boolean;
	/** 0–1 position of the value on the bar. */
	fraction: number;
	/** 0–1 position of the floor marker on the same bar. */
	floorFraction: number;
	text: string;
}

/**
 * Reads the recorded confidence against the fixed 0.70 floor. It does not decide anything: the
 * policy already applied the floor, and `below` is stated so a bare "0.46" can never appear without
 * the fact that makes it mean something.
 */
export function readConfidence(value: number, floor: number = CONFIDENCE_FLOOR): ConfidenceReading {
	const clamped = Number.isFinite(value) ? Math.min(Math.max(value, 0), 1) : 0;
	const belowFloor = clamped < floor;
	return {
		value: clamped,
		floor,
		belowFloor,
		fraction: clamped,
		floorFraction: floor,
		text: `${formatConfidence(clamped)}, ${belowFloor ? 'below' : 'at or above'} the ${formatConfidence(floor)} floor`
	};
}

export function formatConfidence(value: number): string {
	return value.toFixed(2);
}

/* ── formatting ───────────────────────────────────────────────────────── */

export function formatInt(value: number | null | undefined): string {
	return value === null || value === undefined || !Number.isFinite(value)
		? '—'
		: Math.round(value).toLocaleString('en-US');
}

export function formatPercent(value: number | null | undefined, decimals = 1): string {
	return value === null || value === undefined || !Number.isFinite(value)
		? '—'
		: `${(value * 100).toFixed(decimals)}%`;
}

export function formatNumber(value: number | null | undefined, decimals = 2): string {
	return value === null || value === undefined || !Number.isFinite(value)
		? '—'
		: value.toFixed(decimals);
}

/**
 * `km_to_alternative` can legitimately be null, and that means "no alternative listed" — a stronger
 * access signal than a large number, not a missing value to hide.
 */
export function formatDistance(km: number | null | undefined): {
	text: string;
	noneListed: boolean;
} {
	if (km === null || km === undefined || !Number.isFinite(km)) {
		return { text: 'none listed', noneListed: true };
	}
	return { text: `${km.toFixed(1)} km`, noneListed: false };
}

export function formatTimestamp(iso: string | null): string {
	if (!iso) return '—';
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return iso;
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function titleise(value: string): string {
	return value.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/* ── summaries for the case list ──────────────────────────────────────── */

export interface ListSummary {
	total: number;
	byGroup: { spec: ActionGroupSpec; count: number }[];
	/** Cases where the gate sent a suspension somewhere else: protect or phantom. */
	divertedByGate: number;
	degraded: number;
	belowFloor: number;
}

export function summarise(cases: readonly Case[]): ListSummary {
	return {
		total: cases.length,
		byGroup: ACTION_GROUPS.map((spec) => ({
			spec,
			count: cases.filter((c) => c.action_group === spec.key).length
		})),
		divertedByGate: cases.filter((c) => c.gate === 'protect' || c.gate === 'phantom').length,
		degraded: cases.filter((c) => c.degraded).length,
		belowFloor: cases.filter((c) => c.below_floor).length
	};
}

// ── the case list's filter and sort ──────────────────────────────────────────
// These drove the landing screen from inside the page component, where nothing could reach them. A
// wrong predicate here hides a case from the demo silently, so they live out here with the rest of
// the logic and are tested directly.

export type CaseSort = 'confidence' | 'decided' | 'claim';

export interface CaseFilter {
	/** An action group, or 'all'. */
	group: ActionGroup | 'all';
	/** A gate state, 'all', or 'none' for the cases the gate never reached. */
	gate: GateState | 'all' | 'none';
	/** A trigger id, or 'all'. */
	trigger: string | 'all';
	/** A district name, or 'all'. Set by clicking the map as well as by the select. */
	district: string | 'all';
	degradedOnly: boolean;
}

export const NO_FILTER: CaseFilter = {
	group: 'all',
	gate: 'all',
	trigger: 'all',
	district: 'all',
	degradedOnly: false
};

export function filterIsActive(filter: CaseFilter): boolean {
	return (
		filter.group !== 'all' ||
		filter.gate !== 'all' ||
		filter.trigger !== 'all' ||
		filter.district !== 'all' ||
		filter.degradedOnly
	);
}

export function matchesFilter(c: Case, filter: CaseFilter): boolean {
	if (filter.group !== 'all' && c.action_group !== filter.group) return false;
	// 'none' is its own answer, not a missing one: the gate is computed only for actions that would
	// remove a provider, so "not computed" is a state a reader may legitimately want to isolate.
	if (filter.gate === 'none' && c.gate !== null) return false;
	if (filter.gate !== 'all' && filter.gate !== 'none' && c.gate !== filter.gate) return false;
	if (filter.trigger !== 'all' && c.trigger_id !== filter.trigger) return false;
	if (filter.district !== 'all' && (c.access?.district ?? null) !== filter.district) return false;
	if (filter.degradedOnly && !c.degraded) return false;
	return true;
}

export function compareCases(a: Case, b: Case, sort: CaseSort): number {
	if (sort === 'confidence') return a.confidence - b.confidence;
	if (sort === 'decided') return (a.decided_ts ?? '').localeCompare(b.decided_ts ?? '');
	return a.claim_id.localeCompare(b.claim_id);
}

/** Filter then sort, without mutating the input. */
export function arrangeCases(
	cases: readonly Case[],
	filter: CaseFilter,
	sort: CaseSort,
	descending: boolean
): Case[] {
	const rows = cases.filter((c) => matchesFilter(c, filter));
	return rows.sort((a, b) => (descending ? -compareCases(a, b, sort) : compareCases(a, b, sort)));
}

/** Every trigger id present in the data, sorted, with the unflagged cases left out. */
export function triggersPresent(cases: readonly Case[]): string[] {
	return [...new Set(cases.map((c) => c.trigger_id).filter((t): t is string => t !== null))].sort();
}

// ── the districts the run touched ────────────────────────────────────────────

export interface DistrictSummary {
	district: string;
	state: string;
	cases: number;
	/** Cases the gate diverted: a suspension that became an escalation or a de-listing referral. */
	diverted: number;
	/**
	 * The strongest gate any case in the district reached. A district holds several cases and the
	 * map can only draw it once, so it draws the most consequential thing that happened there —
	 * `protect` over `phantom` over `unknown` over `clear`. Never a gate no case actually reached.
	 */
	gate: GateState | null;
}

const GATE_RANK: Record<GateState, number> = { protect: 4, phantom: 3, unknown: 2, clear: 1 };

/**
 * Districts present in the run, ordered by how much happened in them. Cases with no access data —
 * a malformed claim never reaches the gate — carry no district and are left out entirely.
 */
export function districtsPresent(cases: readonly Case[]): DistrictSummary[] {
	const found = new Map<string, DistrictSummary>();
	for (const c of cases) {
		const district = c.access?.district;
		const state = c.access?.state;
		if (!district || !state) continue;
		const key = `${district}|${state}`;
		const entry = found.get(key) ?? { district, state, cases: 0, diverted: 0, gate: null };
		entry.cases += 1;
		if (c.gate === 'protect' || c.gate === 'phantom') entry.diverted += 1;
		if (c.gate !== null && (entry.gate === null || GATE_RANK[c.gate] > GATE_RANK[entry.gate])) {
			entry.gate = c.gate;
		}
		found.set(key, entry);
	}
	return [...found.values()].sort(
		(a, b) => b.cases - a.cases || a.district.localeCompare(b.district)
	);
}

/* ── the sections of one case record ──────────────────────────────────── */

export interface RecordSection {
	/** The id of the heading it jumps to, which is also the section's `aria-labelledby`. */
	id: string;
	/** The same mark the heading carries, so the index is a map of the page, not a second naming. */
	mark: string;
	label: string;
	emphatic: boolean;
}

/**
 * The sections a case record actually renders, in the order it renders them.
 *
 * "What was ordered" is only there when the decision ordered field work, so an index built from a
 * fixed list would point at a heading that is not on the page. Everything else is unconditional:
 * a section with nothing in it says so rather than disappearing, which is the whole convention of
 * this record — an absent reading is a fact about the case, not a gap in the page.
 */
export function recordSections(record: Case): RecordSection[] {
	const ordered =
		record.field_channels_ordered.length > 0 ||
		record.field_questions.length > 0 ||
		record.documents_requested.length > 0;

	return [
		{ id: 'trigger-h', mark: '1', label: 'What was flagged', emphatic: false },
		{ id: 'roster-h', mark: '2', label: 'Who looked at it', emphatic: false },
		{ id: 'findings-h', mark: '3', label: 'What they found', emphatic: false },
		{ id: 'defence-h', mark: '4', label: 'The hospital’s side', emphatic: false },
		{ id: 'review-h', mark: '5', label: 'Who checked the readings', emphatic: false },
		{ id: 'gate-heading', mark: GATES.protect.mark, label: 'The access gate', emphatic: true },
		...(ordered ? [{ id: 'field-h', mark: '·', label: 'What was ordered', emphatic: false }] : []),
		{ id: 'trail-h', mark: '6', label: 'How they worked', emphatic: false },
		{ id: 'artefact-h', mark: '7', label: 'What was issued', emphatic: false }
	];
}
