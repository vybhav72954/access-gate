<script lang="ts">
	import DegradedMark from '$lib/components/DegradedMark.svelte';
	import OutcomeBar from '$lib/components/OutcomeBar.svelte';
	import { ACTION_LABELS, formatNumber } from '$lib/domain';
	import type { ActionKey, BenchmarkCase, BenchmarkSeries } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const b = $derived(data.benchmark);

	const seriesList = $derived<BenchmarkSeries[]>(Object.values(b.series));
	// Default to the final crew run: it is the one the benchmark is about.
	// eslint-disable-next-line svelte/prefer-writable-derived -- the initial choice, then user-owned
	let selectedKey = $state<string>(initialSeriesKey());
	function initialSeriesKey(): string {
		return Object.keys(data.benchmark.series).at(-1) ?? '';
	}
	const selected = $derived<BenchmarkSeries | null>(b.series[selectedKey] ?? null);

	let truthFilter = $state<'all' | 'fraud' | 'innocent'>('all');
	let readingFilter = $state<string>('all');

	const rows = $derived(
		(selected?.cases ?? []).filter((row) => {
			if (truthFilter !== 'all' && row.truth !== truthFilter) return false;
			if (readingFilter !== 'all' && row.reading !== readingFilter) return false;
			return true;
		})
	);

	const scale = $derived(
		Math.max(...seriesList.flatMap((s) => [s.overall.innocent.n, s.overall.fraud.n]), 1)
	);

	const SCORE_TONE: Record<string, string> = {
		correct: 'correct',
		acceptable: 'deferred',
		costly: 'deferred',
		wrong: 'wrong'
	};

	const SCORE_NOTE: Record<string, string> = {
		correct: 'the decision the truth called for',
		acceptable: 'deferred a fraud — slower, not wrong',
		costly: 'deferred an innocent claim — a real cost to the hospital',
		wrong: 'the decision the truth ruled out'
	};

	/**
	 * Whether a row is this path's own answer, and so carries a score.
	 *
	 * This is the predicate `metrics/benchmark.py` scores on: `not (crew and degraded)`. On the
	 * rules path every row is degraded by definition — no model runs — and all forty are scored.
	 * On a crew path a degraded row is the *rules'* answer, left out of the totals; printing its
	 * score anyway put a red “wrong” in the table under a headline that reports none, and credited
	 * the crew with a decision it never made.
	 */
	const crewPath = $derived(Boolean(selected?.crew));
	const isScored = (row: BenchmarkCase): boolean => !(crewPath && row.degraded);
	const NOT_SCORED_NOTE =
		'The rules decided this one, so it is not this path’s answer and counts towards neither column.';

	function actionLabel(action: string | null): string {
		if (!action) return '—';
		return ACTION_LABELS[action as ActionKey] ?? action;
	}

	function crewStat(series: BenchmarkSeries, key: string): string {
		const crew = series.crew;
		if (!crew) return '—';
		const value = crew[key];
		return typeof value === 'number' ? String(value) : '—';
	}

	function readingOf(row: BenchmarkCase): string {
		return row.reading ?? '—';
	}
</script>

<svelte:head>
	<title>Benchmark — The Access Gate</title>
</svelte:head>

<div class="hero">
	<div class="wrap hero-in">
		<header class="intro">
			<p class="eyebrow">Agent benchmark · {b.cases} hand-written cases</p>
			<h1>Rules versus the crew</h1>
			<p class="lede">
				{b.fraud} frauds and {(b.cases ?? 0) - (b.fraud ?? 0)} innocent claims whose truth is stated in
				the documents, run through both paths of the same pipeline, with the same policy deciding. The
				truth is written six ways — plainly, in paraphrase, as a trap, mislabelled, as a contradiction,
				and across claims.
			</p>
			<p class="caveat">{b.caveat}</p>
		</header>
	</div>
</div>

<div class="wrap">
	<!-- Scoring is asymmetric, and the page says so before it shows a number. -->
	<section aria-labelledby="scoring-h" class="scoring">
		<h2 id="scoring-h">How a decision is scored</h2>
		<div class="scoring-grid">
			<div>
				<p class="truth-label">For an innocent claim</p>
				<ul class="score-key">
					<li><span class="sw" data-tone="correct"></span> released — <strong>correct</strong></li>
					<li>
						<span class="sw hatched" data-tone="deferred"></span> deferred —
						<strong>costly</strong>, a real cost to a hospital that did nothing
					</li>
					<li><span class="sw" data-tone="wrong"></span> enforced — <strong>wrong</strong></li>
				</ul>
			</div>
			<div>
				<p class="truth-label">For a fraud</p>
				<ul class="score-key">
					<li><span class="sw" data-tone="correct"></span> enforced — <strong>correct</strong></li>
					<li>
						<span class="sw" data-tone="deferred"></span> deferred — <strong>acceptable</strong>,
						slower but not wrong
					</li>
					<li><span class="sw" data-tone="wrong"></span> released — <strong>wrong</strong></li>
				</ul>
			</div>
		</div>
		<p class="scoring-note">
			“Costly” and “wrong” are not the same thing, and neither is “acceptable”. The same decision —
			deferring — scores differently depending on what was true, so it is drawn in one hue and
			separated by label, and hatched where it cost an innocent hospital.
		</p>
	</section>

	<section aria-labelledby="overall-h">
		<h2 id="overall-h">Overall</h2>
		<div class="series-stack">
			{#each seriesList as series (series.key)}
				<article class="series">
					<header>
						<h3>{series.label}</h3>
						<p class="scored">
							{series.scored} of {series.n_cases} scored
							{#if series.crew && crewStat(series, 'degraded') !== '0'}
								· <DegradedMark /> {crewStat(series, 'degraded')} degraded
							{/if}
						</p>
					</header>

					<div class="halves">
						<div>
							<p class="truth-label">Innocent · {series.overall.innocent.n}</p>
							<OutcomeBar split={series.overall} truth="innocent" scaleTo={scale} />
						</div>
						<div>
							<p class="truth-label">Fraud · {series.overall.fraud.n}</p>
							<OutcomeBar split={series.overall} truth="fraud" scaleTo={scale} />
						</div>
					</div>

					{#if series.crew}
						<dl class="crew">
							<div>
								<dt>Agents</dt>
								<dd>{crewStat(series, 'agents')}</dd>
							</div>
							<div>
								<dt>Router opened</dt>
								<dd>{crewStat(series, 'router_opened_cases')} cases</dd>
							</div>
							<div>
								<dt>Advocate ran</dt>
								<dd>{crewStat(series, 'advocate_ran')}</dd>
							</div>
							<div>
								<dt>Defence stood</dt>
								<dd>{crewStat(series, 'defence_stood')}</dd>
							</div>
							<div>
								<dt>Disputed</dt>
								<dd>{crewStat(series, 'disputed_cases')}</dd>
							</div>
							<div>
								<dt>Disputes refused</dt>
								<dd>{crewStat(series, 'disputes_refused_cases')}</dd>
							</div>
							<div>
								<dt>Refused calls</dt>
								<dd>{crewStat(series, 'refused_calls')}</dd>
							</div>
							<div>
								<dt>Officer executed</dt>
								<dd>{crewStat(series, 'officer_acted')}</dd>
							</div>
							<div>
								<dt>Medical ran</dt>
								<dd>{crewStat(series, 'medical_ran')}</dd>
							</div>
						</dl>
					{/if}
				</article>
			{/each}
		</div>
	</section>

	<section aria-labelledby="reading-h">
		<h2 id="reading-h">By reading</h2>
		<p class="section-note">
			How the truth was written into the documents. The keyword cases are where patterns should
			work; the trap and contradiction cases are where they should not.
		</p>

		<div class="reading-grid">
			{#each b.readings as reading (reading)}
				<article class="reading">
					<h3>{reading}</h3>
					{#each seriesList as series (series.key)}
						{@const split = series.by_reading[reading]}
						{#if split && split.n > 0}
							<div class="reading-row">
								<p class="series-name">{series.label}</p>
								<div class="reading-halves">
									{#if split.innocent.n > 0}
										<div>
											<span class="mini-label">innocent {split.innocent.n}</span>
											<OutcomeBar {split} truth="innocent" compact />
										</div>
									{/if}
									{#if split.fraud.n > 0}
										<div>
											<span class="mini-label">fraud {split.fraud.n}</span>
											<OutcomeBar {split} truth="fraud" compact />
										</div>
									{/if}
								</div>
							</div>
						{/if}
					{/each}
				</article>
			{/each}
		</div>
	</section>

	<section aria-labelledby="cases-h">
		<h2 id="cases-h">Every case</h2>

		<div class="filters">
			<div class="field">
				<label for="f-series">Path</label>
				<select id="f-series" bind:value={selectedKey}>
					{#each seriesList as series (series.key)}
						<option value={series.key}>{series.label}</option>
					{/each}
				</select>
			</div>
			<div class="field">
				<label for="f-truth">Truth</label>
				<select id="f-truth" bind:value={truthFilter}>
					<option value="all">All</option>
					<option value="fraud">Fraud</option>
					<option value="innocent">Innocent</option>
				</select>
			</div>
			<div class="field">
				<label for="f-reading">Reading</label>
				<select id="f-reading" bind:value={readingFilter}>
					<option value="all">All</option>
					{#each b.readings as reading (reading)}
						<option value={reading}>{reading}</option>
					{/each}
				</select>
			</div>
			<p class="count" role="status">{rows.length} cases</p>
		</div>

		{#if rows.length === 0}
			<div class="empty">
				<p class="empty-title">No cases match these filters.</p>
				<p>Widen the truth or reading filter.</p>
			</div>
		{:else}
			<div class="table-scroll">
				<table class="record">
					<caption class="sr-only">Every benchmark case on the selected path.</caption>
					<thead>
						<tr>
							<th scope="col">Case</th>
							<th scope="col">Trigger</th>
							<th scope="col">Truth</th>
							<th scope="col">Reading</th>
							<th scope="col">Decision</th>
							<th scope="col">Score</th>
							<th scope="col" class="n">Confidence</th>
							<th scope="col">Notes</th>
						</tr>
					</thead>
					<tbody>
						{#each rows as row (row.case_id)}
							<tr>
								<th scope="row" class="ident">{row.case_id}</th>
								<td class="ident">{row.trigger}</td>
								<td>
									<span class="truth" data-truth={row.truth}>{row.truth}</span>
								</td>
								<td class="reading-cell">{readingOf(row)}</td>
								<td class="decision">{actionLabel(row.action)}</td>
								<td>
									{#if row.score && isScored(row)}
										<span
											class="score"
											data-tone={SCORE_TONE[row.score] ?? 'deferred'}
											title={SCORE_NOTE[row.score] ?? ''}
										>
											<span class="sw" data-tone={SCORE_TONE[row.score] ?? 'deferred'}></span>
											{row.score}
										</span>
									{:else if !isScored(row)}
										<span class="none" title={NOT_SCORED_NOTE}>not scored</span>
									{:else}
										<span class="none">—</span>
									{/if}
								</td>
								<td class="n mono">{formatNumber(row.confidence)}</td>
								<td class="notes">
									{#if row.degraded}
										<DegradedMark />
									{/if}
									{#if row.disputed.length > 0}
										<span class="chip">disputed: {row.disputed.join(', ')}</span>
									{/if}
									{#if row.disputes_refused.length > 0}
										<span class="chip">dispute refused: {row.disputes_refused.join(', ')}</span>
									{/if}
									{#if row.opened_by_router.length > 0}
										<span class="chip">router opened: {row.opened_by_router.join(', ')}</span>
									{/if}
									{#if row.defence_excluded === false}
										<span class="chip stood">defence stood</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="fine">
				On a crew path a degraded case carries no score at all — neither a pass nor a fail. The
				rules decided it, so it is not the crew’s result, and it is left out of the totals above
				exactly as it is left out here. On the rules path every case is decided that way, so every
				case is scored.
			</p>
		{/if}
	</section>
</div>

<style>
	.hero {
		background: var(--surface);
		border-bottom: 1px solid var(--rule-strong);
	}

	.hero-in {
		padding-top: 30px;
		padding-bottom: 24px;
	}

	.intro {
		margin-bottom: 0;
	}

	h1 {
		font-size: clamp(30px, 2.9vw, 42px);
		letter-spacing: -0.028em;
		margin: 6px 0 10px;
	}

	.lede {
		color: var(--ink-soft);
		font-size: 15px;
		max-width: 88ch;
	}

	.caveat {
		font-size: 12px;
		color: var(--ink-muted);
		max-width: 92ch;
		border-left: 2px solid var(--rule-strong);
		padding-left: 11px;
		margin-top: 14px;
		line-height: 1.5;
	}

	section {
		margin-top: 38px;
	}

	section:first-of-type {
		margin-top: 28px;
	}

	h2 {
		font-size: 21px;
		letter-spacing: -0.022em;
		padding-bottom: 9px;
		border-bottom: 1px solid var(--rule-strong);
		margin-bottom: 15px;
	}

	.section-note {
		font-size: 13.5px;
		color: var(--ink-soft);
		max-width: 80ch;
		margin-bottom: 16px;
	}

	/* ── scoring key ──────────────────────────────────────────────── */

	.scoring-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(270px, 1fr));
		gap: 22px;
		border: 1px solid var(--rule);
		background: var(--surface);
		border-radius: 2px;
		padding: 14px 18px 16px;
	}

	.truth-label {
		font-family: var(--mono);
		font-size: 10.5px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-muted);
		margin: 0 0 7px;
	}

	.score-key {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 5px;
		font-size: 13px;
	}

	.score-key li {
		display: flex;
		align-items: baseline;
		gap: 8px;
		color: var(--ink-soft);
	}

	.sw {
		width: 11px;
		height: 11px;
		border-radius: 1px;
		flex: none;
		background: var(--fill, var(--surface-3));
		align-self: center;
	}

	.sw[data-tone='correct'] {
		--fill: var(--outcome-correct);
	}
	.sw[data-tone='deferred'] {
		--fill: var(--outcome-deferred);
	}
	.sw[data-tone='wrong'] {
		--fill: var(--outcome-wrong);
	}

	.sw.hatched {
		background-image: repeating-linear-gradient(
			45deg,
			rgba(0, 0, 0, 0.32) 0 2px,
			transparent 2px 6px
		);
	}

	.scoring-note {
		margin: 12px 0 0;
		font-size: 12.5px;
		color: var(--ink-muted);
		max-width: 86ch;
	}

	/* ── series ───────────────────────────────────────────────────── */

	.series-stack {
		display: grid;
		gap: 14px;
	}

	.series {
		border: 1px solid var(--rule);
		background: var(--surface);
		border-radius: 2px;
		padding: 14px 16px 16px;
	}

	.series header {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 16px;
		flex-wrap: wrap;
		padding-bottom: 10px;
		border-bottom: 1px solid var(--rule-soft);
		margin-bottom: 12px;
	}

	.series h3 {
		font-size: 14.5px;
	}

	.scored {
		margin: 0;
		font-size: 12px;
		color: var(--ink-muted);
		display: flex;
		align-items: center;
		gap: 6px;
	}

	.halves {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
		gap: 20px;
	}

	.crew {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
		gap: 0;
		margin: 14px 0 0;
		padding-top: 11px;
		border-top: 1px solid var(--rule-soft);
	}

	.crew > div {
		padding: 4px 10px 4px 0;
	}

	.crew dt {
		font-size: 10.5px;
		white-space: nowrap;
		font-family: var(--mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	.crew dd {
		margin: 1px 0 0;
		font-size: 14px;
		font-weight: 500;
	}

	/* ── by reading ───────────────────────────────────────────────── */

	.reading-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
		gap: 14px;
	}

	.reading {
		border: 1px solid var(--rule);
		background: var(--surface);
		border-radius: 2px;
		padding: 12px 14px 13px;
	}

	.reading h3 {
		font-size: 13px;
		font-family: var(--mono);
		letter-spacing: 0.07em;
		text-transform: uppercase;
		padding-bottom: 7px;
		border-bottom: 1px solid var(--rule-soft);
		margin-bottom: 9px;
	}

	.reading-row {
		padding: 7px 0;
		border-bottom: 1px solid var(--rule-soft);
	}

	.reading-row:last-child {
		border-bottom: none;
	}

	.series-name {
		margin: 0 0 5px;
		font-size: 11.5px;
		color: var(--ink-muted);
	}

	.reading-halves {
		display: grid;
		gap: 7px;
	}

	.mini-label {
		font-size: 10.5px;
		font-family: var(--mono);
		color: var(--ink-muted);
		display: block;
		margin-bottom: 2px;
	}

	/* ── case table ───────────────────────────────────────────────── */

	.filters {
		display: flex;
		align-items: flex-end;
		gap: 16px;
		flex-wrap: wrap;
		padding: 0 0 12px;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}

	.field label {
		font-family: var(--mono);
		font-size: 10.5px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	select {
		font: inherit;
		font-size: 13px;
		padding: 4px 7px;
		background: var(--surface);
		color: var(--ink);
		border: 1px solid var(--rule-strong);
		border-radius: 2px;
		min-width: 150px;
		max-width: 320px;
	}

	.count {
		margin: 0 0 4px auto;
		font-size: 12.5px;
		color: var(--ink-muted);
	}

	.table-scroll {
		overflow-x: auto;
		border: 1px solid var(--rule);
		background: var(--surface);
	}

	.truth {
		font-family: var(--mono);
		font-size: 11px;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		border: 1px solid var(--rule-strong);
		border-radius: 2px;
		padding: 0 5px;
	}

	.truth[data-truth='fraud'] {
		border-color: var(--ink-muted);
		font-weight: 500;
	}

	.reading-cell {
		color: var(--ink-soft);
		white-space: nowrap;
	}

	.decision {
		white-space: nowrap;
	}

	.score {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-weight: 500;
		font-size: 12.5px;
		white-space: nowrap;
	}

	.notes {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
		max-width: 34ch;
	}

	.chip {
		border: 1px solid var(--rule);
		border-radius: 2px;
		padding: 0 5px;
		font-size: 10.5px;
		color: var(--ink-soft);
		background: var(--surface-2);
	}

	.chip.stood {
		border-color: var(--deferred);
		color: var(--deferred);
	}

	.none {
		color: var(--ink-faint);
	}

	.empty {
		border: 1px dashed var(--rule-strong);
		border-radius: 2px;
		padding: 36px 24px;
		text-align: center;
		background: var(--surface);
	}

	.empty-title {
		font-weight: 600;
		margin: 0 auto 5px;
	}

	.empty p {
		color: var(--ink-muted);
		font-size: 13.5px;
		margin-left: auto;
		margin-right: auto;
	}

	.fine {
		margin-top: 10px;
		font-size: 11.5px;
		color: var(--ink-muted);
	}

	@media (max-width: 720px) {
		.hero-in {
			padding-top: 20px;
			padding-bottom: 18px;
		}
		.count {
			margin-left: 0;
		}
	}
</style>
