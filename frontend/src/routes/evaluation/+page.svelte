<script lang="ts">
	import BarRow from '$lib/components/BarRow.svelte';
	import IndiaMap from '$lib/components/IndiaMap.svelte';
	import SweepChart from '$lib/components/SweepChart.svelte';
	import { formatInt, formatNumber, formatPercent } from '$lib/domain';
	import { india, makeScale, numeric, text } from '$lib/map';
	import { DISTANCE_MATERIAL_KM } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const e = $derived(data.evaluation);
	const s = $derived(e.summary);

	// ── the geography of the Committee's workload ────────────────────────────
	// `by_state` is the ten states carrying the most escalations, already sorted by the exporter.
	// Both columns are worth seeing and they rank differently: the biggest states send the most
	// cases, the thinnest networks send the largest share of their own.
	const metrics = [
		{
			key: 'sec_escalations_per_year',
			label: 'Escalations a year',
			measure: 'escalations a year',
			blurb:
				'How many hospitals a year the gate would send to that state’s Committee rather than suspend.',
			format: (value: number) => formatInt(value)
		},
		{
			key: 'share_of_egregious_escalated',
			label: 'Share of the state’s own',
			measure: 'of the state’s egregious findings',
			blurb:
				'Of everything confirmed egregious in that state, the fraction the gate diverts. This is a property of the network, not of its size.',
			format: (value: number) => formatPercent(value)
		}
	] as const;

	let metric = $state<(typeof metrics)[number]['key']>('sec_escalations_per_year');
	const chosen = $derived(metrics.find((entry) => entry.key === metric) ?? metrics[0]);

	const stateRows = $derived(
		e.by_state
			.map((row) => ({ state: text(row, 'state'), value: numeric(row, metric) }))
			.filter((row) => row.state !== '' && Number.isFinite(row.value))
			.sort((a, b) => b.value - a.value)
	);
	const stateValues = $derived(new Map(stateRows.map((row) => [row.state, row.value])));
	const stateScale = $derived(makeScale(stateRows.map((row) => row.value)));
	const stateMax = $derived(Math.max(...stateRows.map((row) => row.value), 0));
	let pickedState = $state<string | null>(null);

	const num = (row: Record<string, unknown>, key: string): number => {
		const value = row[key];
		return typeof value === 'number' ? value : Number.NaN;
	};

	const sweepXs = $derived(e.threshold_sweep.map((row) => num(row, 'km')));
	const sweepSeries = $derived([
		{
			key: 'escalated',
			label: 'Hospitals escalated',
			tone: 'series-1' as const,
			values: e.threshold_sweep.map((row) => num(row, 'hospitals_escalated'))
		},
		{
			key: 'removing',
			// ` / ` is the wrap point for the chart's end label; the readout prints it as one line.
			label: 'Suspended, removing / an only provider',
			tone: 'series-2' as const,
			values: e.threshold_sweep.map((row) => num(row, 'hospitals_suspended_removing_only_provider'))
		}
	]);

	// ── moving the threshold ─────────────────────────────────────────────────
	// The sweep was run at every 5 km from 0 to 200 and the answers are all in the export, so the
	// slider is reading a row, never interpolating and never recomputing. That is the whole reason
	// it can be offered at all: the viewer decides nothing, and this is no exception.
	// The slider starts where the policy stands. Taken from the generated constant rather than from
	// `data`, which is a prop and would only be read once here anyway; `export-contract.test.ts`
	// asserts the two agree, so this cannot start anywhere the run did not actually operate.
	let km = $state<number>(DISTANCE_MATERIAL_KM);

	const inUseKm = $derived(e.distance_material_km);
	const rowAt = (target: number) =>
		e.threshold_sweep.find((row) => num(row, 'km') === target) ?? null;
	const atKm = $derived(rowAt(km));
	const atInUse = $derived(rowAt(inUseKm));
	const kmBounds = $derived({
		min: Math.min(...sweepXs),
		max: Math.max(...sweepXs),
		step: sweepXs.length > 1 ? (sweepXs[1] ?? 5) - (sweepXs[0] ?? 0) : 5
	});

	const readouts = [
		{
			key: 'hospitals_escalated',
			label: 'Hospitals escalated',
			note: 'sent to the Committee rather than suspended',
			decimals: 0
		},
		{
			key: 'hospitals_suspended_removing_only_provider',
			label: 'Suspended anyway, removing an only provider',
			note: 'the access this threshold does not protect',
			decimals: 0
		},
		{
			key: 'districts_losing_a_specialty',
			label: 'Districts losing a specialty',
			note: 'left with no empanelled provider of it',
			decimals: 0
		},
		{
			key: 'population_M_of_those_districts',
			label: 'People in those districts',
			note: 'millions',
			decimals: 1
		}
	] as const;

	const show = (value: number, decimals: number) =>
		decimals === 0 ? formatInt(value) : formatNumber(value, decimals);

	const ablationMax = $derived(
		Math.max(...e.ablation.map((row) => num(row, 'wrongful_suspensions_per_year')), 1)
	);

	const sensitivityColumns = [
		['auto_resolution', 'Auto-resolved'],
		['to_human', 'To a human'],
		['innocent_show_cause', 'Innocent: notice'],
		['innocent_suspended', 'Innocent: suspended'],
		['fraud_enforced', 'Fraud enforced'],
		['fraud_released', 'Fraud released']
	] as const;

	const actionRows = $derived(Object.entries(s.final_actions ?? {}));
</script>

<svelte:head>
	<title>Evaluation — The Access Gate</title>
</svelte:head>

<div class="hero">
	<div class="wrap hero-in">
		<header class="intro">
			<p class="eyebrow">Evaluation · {formatInt(s.flagged_cases)} flagged cases</p>
			<h1>How well it works</h1>
			<p class="lede">
				What happens when flags arrive in realistic proportions, what that means at national scale,
				and how much of it rests on assumptions.
			</p>
			<p class="caveat top">{e.caveats.corpus}</p>
			<p class="caveat">{e.caveats.not_agents}</p>
		</header>
	</div>
</div>

<div class="wrap">
	<section aria-labelledby="headline-h" class="first">
		<h2 id="headline-h">Headline</h2>
		<div class="tiles">
			<div class="tile">
				<p class="tile-value num">{formatPercent(s.auto_resolution_share)}</p>
				<p class="tile-label">Auto-resolution</p>
				<p class="tile-note">flags that reached a final action with no human</p>
			</div>
			<div class="tile">
				<p class="tile-value num">{formatPercent(s.innocent?.suspended, 2)}</p>
				<p class="tile-label">Innocent flags suspended</p>
				<p class="tile-note">
					{formatInt(s.per_year?.wrongful_suspensions)} a year at national scale
				</p>
			</div>
			<div class="tile">
				<p class="tile-value num">{formatPercent(s.fraud?.enforced)}</p>
				<p class="tile-label">Frauds enforced against</p>
				<p class="tile-note">notice, suspension, escalation or de-listing referral</p>
			</div>
			<!--
				Both states the gate diverts on, which is the figure the README and docs/09 headline. `protect`
				escalates to the Committee; `phantom` refers the specialty for de-listing. Showing `protect` alone
				here read as 10.8% beside a published 10.9% and invited the reader to reconcile two right numbers.
			-->
			<div class="tile emph">
				<p class="tile-value num">
					{formatPercent((e.gate_at_chosen_km?.protect ?? 0) + (e.gate_at_chosen_km?.phantom ?? 0))}
				</p>
				<p class="tile-label">Diverted by the gate</p>
				<p class="tile-note">
					of confirmed egregious findings: {formatPercent(e.gate_at_chosen_km?.protect)} escalated to
					the Committee, {formatPercent(e.gate_at_chosen_km?.phantom, 2)} referred for de-listing, at
					{formatNumber(e.distance_material_km, 0)} km
				</p>
			</div>
		</div>

		<div class="split">
			<div>
				<h3>Where flags end up</h3>
				<table class="record">
					<tbody>
						<tr>
							<th scope="row">Resolved at the desk, no field work</th>
							<td class="n">{formatPercent(s.resolved_at_desk_share)}</td>
						</tr>
						<tr>
							<th scope="row">Needed a field audit first</th>
							<td class="n">{formatPercent(s.field_audit_share)}</td>
						</tr>
						<tr>
							<th scope="row">Went to a human</th>
							<td class="n">{formatPercent(s.human_share)}</td>
						</tr>
						<tr>
							<th scope="row">Fraud share of flags</th>
							<td class="n">{formatPercent(s.fraud_share_of_flags)}</td>
						</tr>
					</tbody>
				</table>
			</div>

			<div>
				<h3>Final actions</h3>
				<table class="record">
					<tbody>
						{#each actionRows as [action, share] (action)}
							<tr>
								<th scope="row"><span class="ident">{action}</span></th>
								<td class="n">{formatPercent(share)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>

			<div>
				<h3>At national scale, per year</h3>
				<table class="record">
					<tbody>
						{#each Object.entries(s.per_year ?? {}) as [key, value] (key)}
							<tr>
								<th scope="row"><span class="ident">{key}</span></th>
								<td class="n">{formatInt(value)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	</section>

	<section aria-labelledby="sweep-h">
		<h2 id="sweep-h">The distance threshold</h2>
		<p class="section-note">
			<code>DISTANCE_MATERIAL_KM</code> decides when a sole provider is protected: protected if its nearest
			alternative is at least this far away. At 0 km every sole provider is protected; raising the threshold
			protects fewer.
		</p>

		{#if sweepXs.length === 0}
			<p class="void">No threshold sweep is in the exported record.</p>
		{:else}
			<SweepChart
				xs={sweepXs}
				series={sweepSeries}
				xLabel="distance threshold (km)"
				yLabel="Hospitals"
				marker={{ x: e.distance_material_km, label: `${e.distance_material_km} km — in use` }}
			/>

			<div class="scrub">
				<div class="scrub-head">
					<label for="km-slider">Move the threshold</label>
					<output for="km-slider" class="scrub-km num">{formatInt(km)} km</output>
					{#if km !== inUseKm}
						<button type="button" class="link" onclick={() => (km = inUseKm)}>
							back to the {formatInt(inUseKm)} km in use
						</button>
					{:else}
						<span class="scrub-tag">the threshold in use</span>
					{/if}
				</div>

				<input
					id="km-slider"
					type="range"
					min={kmBounds.min}
					max={kmBounds.max}
					step={kmBounds.step}
					bind:value={km}
					aria-describedby="scrub-readout"
				/>

				{#if atKm}
					<div class="readouts" id="scrub-readout">
						{#each readouts as readout (readout.key)}
							{@const value = num(atKm, readout.key)}
							{@const base = atInUse ? num(atInUse, readout.key) : Number.NaN}
							{@const delta = value - base}
							<div class="readout">
								<p class="readout-value num">{show(value, readout.decimals)}</p>
								<p class="readout-label">{readout.label}</p>
								<p class="readout-note">{readout.note}</p>
								{#if Number.isFinite(delta) && Math.abs(delta) > 0.0001}
									<p class="readout-delta">
										{delta > 0 ? '+' : '−'}{show(Math.abs(delta), readout.decimals)}
										<span>vs {formatInt(inUseKm)} km</span>
									</p>
								{:else}
									<p class="readout-delta flat">unchanged</p>
								{/if}
							</div>
						{/each}
					</div>
				{:else}
					<p class="void">The sweep has no row at {formatInt(km)} km.</p>
				{/if}

				<p class="scrub-note">
					Raising the threshold protects fewer hospitals, so the Committee sees fewer cases and more
					suspensions go through — and every one of those that takes a district's last provider of a
					specialty is counted in the third figure. There is no setting that makes both columns
					small; the 50 km in use is where the project chose to stand, not a value the data picks
					out.
				</p>
			</div>

			<details class="data-table">
				<summary>The same numbers as a table ({e.threshold_sweep.length} rows)</summary>
				<div class="table-scroll">
					<table class="record">
						<thead>
							<tr>
								<th scope="col" class="n">km</th>
								<th scope="col" class="n">Hospitals escalated</th>
								<th scope="col" class="n">Suspended, removing an only provider</th>
								<th scope="col" class="n">Cells losing their only provider</th>
								<th scope="col" class="n">Districts losing a specialty</th>
								<th scope="col" class="n">Population of those districts (M)</th>
								<th scope="col" class="n">SEC escalations / year</th>
							</tr>
						</thead>
						<tbody>
							{#each e.threshold_sweep as row, i (i)}
								<tr class:marked={num(row, 'km') === e.distance_material_km}>
									<th scope="row" class="n mono">{formatInt(num(row, 'km'))}</th>
									<td class="n">{formatInt(num(row, 'hospitals_escalated'))}</td>
									<td class="n"
										>{formatInt(num(row, 'hospitals_suspended_removing_only_provider'))}</td
									>
									<td class="n">{formatInt(num(row, 'cells_losing_only_provider'))}</td>
									<td class="n">{formatInt(num(row, 'districts_losing_a_specialty'))}</td>
									<td class="n">{formatNumber(num(row, 'population_M_of_those_districts'), 1)}</td>
									<td class="n">{formatInt(num(row, 'sec_escalations_per_year'))}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</details>

			<p class="caveat">{e.caveats.threshold}</p>
		{/if}
	</section>

	<section aria-labelledby="geo-h" class="geography">
		<h2 id="geo-h">Where the load falls</h2>
		<p class="section-note">
			An escalation is work for a State Empanelment Committee, and the gate does not spread that
			work evenly. Select a state on either side to hold it.
		</p>

		{#if stateRows.length === 0}
			<p class="void">No state breakdown is in the exported record.</p>
		{:else}
			<div class="switch" role="group" aria-label="Measure to map">
				{#each metrics as entry (entry.key)}
					<button
						type="button"
						class:on={metric === entry.key}
						aria-pressed={metric === entry.key}
						onclick={() => (metric = entry.key)}
					>
						{entry.label}
					</button>
				{/each}
			</div>
			<p class="switch-note">{chosen.blurb}</p>

			<div class="geo">
				<div class="geo-map">
					<IndiaMap
						label="India, shaded by {chosen.measure}. Ten states carry a figure; the rest are hatched."
						values={stateValues}
						scale={stateScale}
						format={chosen.format}
						measure={chosen.measure}
						selected={pickedState}
						onselect={(name) => (pickedState = name)}
						offLabel="outside the top ten"
					/>
				</div>

				<ol class="rank">
					{#each stateRows as row, i (row.state)}
						<li>
							<button
								type="button"
								class:on={pickedState === row.state}
								aria-pressed={pickedState === row.state}
								onclick={() => (pickedState = pickedState === row.state ? null : row.state)}
							>
								<span class="rank-n mono">{i + 1}</span>
								<span class="rank-name">{row.state}</span>
								<span class="rank-track" aria-hidden="true">
									<span
										class="rank-fill"
										style="width: {stateMax > 0
											? (row.value / stateMax) * 100
											: 0}%; background: var(--choro-{stateScale.bin(row.value) + 1})"
									></span>
								</span>
								<span class="rank-value num">{chosen.format(row.value)}</span>
							</button>
						</li>
					{/each}
				</ol>
			</div>

			<p class="caveat">
				The export carries the ten states with the heaviest Committee load, not a ranking of every
				state: the evaluation itself covers the whole registry. Boundaries are DataMeet's ({india._source.licence
					.split('—')[0]
					?.trim()}), digitised from the Survey of India state map, and show the full extent India
				claims. They predate the 2019 reorganisation, so Jammu &amp; Kashmir appears as one state
				and Ladakh is not drawn separately.
			</p>
		{/if}
	</section>

	<section aria-labelledby="ablation-h">
		<h2 id="ablation-h">What each safeguard bought</h2>
		<p class="section-note">
			The same corpus, run as the system was first built and then with each safeguard added.
			Wrongful suspensions per year:
		</p>

		{#if e.ablation.length === 0}
			<p class="void">No ablation is in the exported record.</p>
		{:else}
			<div class="bars">
				{#each e.ablation as row, i (i)}
					<BarRow
						label={String(row['variant'] ?? '—')}
						value={num(row, 'wrongful_suspensions_per_year')}
						max={ablationMax}
						tone={i === e.ablation.length - 1 ? 'series-1' : 'series-2'}
						note={`auto-resolution ${formatPercent(num(row, 'auto_resolution'))} · innocent suspended ${formatPercent(num(row, 'innocent_suspended'), 2)} · fraud enforced ${formatPercent(num(row, 'fraud_enforced'))}`}
					/>
				{/each}
			</div>
			<p class="caveat">{e.caveats.ablation}</p>
		{/if}
	</section>

	<section aria-labelledby="sens-h">
		<h2 id="sens-h">Sensitivity</h2>
		<p class="section-note">{e.caveats.sensitivity}</p>
		<div class="table-scroll">
			<table class="record">
				<thead>
					<tr>
						<th scope="col">Assumption</th>
						<th scope="col" class="n">Value</th>
						{#each sensitivityColumns as [, label] (label)}
							<th scope="col" class="n">{label}</th>
						{/each}
						<th scope="col" class="n">Wrongful susp. / yr</th>
					</tr>
				</thead>
				<tbody>
					{#each e.sensitivity as row, i (i)}
						<tr class:baseline={row['parameter'] === 'baseline'}>
							<th scope="row"><span class="ident">{String(row['parameter'] ?? '—')}</span></th>
							<td class="n mono">{row['value'] === null ? '—' : String(row['value'])}</td>
							{#each sensitivityColumns as [key] (key)}
								<td class="n">{formatPercent(num(row, key), key.includes('suspended') ? 2 : 1)}</td>
							{/each}
							<td class="n">{formatInt(num(row, 'wrongful_suspensions_per_year'))}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>

	<section aria-labelledby="trigger-h">
		<h2 id="trigger-h">By trigger</h2>
		<div class="table-scroll">
			<table class="record">
				<thead>
					<tr>
						<th scope="col">Trigger</th>
						<th scope="col" class="n">Cases</th>
						<th scope="col" class="n">Fraud share</th>
						<th scope="col" class="n">Field audited</th>
						<th scope="col" class="n">Released</th>
						<th scope="col" class="n">Show cause</th>
						<th scope="col" class="n">Suspended</th>
						<th scope="col" class="n">Escalated</th>
						<th scope="col" class="n">To a human</th>
					</tr>
				</thead>
				<tbody>
					{#each e.by_trigger as row, i (i)}
						<tr>
							<th scope="row"><span class="ident">{String(row['trigger'] ?? '—')}</span></th>
							<td class="n">{formatInt(num(row, 'cases'))}</td>
							<td class="n">{formatPercent(num(row, 'fraud_share'))}</td>
							<td class="n">{formatPercent(num(row, 'field_audited'))}</td>
							<td class="n">{formatPercent(num(row, 'release_claim'))}</td>
							<td class="n">{formatPercent(num(row, 'show_cause_notice'))}</td>
							<td class="n">{formatPercent(num(row, 'suspend_hospital'))}</td>
							<td class="n">{formatPercent(num(row, 'escalate_to_sec'))}</td>
							<td class="n">{formatPercent(num(row, 'no_action_review'))}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>

	{#if e.disparate_impact.length > 0}
		<section aria-labelledby="di-h">
			<h2 id="di-h">By hospital type</h2>
			<p class="section-note">{e.caveats.disparate_impact}</p>
			<div class="table-scroll">
				<table class="record">
					<thead>
						<tr>
							<th scope="col">Hospital type</th>
							<th scope="col" class="n">Network</th>
							<th scope="col" class="n">Sole provider</th>
							<th scope="col" class="n">Cases</th>
							<th scope="col" class="n">Auto-resolved</th>
							<th scope="col" class="n">Suspended</th>
							<th scope="col" class="n">Innocent adverse</th>
							<th scope="col" class="n">Egregious escalated</th>
						</tr>
					</thead>
					<tbody>
						{#each e.disparate_impact as row, i (i)}
							<tr>
								<th scope="row">{String(row['hospital_type'] ?? '—')}</th>
								<td class="n">{formatNumber(num(row, 'network_pct'), 1)}%</td>
								<td class="n">{formatNumber(num(row, 'sole_provider_pct'), 1)}%</td>
								<td class="n">{formatInt(num(row, 'cases'))}</td>
								<td class="n">{formatPercent(num(row, 'auto_resolution'))}</td>
								<td class="n">{formatPercent(num(row, 'suspended'), 2)}</td>
								<td class="n">{formatPercent(num(row, 'innocent_adverse'))}</td>
								<td class="n">{formatPercent(num(row, 'egregious_escalated_exact'))}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}

	<section aria-labelledby="limits-h" class="limits">
		<h2 id="limits-h">Known limitations</h2>
		<ol>
			{#each e.limitations as limitation (limitation)}
				<li>{limitation}</li>
			{/each}
		</ol>
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

	section {
		margin-top: 44px;
	}

	section.first {
		margin-top: 30px;
	}

	h2 {
		font-size: 21px;
		letter-spacing: -0.022em;
		padding-bottom: 9px;
		border-bottom: 1px solid var(--rule-strong);
		margin-bottom: 15px;
	}

	h3 {
		font-size: 13px;
		font-family: var(--mono);
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--ink-muted);
		font-weight: 500;
		margin-bottom: 7px;
	}

	.section-note {
		font-size: 13.5px;
		color: var(--ink-soft);
		max-width: 78ch;
		margin-bottom: 14px;
	}

	/* Every chart carries the caveat from the source doc, whether or not it is asked for. */
	.caveat {
		font-size: 12px;
		color: var(--ink-muted);
		max-width: 88ch;
		border-left: 2px solid var(--rule-strong);
		padding-left: 11px;
		margin-top: 12px;
		line-height: 1.5;
	}

	.caveat.top {
		margin-top: 14px;
	}

	/* ── moving the threshold ────────────────────────────────────────────── */

	/* The one control on the page, so it is allowed to look like one: its own panel, an accent edge
	   and the numbers it moves set large directly underneath. */
	.scrub {
		margin: 20px 0 0;
		padding: 18px 20px 16px;
		border: 1px solid var(--rule-strong);
		border-top: 3px solid var(--accent);
		border-radius: 3px;
		background: var(--surface);
		box-shadow: var(--lift-2);
	}

	.scrub-head {
		display: flex;
		align-items: baseline;
		gap: 12px;
		flex-wrap: wrap;
		margin-bottom: 8px;
	}

	.scrub-head label {
		font-family: var(--mono);
		font-size: 10.5px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	.scrub-km {
		font-size: 22px;
		font-weight: 600;
		letter-spacing: -0.02em;
		margin-right: auto;
	}

	.scrub-tag {
		font-size: 11.5px;
		color: var(--gate-protect);
		background: var(--gate-protect-wash);
		border: 1px solid var(--gate-protect-edge);
		border-radius: 9px;
		padding: 1px 9px;
	}

	.scrub input[type='range'] {
		width: 100%;
		accent-color: var(--series-1);
		margin: 0 0 14px;
	}

	.readouts {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 0;
		border-top: 1px solid var(--rule);
	}

	.readout {
		padding: 11px 14px 11px 0;
		border-right: 1px solid var(--rule-soft);
		min-width: 0;
	}

	.readout:last-child {
		border-right: none;
	}

	.readout p {
		margin: 0;
		max-width: none;
	}

	.readout-value {
		font-size: 25px;
		font-weight: 600;
		letter-spacing: -0.025em;
		line-height: 1.1;
	}

	.readout-label {
		font-size: 12.5px;
		font-weight: 500;
		margin-top: 2px;
		line-height: 1.3;
	}

	.readout-note {
		font-size: 11px;
		color: var(--ink-muted);
		line-height: 1.35;
	}

	.readout-delta {
		margin-top: 5px;
		font-size: 11.5px;
		font-variant-numeric: tabular-nums;
		color: var(--ink-soft);
	}

	.readout-delta span {
		color: var(--ink-faint);
	}

	.readout-delta.flat {
		color: var(--ink-faint);
	}

	.scrub-note {
		font-size: 12px;
		color: var(--ink-muted);
		max-width: 88ch;
		margin: 12px 0 0;
		line-height: 1.5;
	}

	/* ── where the load falls ────────────────────────────────────────────── */

	.switch {
		display: inline-flex;
		border: 1px solid var(--rule-strong);
		border-radius: 2px;
		overflow: hidden;
		background: var(--surface);
	}

	.switch button {
		font: inherit;
		font-size: 12.5px;
		padding: 5px 13px;
		background: none;
		border: none;
		border-left: 1px solid var(--rule);
		color: var(--ink-soft);
		cursor: pointer;
	}

	.switch button:first-child {
		border-left: none;
	}

	.switch button:hover {
		background: var(--surface-2);
		color: var(--ink);
	}

	.switch button.on {
		background: var(--ink);
		color: var(--paper);
		font-weight: 500;
	}

	.switch-note {
		font-size: 12.5px;
		color: var(--ink-muted);
		margin: 9px 0 16px;
		max-width: 78ch;
	}

	.geo {
		display: grid;
		/* A ceiling, not a share: past about 520px the map only gets taller than the ranking beside
		   it, and the panel ends in a column of empty paper. */
		grid-template-columns: minmax(0, 520px) minmax(0, 1fr);
		gap: 40px;
		align-items: center;
	}

	/* The map is the slower read of the two, so it gets the room; the ranking beside it is what a
	   reader checks a figure against. */
	.geo-map {
		width: 100%;
		max-width: 520px;
		margin: 0 auto;
	}

	.rank {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 2px;
	}

	.rank button {
		width: 100%;
		display: grid;
		grid-template-columns: 18px minmax(80px, auto) minmax(40px, 1fr) auto;
		align-items: center;
		gap: 10px;
		padding: 6px 8px;
		font: inherit;
		font-size: 13px;
		text-align: left;
		background: none;
		border: 1px solid transparent;
		border-radius: 2px;
		color: inherit;
		cursor: pointer;
	}

	.rank button:hover {
		background: var(--surface-2);
	}

	.rank button.on {
		border-color: var(--rule-strong);
		background: var(--surface);
	}

	.rank-n {
		font-size: 11px;
		color: var(--ink-faint);
	}

	.rank-name {
		white-space: nowrap;
	}

	.rank-track {
		height: 9px;
		background: var(--surface-2);
		border: 1px solid var(--rule-soft);
		display: block;
	}

	.rank-fill {
		display: block;
		height: 100%;
		transition: width 200ms ease;
	}

	.rank-value {
		font-size: 12.5px;
		font-variant-numeric: tabular-nums;
		text-align: right;
		min-width: 52px;
	}

	@media (max-width: 1200px) {
		.geo {
			grid-template-columns: minmax(0, 1fr);
			gap: 20px;
		}

		.geo-map {
			max-width: 430px;
		}
	}

	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		border: 1px solid var(--rule);
		background: var(--surface);
		border-radius: 3px;
		box-shadow: var(--lift);
		overflow: hidden;
	}

	.tile {
		padding: 16px 18px 17px;
		border-left: 1px solid var(--rule-soft);
	}

	.tile:first-child {
		border-left: none;
	}

	.tile.emph {
		background: var(--gate-protect-wash);
		border-left-color: var(--gate-protect-edge);
	}

	.tile-value {
		margin: 0;
		font-size: 36px;
		font-weight: 600;
		letter-spacing: -0.035em;
		line-height: 1.02;
	}

	.tile-label {
		margin: 3px 0 0;
		font-size: 13px;
		font-weight: 500;
	}

	.tile-note {
		margin: 2px 0 0;
		font-size: 11.5px;
		color: var(--ink-muted);
		line-height: 1.35;
	}

	.split {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
		gap: 26px;
		margin-top: 22px;
	}

	.split table {
		font-size: 13px;
	}

	.table-scroll {
		overflow-x: auto;
		border: 1px solid var(--rule);
		background: var(--surface);
	}

	.data-table {
		margin-top: 12px;
	}

	.data-table summary {
		cursor: pointer;
		font-size: 12.5px;
		color: var(--ink-soft);
		padding: 6px 0;
	}

	tr.marked,
	tr.baseline {
		background: var(--surface-2);
		font-weight: 500;
	}

	.bars {
		border-top: 1px solid var(--rule);
	}

	.void {
		border: 1px dashed var(--rule-strong);
		background: var(--surface-2);
		padding: 14px 16px;
		color: var(--ink-soft);
		font-size: 13.5px;
	}

	.limits ol {
		padding-left: 20px;
		font-size: 13.5px;
		max-width: 84ch;
		color: var(--ink-soft);
	}

	.limits li {
		margin-bottom: 6px;
	}

	@media (max-width: 720px) {
		.hero-in {
			padding-top: 20px;
			padding-bottom: 18px;
		}
	}
</style>
