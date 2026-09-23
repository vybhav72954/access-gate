import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import AccessGate from '$lib/components/AccessGate.svelte';
import Confidence from '$lib/components/Confidence.svelte';
import FindingCard from '$lib/components/FindingCard.svelte';
import GateBadge from '$lib/components/GateBadge.svelte';
import Roster from '$lib/components/Roster.svelte';
import Trail from '$lib/components/Trail.svelte';
import { makeScale } from '$lib/map';
import type {
	Access,
	Benchmark,
	BenchmarkSeries,
	Case,
	Evaluation,
	Finding,
	Meta,
	RosterEntry
} from '$lib/types';

const finding = (over: Partial<Finding> = {}): Finding => ({
	reading: 'desk_audit',
	reading_title: 'Desk audit',
	agent_key: 'desk_investigator',
	agent_title: 'Desk Investigator',
	channel: null,
	stance: 'supports',
	confidence: 0.95,
	conclusion: 'Admission recorded after the certified death.',
	citation: 'death_certificate',
	disputed: false,
	dispute_refused: false,
	...over
});

const agent = (
	key: RosterEntry['key'],
	title: string,
	over: Partial<RosterEntry> = {}
): RosterEntry => ({
	key,
	title,
	owns: 'something',
	ran: false,
	order: null,
	mandatory: false,
	opened_by_router: false,
	router_reason: null,
	...over
});

const access: Access = {
	district: 'Bahraich',
	state: 'Uttar Pradesh',
	specialty: 'cardiology',
	specialty_name: 'Cardiology',
	hospital_listed: true,
	n_providers: 1,
	km_to_alternative: 83.3,
	nearest_alternative: 'Gonda, 83 km away',
	nearest_alternative_district: 'Gonda',
	population: 4156731,
	aspirational: true,
	capability_ok: true,
	capability_reason: 'no facility-tier / specialty mismatch'
};

describe('Confidence', () => {
	it('never shows a bare number: the floor is in the accessible label', () => {
		render(Confidence, { value: 0.46 });
		expect(screen.getByRole('img')).toHaveAccessibleName('Confidence 0.46, below the 0.70 floor.');
	});

	it('says "below the floor" in visible text too', () => {
		render(Confidence, { value: 0.31 });
		expect(screen.getByText(/below the/)).toBeInTheDocument();
	});

	it('does not claim a value at the floor is below it', () => {
		render(Confidence, { value: 0.7 });
		expect(screen.getByText(/at or above the/)).toBeInTheDocument();
		expect(screen.queryByText(/below the/)).not.toBeInTheDocument();
	});

	it('draws the floor marker at the floor, not at the value', () => {
		const { container } = render(Confidence, { value: 0.95 });
		const track = container.querySelector('.track') as HTMLElement;
		expect(track.style.getPropertyValue('--floor')).toBe('0.7');
		expect(track.style.getPropertyValue('--fraction')).toBe('0.95');
	});
});

describe('GateBadge', () => {
	it('renders a null gate as "not computed", never as clear', () => {
		render(GateBadge, { gate: null });
		expect(screen.getByText('Not computed')).toBeInTheDocument();
		expect(screen.queryByText('Clear')).not.toBeInTheDocument();
	});

	it('labels each gate state in words as well as colour', () => {
		render(GateBadge, { gate: 'protect' });
		expect(screen.getByText('Protected')).toBeInTheDocument();
	});
});

describe('FindingCard', () => {
	it('shows a reading that stands with no dispute note', () => {
		render(FindingCard, { finding: finding() });
		expect(screen.getByText(/Admission recorded/)).toBeInTheDocument();
		expect(screen.queryByText(/set aside/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/Dispute refused/i)).not.toBeInTheDocument();
	});

	it('marks a disputed reading as set aside and weighing nothing', () => {
		const { container } = render(FindingCard, {
			finding: finding({ disputed: true })
		});
		expect(container.querySelector('[data-state="disputed"]')).not.toBeNull();
		expect(screen.getByText(/set aside/i)).toBeInTheDocument();
		expect(screen.getByText(/weighs nothing/i)).toBeInTheDocument();
	});

	it('carries the reviewer’s reason on a disputed reading', () => {
		render(FindingCard, {
			finding: finding({ disputed: true }),
			reviewReason: 'The citation does not support the conclusion.'
		});
		expect(screen.getByText(/citation does not support/)).toBeInTheDocument();
	});

	it('renders a refused dispute differently, and says the reading stands', () => {
		const { container } = render(FindingCard, {
			finding: finding({ dispute_refused: true })
		});
		expect(container.querySelector('[data-state="dispute-refused"]')).not.toBeNull();
		expect(screen.getByText(/the reading stands/i)).toBeInTheDocument();
		expect(screen.getByText(/never a measurement/i)).toBeInTheDocument();
		// It is not a dispute, so it must not be struck aside.
		expect(screen.queryByText(/weighs nothing/i)).not.toBeInTheDocument();
	});
});

describe('Roster', () => {
	const roster: RosterEntry[] = [
		agent('case_router', 'Case Router', {
			ran: true,
			order: 0,
			mandatory: true
		}),
		agent('desk_investigator', 'Desk Investigator', {
			ran: true,
			order: 1,
			mandatory: true
		}),
		agent('billing_analyst', 'Billing & Tariff Analyst'),
		agent('provider_advocate', 'Provider Advocate', {
			ran: true,
			order: 2,
			opened_by_router: true,
			router_reason: 'the file bears an innocent account worth testing'
		}),
		agent('medical_auditor', 'Medical Auditor'),
		agent('field_evidence_analyst', 'Field Evidence Analyst'),
		agent('audit_reviewer', 'Audit Reviewer', {
			ran: true,
			order: 3,
			mandatory: true
		}),
		agent('committee_liaison', 'Committee Liaison'),
		agent('enforcement_officer', 'Enforcement Officer', {
			ran: true,
			order: 4,
			mandatory: true
		})
	];

	it('separates mandatory agents from the ones the Router opened', () => {
		const { container } = render(Roster, { roster, degraded: false });
		const worked = container.querySelector('.worked') as HTMLElement;
		const advocate = within(worked).getByText('Provider Advocate').closest('li') as HTMLElement;
		const desk = within(worked).getByText('Desk Investigator').closest('li') as HTMLElement;
		expect(advocate.className).toContain('opened');
		expect(desk.className).not.toContain('opened');
		expect(within(advocate).getByText('opened by the Router')).toBeInTheDocument();
		expect(within(desk).getByText('mandatory')).toBeInTheDocument();
	});

	it('shows the Router’s stated reason for the work it added', () => {
		render(Roster, { roster, degraded: false });
		expect(screen.getByText(/innocent account worth testing/)).toBeInTheDocument();
	});

	it('lists the agents that did not run rather than hiding them', () => {
		const { container } = render(Roster, { roster, degraded: false });
		const absent = container.querySelector('.absent') as HTMLElement;
		expect(within(absent).getByText('Medical Auditor')).toBeInTheDocument();
		expect(within(absent).getByText('Committee Liaison')).toBeInTheDocument();
	});

	it('does not render an empty crew for a degraded case', () => {
		render(Roster, {
			roster: roster.map((a) => agent(a.key, a.title)),
			degraded: true
		});
		expect(screen.getByText(/No agent worked this case/)).toBeInTheDocument();
		expect(screen.queryByText('mandatory')).not.toBeInTheDocument();
	});
});

describe('AccessGate', () => {
	it('gives protect the emphatic treatment', () => {
		const { container } = render(AccessGate, {
			gate: 'protect',
			action: 'escalate_to_sec',
			access,
			atStake: [
				{
					specialty_name: 'Cardiology',
					n_providers: 1,
					nearest_alternative: 'Gonda, 83 km away',
					nearest_alternative_district: 'Gonda',
					why_protected: 'sole real provider, NITI aspirational district'
				}
			],
			specialtiesAtStake: ['cardiology']
		});
		expect(container.querySelector('.emphatic')).not.toBeNull();
		expect(screen.getByText('Protected')).toBeInTheDocument();
		expect(screen.getByText(/State Empanelment Committee/)).toBeInTheDocument();
	});

	it('says a null gate was not computed, and why', () => {
		render(AccessGate, {
			gate: null,
			action: 'show_cause_notice',
			access: null,
			atStake: [],
			specialtiesAtStake: []
		});
		expect(screen.getByText('Not computed')).toBeInTheDocument();
		expect(screen.getByText(/removes no provider/)).toBeInTheDocument();
	});

	it('reads a null distance as "none listed", not as missing', () => {
		render(AccessGate, {
			gate: 'clear',
			action: 'suspend_hospital',
			access: { ...access, km_to_alternative: null, nearest_alternative: null },
			atStake: [],
			specialtiesAtStake: []
		});
		expect(screen.getByText('none listed')).toBeInTheDocument();
		expect(screen.getByText(/not a missing value/)).toBeInTheDocument();
	});

	it('does not claim a clear gate for a refused claim', () => {
		render(AccessGate, {
			gate: null,
			action: 'refuse_malformed',
			access: null,
			atStake: [],
			specialtiesAtStake: []
		});
		expect(screen.getByText(/refused as malformed/)).toBeInTheDocument();
	});
});

describe('Trail', () => {
	it('explains an empty trail on a degraded case rather than showing nothing', () => {
		render(Trail, { trail: [], degraded: true });
		expect(screen.getByText(/No tool was called/)).toBeInTheDocument();
	});

	// A released or refused claim issues no artefact, so the export has no Markdown to parse the full
	// trail out of — but the decision log recorded every call. Falling back to that summary is the
	// difference between "six agents worked this case" and a page saying they called nothing.
	it('falls back to the log summary when the case issued no artefact', () => {
		render(Trail, {
			trail: [],
			degraded: false,
			toolsCalled: [
				'desk_investigator:documents_shared_with_other_claims',
				'medical_auditor:read_document',
				'enforcement_officer:release_claim'
			]
		});
		expect(screen.getByText(/3 tool calls/)).toBeInTheDocument();
		expect(screen.getByText('medical_auditor')).toBeInTheDocument();
		expect(screen.getByText('documents_shared_with_other_claims')).toBeInTheDocument();
		expect(screen.queryByText(/lists no tool calls/)).not.toBeInTheDocument();
	});

	it('still says so when the case genuinely called nothing', () => {
		render(Trail, { trail: [], degraded: false, toolsCalled: [] });
		expect(screen.getByText(/lists no tool calls/)).toBeInTheDocument();
	});

	it('counts the calls and flags the refused ones', () => {
		render(Trail, {
			degraded: false,
			trail: [
				{
					n: 1,
					agent_key: 'desk_investigator',
					agent_title: 'Desk Investigator',
					tool: 'read_document',
					arguments: [{ name: 'kind', value: 'death_certificate' }],
					outcome: 'read',
					refused: false
				},
				{
					n: 2,
					agent_key: 'enforcement_officer',
					agent_title: 'Enforcement Officer',
					tool: 'suspend_hospital',
					arguments: [],
					outcome: 'the policy decided escalate_to_sec',
					refused: true
				}
			]
		});
		expect(screen.getByText(/2 tool calls/)).toBeInTheDocument();
		expect(screen.getByText(/1 refused/)).toBeInTheDocument();
		expect(screen.getByText('refused')).toBeInTheDocument();
	});
});

// ── page structure ───────────────────────────────────────────────────────────
// Read as source rather than rendered: a route needs its whole `load` to render, and what is being
// checked here is the document outline, which is a property of the markup.

describe('every route names itself with exactly one h1', () => {
	const routes = {
		'case list': 'src/routes/+page.svelte',
		'case record': 'src/routes/case/[claim_id]/+page.svelte',
		evaluation: 'src/routes/evaluation/+page.svelte',
		benchmark: 'src/routes/benchmark/+page.svelte'
	};

	for (const [name, file] of Object.entries(routes)) {
		it(`${name} has one h1`, async () => {
			const { readFileSync } = await import('node:fs');
			const source = readFileSync(file, 'utf8');
			// The case record's is visually hidden — the claim id and action badge carry it on screen —
			// but without it the page's seven h2 sections hang off no heading at all.
			expect(source.match(/<h1[\s>]/g) ?? []).toHaveLength(1);
		});
	}
});

// ── IndiaMap ─────────────────────────────────────────────────────────────────
// A map is the easiest component to be wrong about without noticing: a wrong shade still paints and
// a missing district still leaves a map on the page. These check the things the eye will not.

describe('IndiaMap', () => {
	const load = async () => (await import('$lib/components/IndiaMap.svelte')).default;
	const values = new Map([
		['Karnataka', 175],
		['Uttar Pradesh', 55],
		['Jharkhand', 36]
	]);

	it('draws the country and every state when nothing is cropped', async () => {
		const { container } = render(await load(), { label: 'India' });
		expect(container.querySelector('path.halo')).not.toBeNull();
		expect(container.querySelectorAll('path.state')).toHaveLength(36);
	});

	it('shades a state it has a figure for and hatches one it does not', async () => {
		const scale = makeScale([...values.values()]);
		const { container } = render(await load(), {
			label: 'India',
			values,
			scale,
			measure: 'escalations a year'
		});
		const live = container.querySelectorAll('path.state.live');
		expect(live).toHaveLength(3);
		for (const path of live) {
			expect((path as SVGPathElement).style.fill).toMatch(/var\(--choro-[1-5]\)/);
		}
		expect(container.querySelectorAll('path.state.unmodelled').length).toBe(33);
	});

	it('names every figure in the accessible label, so the shade is never the only carrier', async () => {
		const scale = makeScale([...values.values()]);
		render(await load(), { label: 'India', values, scale, measure: 'escalations a year' });
		expect(
			screen.getByRole('button', { name: /Karnataka: 175 escalations a year/ })
		).toBeInTheDocument();
	});

	it('repeats every figure as text for a reader who cannot see the map at all', async () => {
		const scale = makeScale([...values.values()]);
		const { container } = render(await load(), {
			label: 'India',
			values,
			scale,
			measure: 'escalations a year'
		});
		const listed = container.querySelectorAll('ul.sr-only li');
		expect(listed).toHaveLength(3);
		expect([...listed].map((li) => li.textContent?.trim())).toContain(
			'Karnataka: 175 escalations a year'
		);
	});

	it('reports the state a reader picks', async () => {
		const scale = makeScale([...values.values()]);
		let picked: string | null = 'nothing yet';
		render(await load(), {
			label: 'India',
			values,
			scale,
			measure: 'escalations a year',
			onselect: (name: string | null) => (picked = name)
		});
		await fireEvent.click(screen.getByRole('button', { name: /Karnataka/ }));
		expect(picked).toBe('Karnataka');
	});

	it('clears the selection when the chosen state is picked again', async () => {
		const scale = makeScale([...values.values()]);
		let picked: string | null = 'nothing yet';
		render(await load(), {
			label: 'India',
			values,
			scale,
			measure: 'escalations a year',
			selected: 'Karnataka',
			onselect: (name: string | null) => (picked = name)
		});
		await fireEvent.click(screen.getByRole('button', { name: /Karnataka/ }));
		expect(picked).toBeNull();
	});

	it('answers the keyboard, since a state is a path and gets no key handling for free', async () => {
		const scale = makeScale([...values.values()]);
		const picked: (string | null)[] = [];
		const { container } = render(await load(), {
			label: 'India',
			values,
			scale,
			measure: 'escalations a year',
			onselect: (name: string | null) => picked.push(name)
		});
		const karnataka = [...container.querySelectorAll('path.state.live')].find((p) =>
			p.getAttribute('aria-label')?.startsWith('Karnataka')
		) as SVGPathElement;
		expect(karnataka.getAttribute('tabindex')).toBe('0');
		await fireEvent.keyDown(karnataka, { key: 'Enter' });
		await fireEvent.keyDown(karnataka, { key: ' ' });
		expect(picked).toEqual(['Karnataka', 'Karnataka']);
	});

	it('leaves a state with no figure out of the tab order entirely', async () => {
		const scale = makeScale([...values.values()]);
		const { container } = render(await load(), {
			label: 'India',
			values,
			scale,
			measure: 'escalations a year'
		});
		for (const path of container.querySelectorAll('path.state.unmodelled')) {
			expect(path.getAttribute('tabindex')).toBeNull();
			expect(path.getAttribute('aria-hidden')).toBe('true');
		}
	});

	it('marks a pinned district with a real button, not a decorative dot', async () => {
		render(await load(), {
			label: 'Where the cases are',
			pins: [
				{
					name: 'Bahraich',
					state: 'Uttar Pradesh',
					tone: 'protect' as const,
					label: 'Bahraich, Uttar Pradesh',
					detail: '2 cases'
				}
			]
		});
		expect(
			screen.getByRole('button', { name: /Bahraich, Uttar Pradesh\. 2 cases/ })
		).toBeInTheDocument();
	});

	it('crops to the districts it is focused on, leaving distant states out of the page', async () => {
		const { container } = render(await load(), {
			label: 'Bahraich',
			pins: [
				{
					name: 'Bahraich',
					state: 'Uttar Pradesh',
					tone: 'protect' as const,
					label: 'Bahraich'
				}
			],
			focus: [{ name: 'Bahraich', state: 'Uttar Pradesh' }]
		});
		const drawn = container.querySelectorAll('path.state');
		expect(drawn.length).toBeGreaterThan(0);
		expect(drawn.length).toBeLessThan(36);
		const box = container.querySelector('svg')?.getAttribute('viewBox')?.split(' ').map(Number);
		expect(box?.[2]).toBeLessThan(400);
	});

	it('draws the journey between a district and its alternative, labelled once', async () => {
		const { container } = render(await load(), {
			label: 'Bahraich and Gonda',
			pins: [
				{ name: 'Bahraich', state: 'Uttar Pradesh', tone: 'protect' as const, label: 'Bahraich' },
				{ name: 'Gonda', state: 'Uttar Pradesh', tone: 'alternative' as const, label: 'Gonda' }
			],
			connectors: [
				{
					from: { name: 'Bahraich', state: 'Uttar Pradesh' },
					to: { name: 'Gonda', state: 'Uttar Pradesh' },
					label: '83 km'
				}
			],
			focus: [{ name: 'Bahraich', state: 'Uttar Pradesh' }]
		});
		expect(container.querySelectorAll('line.connector')).toHaveLength(1);
		expect(container.querySelector('.distance')?.textContent?.trim()).toBe('83 km');
	});

	it('drops the on-map distance labels when several would land on top of each other', async () => {
		const spokes = ['Nandurbar', 'Dhule'].map((name) => ({
			from: { name: 'Barwani', state: 'Madhya Pradesh' },
			to: { name, state: 'Maharashtra' },
			label: name === 'Dhule' ? '83 km' : '47 km'
		}));
		const { container } = render(await load(), {
			label: 'Barwani',
			pins: [
				{ name: 'Barwani', state: 'Madhya Pradesh', tone: 'phantom' as const, label: 'Barwani' }
			],
			connectors: spokes,
			focus: [{ name: 'Barwani', state: 'Madhya Pradesh' }]
		});
		expect(container.querySelectorAll('line.connector')).toHaveLength(2);
		expect(container.querySelector('.distance')).toBeNull();
	});

	it('ignores a district it has no shape for instead of drawing a marker at the origin', async () => {
		const { container } = render(await load(), {
			label: 'Nowhere',
			pins: [{ name: 'Atlantis', state: 'Nowhere', tone: 'plain' as const, label: 'Atlantis' }]
		});
		expect(container.querySelectorAll('.pin')).toHaveLength(0);
		expect(container.querySelectorAll('path.state')).toHaveLength(36);
	});
});

// ── the benchmark case table ─────────────────────────────────────────────────
// A degraded row on a crew path is the *rules'* answer: `metrics/benchmark.py` leaves it out of
// the totals. Printing its score anyway put a red "wrong" in the table under a headline reporting
// none, and credited the crew with a decision it never made. These render the real committed
// benchmark, because the contradiction only exists when the two halves disagree.

describe('the benchmark case table', () => {
	const load = async () => (await import('../routes/benchmark/+page.svelte')).default;

	const readJson = async <T>(name: string): Promise<T> => {
		const { readFileSync } = await import('node:fs');
		const { join } = await import('node:path');
		return JSON.parse(readFileSync(join(process.cwd(), 'static/data', name), 'utf8')) as T;
	};
	const readBenchmark = () => readJson<Benchmark>('benchmark.json');
	const readMeta = () => readJson<Meta>('meta.json');

	/** Both files, as the route's own `load` hands them over. */
	const pageData = async () => ({ benchmark: await readBenchmark(), meta: await readMeta() });

	const rowFor = (container: HTMLElement, caseId: string): HTMLElement => {
		const heading = [...container.querySelectorAll('th[scope="row"]')].find(
			(cell) => cell.textContent?.trim() === caseId
		);
		if (!heading) throw new Error(`no row for ${caseId}`);
		const row = heading.closest('tr');
		if (!row) throw new Error(`${caseId} is not in a row`);
		return row as HTMLElement;
	};

	const seriesNamed = (benchmark: Benchmark, key: string): BenchmarkSeries => {
		const series = benchmark.series[key];
		if (!series) throw new Error(`the committed benchmark has no "${key}" series`);
		return series;
	};

	/** The series the page opens on: the last one, which is the run the benchmark is about. */
	const crewSeries = (benchmark: Benchmark) => {
		const key = Object.keys(benchmark.series).at(-1);
		if (!key) throw new Error('the committed benchmark has no series at all');
		const series = seriesNamed(benchmark, key);
		expect(series.crew, `the last series (${key}) is not a crew run`).toBeTruthy();
		return { key, series };
	};

	it('opens on the crew path, which is the run the benchmark is about', async () => {
		const data = await pageData();
		const { container } = render(await load(), { data });
		const select = container.querySelector('#f-series') as HTMLSelectElement;
		expect(select.value).toBe(crewSeries(data.benchmark).key);
	});

	it('prints no score for a degraded case on a crew path', async () => {
		const data = await pageData();
		const { series } = crewSeries(data.benchmark);
		const degraded = series.cases.filter((row) => row.degraded);
		expect(degraded.length, 'no degraded case in the crew run to check').toBeGreaterThan(0);

		const { container } = render(await load(), { data });
		for (const row of degraded) {
			const tr = rowFor(container, row.case_id);
			expect(within(tr).getByText('not scored')).toBeInTheDocument();
			expect(tr.querySelector('.score')).toBeNull();
		}
	});

	it('shows exactly as many wrong badges as the headline reports', async () => {
		// The check that would have caught it: the table and the split above it are one claim.
		const data = await pageData();
		const { series } = crewSeries(data.benchmark);
		const { container } = render(await load(), { data });
		const table = container.querySelector('table.record') as HTMLElement;
		expect(table.querySelectorAll('.score[data-tone="wrong"]')).toHaveLength(series.overall.wrong);
		expect(table.querySelectorAll('.score')).toHaveLength(series.scored ?? 0);
	});

	it('still scores every case on the rules path, where degraded is the normal state', async () => {
		// No model runs there, so every row is degraded; dropping their scores would empty the column.
		const data = await pageData();
		const rules = seriesNamed(data.benchmark, 'rules');
		const { container } = render(await load(), { data });
		const select = container.querySelector('#f-series') as HTMLSelectElement;
		await fireEvent.change(select, { target: { value: 'rules' } });

		const table = container.querySelector('table.record') as HTMLElement;
		expect(table.querySelectorAll('.score')).toHaveLength(rules.cases.length);
		expect(table.querySelectorAll('.score[data-tone="wrong"]')).toHaveLength(rules.overall.wrong);
		expect(within(table).queryByText('not scored')).toBeNull();
	});
});

// ── the interactive pages, driven ────────────────────────────────────────────
// `domain.test.ts` proves the filter and sort logic. These prove the pages are actually wired to
// it: a route can import the right helper and never bind it, and the result is a control that
// moves and a table that does not. Normally a browser would catch that; this is that check,
// rendered.

describe('the case list, driven', () => {
	const load = async () => (await import('../routes/+page.svelte')).default;

	const pageData = async () => {
		const { readFileSync } = await import('node:fs');
		const { join } = await import('node:path');
		const read = <T>(name: string): T =>
			JSON.parse(readFileSync(join(process.cwd(), 'static/data', name), 'utf8')) as T;
		return { cases: read<Case[]>('cases.json'), meta: read<Meta>('meta.json') };
	};

	const bodyRows = (container: HTMLElement) => container.querySelectorAll('table.record tbody tr');

	/** The claim id alone: the heading cell also carries marks such as the degraded flag. */
	const idsShown = (container: HTMLElement) =>
		[...bodyRows(container)].map(
			(tr) =>
				tr
					.querySelector('th')
					?.textContent?.trim()
					.match(/CLM-\S+/)?.[0]
		);

	it('lists every decided case before anything is filtered', async () => {
		const data = await pageData();
		const { container } = render(await load(), { data });
		expect(bodyRows(container)).toHaveLength(data.cases.length);
	});

	it('narrows to the degraded case when that toggle is set, and restores it', async () => {
		const data = await pageData();
		const degraded = data.cases.filter((c) => c.degraded).map((c) => c.claim_id);
		expect(degraded.length, 'no degraded case to filter to').toBeGreaterThan(0);

		const { container } = render(await load(), { data });
		const toggle = container.querySelector('.filters input[type="checkbox"]') as HTMLInputElement;
		await fireEvent.click(toggle);

		expect(idsShown(container)).toEqual(degraded);

		await fireEvent.click(toggle);
		expect(bodyRows(container)).toHaveLength(data.cases.length);
	});

	it('filters on the gate to exactly the cases that carry it', async () => {
		const data = await pageData();
		const protectedIds = data.cases.filter((c) => c.gate === 'protect').map((c) => c.claim_id);
		expect(protectedIds.length, 'no protected case to filter to').toBeGreaterThan(0);

		const { container } = render(await load(), { data });
		const select = container.querySelector('#f-gate') as HTMLSelectElement;
		await fireEvent.change(select, { target: { value: 'protect' } });

		expect(idsShown(container).sort()).toEqual([...protectedIds].sort());
	});

	it('says so rather than showing an empty table when nothing matches', async () => {
		const data = await pageData();
		const { container } = render(await load(), { data });
		// A released claim never reaches the gate, so this pair can match nothing.
		await fireEvent.change(container.querySelector('#f-group') as HTMLSelectElement, {
			target: { value: 'released' }
		});
		await fireEvent.change(container.querySelector('#f-gate') as HTMLSelectElement, {
			target: { value: 'protect' }
		});
		expect(bodyRows(container)).toHaveLength(0);
		expect(screen.getByText(/No cases match these filters/i)).toBeInTheDocument();
	});
});

describe('the threshold scrubber, driven', () => {
	const load = async () => (await import('../routes/evaluation/+page.svelte')).default;

	const pageData = async () => {
		const { readFileSync } = await import('node:fs');
		const { join } = await import('node:path');
		const read = <T>(name: string): T =>
			JSON.parse(readFileSync(join(process.cwd(), 'static/data', name), 'utf8')) as T;
		return { evaluation: read<Evaluation>('evaluation.json'), meta: read<Meta>('meta.json') };
	};

	const readouts = (container: HTMLElement) =>
		[...container.querySelectorAll('.readout .readout-value')].map((el) => el.textContent?.trim());

	it('starts where the policy stands', async () => {
		const data = await pageData();
		const { container } = render(await load(), { data });
		const slider = container.querySelector('#km-slider') as HTMLInputElement;
		expect(Number(slider.value)).toBe(data.evaluation.distance_material_km);
	});

	it('reads a swept row rather than interpolating one', async () => {
		// The page's whole claim: the viewer decides nothing, so every figure the slider shows has
		// to be a number already in the export, at the km the slider is on.
		const data = await pageData();
		const sweep = data.evaluation.threshold_sweep;
		const { container } = render(await load(), { data });
		const slider = container.querySelector('#km-slider') as HTMLInputElement;

		for (const km of [0, 75, 200]) {
			await fireEvent.input(slider, { target: { value: String(km) } });
			const row = sweep.find((r) => Number(r.km) === km);
			expect(row, `the export has no row at ${km} km`).toBeDefined();
			const shown = readouts(container);
			const expected = Number(row!.hospitals_escalated);
			expect(shown[0]?.replace(/[^\d]/g, '')).toBe(String(Math.round(expected)));
		}
	});

	it('offers a way back to the threshold actually in use', async () => {
		const data = await pageData();
		const { container } = render(await load(), { data });
		const slider = container.querySelector('#km-slider') as HTMLInputElement;
		await fireEvent.input(slider, { target: { value: '120' } });

		const back = screen.getByRole('button', { name: /back to the .* km in use/ });
		await fireEvent.click(back);
		expect(Number(slider.value)).toBe(data.evaluation.distance_material_km);
	});
});
