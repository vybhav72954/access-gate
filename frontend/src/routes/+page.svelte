<script lang="ts">
	import { base } from '$app/paths';
	import ActionBadge from '$lib/components/ActionBadge.svelte';
	import Confidence from '$lib/components/Confidence.svelte';
	import GateBadge from '$lib/components/GateBadge.svelte';
	import DegradedMark from '$lib/components/DegradedMark.svelte';
	import IndiaMap from '$lib/components/IndiaMap.svelte';
	import {
		arrangeCases,
		districtsPresent,
		filterIsActive,
		formatTimestamp,
		GATES,
		summarise,
		triggersPresent,
		type CaseSort
	} from '$lib/domain';
	import type { MapPin } from '$lib/map';
	import type { ActionGroup, GateState } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Sort = CaseSort;
	let group = $state<ActionGroup | 'all'>('all');
	let gate = $state<GateState | 'all' | 'none'>('all');
	let trigger = $state<string>('all');
	let district = $state<string>('all');
	let degradedOnly = $state(false);
	let sort = $state<Sort>('claim');
	let descending = $state(false);

	const triggers = $derived(triggersPresent(data.cases));
	const filter = $derived({ group, gate, trigger, district, degradedOnly });
	const filtered = $derived(arrangeCases(data.cases, filter, sort, descending));

	// ── the map ──────────────────────────────────────────────────────────────
	// Districts, not hospitals. The gate asks what a district would be left with, so the district is
	// the honest unit; and a pin on a hospital would undo the pseudonymity the rest of the app keeps.
	const districts = $derived(districtsPresent(data.cases));
	const pins = $derived<MapPin[]>(
		districts.map((entry) => ({
			name: entry.district,
			state: entry.state,
			tone:
				entry.gate === 'protect'
					? 'protect'
					: entry.gate === 'phantom'
						? 'phantom'
						: entry.gate === 'clear'
							? 'clear'
							: 'plain',
			label: `${entry.district}, ${entry.state}`,
			detail:
				`${entry.cases} ${entry.cases === 1 ? 'case' : 'cases'}` +
				(entry.diverted > 0 ? ` · ${entry.diverted} diverted by the gate` : '')
		}))
	);
	const placed = $derived(data.cases.filter((c) => c.access?.district).length);

	// The markers are coloured by the strongest gate state a district actually reached, so the key
	// lists exactly the states this run produced and never invents one the map does not show.
	const GATE_NOTES: Record<GateState, string> = {
		protect: 'a suspension the gate sent to the Committee instead',
		phantom: 'the listed alternative is not a real provider',
		clear: 'the gate ran and the district stays covered',
		unknown: 'the consequence could not be computed, so a human decides'
	};

	const toneKey = $derived([
		...(['protect', 'phantom', 'clear', 'unknown'] as const)
			.filter((state) => districts.some((entry) => entry.gate === state))
			.map((state) => ({
				tone: state as GateState | 'none',
				label: GATES[state].label,
				note: GATE_NOTES[state]
			})),
		...(districts.some((entry) => entry.gate === null)
			? [
					{
						tone: 'none' as GateState | 'none',
						label: 'Not computed',
						note: 'the decided action removes no provider, so the gate was never reached'
					}
				]
			: [])
	]);

	const all = $derived(summarise(data.cases));
	const shown = $derived(summarise(filtered));
	const filtersActive = $derived(filterIsActive(filter));

	function toggleSort(next: Sort) {
		if (sort === next) descending = !descending;
		else {
			sort = next;
			descending = next === 'confidence';
		}
	}

	function reset() {
		group = 'all';
		gate = 'all';
		trigger = 'all';
		district = 'all';
		degradedOnly = false;
	}

	function ariaSort(column: Sort): 'ascending' | 'descending' | 'none' {
		if (sort !== column) return 'none';
		return descending ? 'descending' : 'ascending';
	}
</script>

<svelte:head>
	<title>Cases — The Access Gate</title>
</svelte:head>

<!--
	A band across the whole canvas, then the record on the paper below it. The band is what stops the
	page reading as one long column: the title and the five counts are the part a viewer takes in at a
	glance, and they get their own ground.
-->
<div class="hero">
	<div class="wrap hero-in">
		<header class="intro">
			<p class="eyebrow">Decision log · {data.meta.source_run}</p>
			<h1>Decided cases</h1>
			<p class="lede">
				Every flagged claim the pipeline decided in this run. The action was settled by
				<code>rules/policy.py</code>, not by an agent; the access gate may have diverted a
				suspension to the State Empanelment Committee rather than execute it.
			</p>
		</header>

		<!-- Summary strip: the counts a viewer needs before reading a single row. -->
		<section class="strip" aria-label="Run summary">
			{#each all.byGroup as entry (entry.spec.key)}
				<div class="stat" data-group={entry.spec.key}>
					<span class="stat-mark" aria-hidden="true">{entry.spec.mark}</span>
					<span class="stat-value num">{entry.count}</span>
					<span class="stat-label">{entry.spec.label}</span>
					<span class="stat-note">{entry.spec.note}</span>
				</div>
			{/each}
			<div class="stat diverted">
				<span class="stat-mark" aria-hidden="true">▣</span>
				<span class="stat-value num">{all.divertedByGate}</span>
				<span class="stat-label">Diverted by the gate</span>
				<span class="stat-note">a suspension the gate sent to the Committee or to de-listing</span>
			</div>
		</section>
	</div>
</div>

<div class="wrap">
	{#if districts.length > 0}
		<section class="where panel" aria-labelledby="where-h">
			<div class="where-map">
				<IndiaMap
					label="The districts this run decided cases in, marked on a map of India."
					{pins}
					selected={district === 'all' ? null : district}
					onselect={(name) => (district = name ?? 'all')}
				/>
			</div>

			<div class="where-side">
				<h2 id="where-h">Where these cases are</h2>
				<p class="where-note">
					The gate asks what a <em>district</em> would be left with, so that is what is marked —
					never a hospital. {placed} of {all.total} cases carry a district; a claim refused as malformed
					never reaches the gate and has none.
				</p>

				<ul class="places">
					{#each districts as entry, i (entry.district + entry.state)}
						<li>
							<button
								type="button"
								class:on={district === entry.district}
								aria-pressed={district === entry.district}
								onclick={() => (district = district === entry.district ? 'all' : entry.district)}
							>
								<!-- Same number as the marker on the map, which is what carries the name there. -->
								<span class="place-n" data-gate={entry.gate ?? 'none'} aria-hidden="true"
									>{i + 1}</span
								>
								<span class="place-name">
									{entry.district}<span class="place-state">, {entry.state}</span>
								</span>
								<span class="place-count num">
									{entry.cases}
									{entry.cases === 1 ? 'case' : 'cases'}
								</span>
								{#if entry.diverted > 0}
									<span class="place-diverted">{entry.diverted} diverted</span>
								{/if}
							</button>
						</li>
					{/each}
				</ul>

				{#if toneKey.length > 0}
					<!-- What the marker colours mean. Without this the map asks a viewer to guess. -->
					<ul class="tone-key">
						{#each toneKey as entry (entry.tone)}
							<li>
								<span class="key-dot" data-gate={entry.tone} aria-hidden="true"></span>
								<span><strong>{entry.label}</strong> — {entry.note}</span>
							</li>
						{/each}
					</ul>
				{/if}

				<p class="where-source">
					Boundaries: DataMeet (MIT), from the Survey of India state map and the 2011 Census
					districts. Straight-line distances only — the figure on a case page is the one the gate
					used.
				</p>
			</div>
		</section>
	{/if}

	{#if all.degraded > 0}
		<p class="degraded-run">
			<DegradedMark />
			{#if all.degraded === all.total}
				<span
					>Every case in this run is <strong>degraded</strong>: the model was unavailable, so the
					deterministic rules decided alone. There is no crew data on any case — no roster, no
					disputes, no advocate, no investigation trail. The decisions themselves are unaffected:
					the policy decides identically on both paths.</span
				>
			{:else}
				<span
					><strong>{all.degraded}</strong> of {all.total} cases are degraded: the rules decided them alone,
					with no crew data.</span
				>
			{/if}
		</p>
	{/if}

	<section class="filters" aria-label="Filters">
		<div class="field">
			<label for="f-group">Action group</label>
			<select id="f-group" bind:value={group}>
				<option value="all">All ({all.total})</option>
				{#each all.byGroup as entry (entry.spec.key)}
					<option value={entry.spec.key} disabled={entry.count === 0}>
						{entry.spec.label} ({entry.count})
					</option>
				{/each}
			</select>
		</div>

		<div class="field">
			<label for="f-gate">Gate</label>
			<select id="f-gate" bind:value={gate}>
				<option value="all">All</option>
				<option value="protect">Protected</option>
				<option value="phantom">Phantom provider</option>
				<option value="clear">Clear</option>
				<option value="unknown">Unknown</option>
				<option value="none">Not computed</option>
			</select>
		</div>

		<div class="field">
			<label for="f-trigger">Trigger</label>
			<select id="f-trigger" bind:value={trigger}>
				<option value="all">All</option>
				{#each triggers as id (id)}
					<option value={id}>{id}</option>
				{/each}
			</select>
		</div>

		{#if districts.length > 0}
			<div class="field">
				<label for="f-district">District</label>
				<select id="f-district" bind:value={district}>
					<option value="all">All</option>
					{#each districts as entry (entry.district + entry.state)}
						<option value={entry.district}>{entry.district} ({entry.cases})</option>
					{/each}
				</select>
			</div>
		{/if}

		<label class="toggle">
			<input type="checkbox" bind:checked={degradedOnly} />
			<span>Degraded only</span>
		</label>

		<p class="count" role="status">
			{#if filtersActive}
				{filtered.length} of {all.total} cases
				<button type="button" class="link" onclick={reset}>clear filters</button>
			{:else}
				{all.total} cases · {shown.belowFloor} below the confidence floor
			{/if}
		</p>
	</section>

	{#if filtered.length === 0}
		<div class="empty">
			<p class="empty-title">No cases match these filters.</p>
			<p>
				{all.total} cases are in the record. Widen the filters, or
				<button type="button" class="link" onclick={reset}>clear them</button>.
			</p>
		</div>
	{:else}
		<div class="table-scroll">
			<table class="record cases">
				<caption class="sr-only">
					Decided cases. Select a row to open the full case record.
				</caption>
				<thead>
					<tr>
						<th scope="col" aria-sort={ariaSort('claim')}>
							<button type="button" class="sorter" onclick={() => toggleSort('claim')}>
								Claim <span class="arrow" aria-hidden="true"
									>{sort === 'claim' ? (descending ? '▾' : '▴') : ''}</span
								>
							</button>
						</th>
						<th scope="col">Hospital</th>
						<th scope="col">Trigger</th>
						<th scope="col">District</th>
						<th scope="col">Action</th>
						<th scope="col">Gate</th>
						<th scope="col" class="conf" aria-sort={ariaSort('confidence')}>
							<button type="button" class="sorter" onclick={() => toggleSort('confidence')}>
								Confidence <span class="arrow" aria-hidden="true"
									>{sort === 'confidence' ? (descending ? '▾' : '▴') : ''}</span
								>
							</button>
						</th>
						<th scope="col" aria-sort={ariaSort('decided')}>
							<button type="button" class="sorter" onclick={() => toggleSort('decided')}>
								Decided <span class="arrow" aria-hidden="true"
									>{sort === 'decided' ? (descending ? '▾' : '▴') : ''}</span
								>
							</button>
						</th>
					</tr>
				</thead>
				<tbody>
					{#each filtered as record (record.case_id)}
						<tr class:is-gate={record.gate === 'protect' || record.gate === 'phantom'}>
							<th scope="row" class="claim">
								<a href="{base}/case/{record.claim_id}/" class="ident">{record.claim_id}</a>
								{#if record.degraded}
									<DegradedMark compact />
								{/if}
							</th>
							<td class="ident hosp">{record.hospital_ref}</td>
							<td class="trigger">
								{#if record.trigger_id}
									<span class="ident">{record.trigger_id}</span>
									<span class="trigger-name" title={record.trigger?.name ?? ''}
										>{record.trigger?.name ?? ''}</span
									>
								{:else}
									<span class="none">no trigger</span>
								{/if}
							</td>
							<td class="district">
								{#if record.access?.district}
									{record.access.district}<span class="state">, {record.access.state}</span>
								{:else}
									<span class="none">—</span>
								{/if}
							</td>
							<td>
								<ActionBadge action={record.action} group={record.action_group} size="sm" />
							</td>
							<td><GateBadge gate={record.gate} size="sm" /></td>
							<td class="conf">
								<Confidence value={record.confidence} size="sm" caption={false} />
								{#if record.below_floor}
									<span class="floor-note">below floor</span>
								{/if}
							</td>
							<td class="when mono">{formatTimestamp(record.decided_ts)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
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
		margin-bottom: 22px;
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

	.strip {
		display: grid;
		grid-template-columns: repeat(5, minmax(0, 1fr));
		gap: 0;
		border: 1px solid var(--rule);
		border-radius: 3px;
		background: var(--paper);
		box-shadow: var(--lift);
		overflow: hidden;
	}

	/* The group's colour rides the top edge rather than tinting the whole cell: it identifies the
	   column at a glance without putting coloured text on a coloured field. The glyph beside the
	   number is the encoding that survives without it. */
	.stat {
		padding: 15px 16px 14px;
		border-left: 1px solid var(--rule-soft);
		border-top: 3px solid var(--tone, var(--rule-strong));
		display: grid;
		grid-template-columns: auto 1fr;
		grid-template-rows: auto auto auto;
		column-gap: 8px;
		align-items: baseline;
	}

	.stat:first-child {
		border-left: none;
	}

	.stat-mark {
		grid-row: 1;
		font-size: 12px;
		color: var(--tone, var(--ink-faint));
	}

	.stat-value {
		grid-row: 1;
		font-size: 36px;
		font-weight: 600;
		letter-spacing: -0.035em;
		line-height: 1;
	}

	.stat-label {
		grid-column: 1 / -1;
		grid-row: 2;
		font-size: 12.5px;
		font-weight: 500;
		margin-top: 2px;
	}

	.stat-note {
		grid-column: 1 / -1;
		grid-row: 3;
		font-size: 11px;
		color: var(--ink-muted);
		line-height: 1.35;
	}

	.stat[data-group='released'] {
		--tone: var(--released);
	}
	.stat[data-group='deferred'] {
		--tone: var(--deferred);
	}
	.stat[data-group='enforced'] {
		--tone: var(--enforced);
	}
	.stat[data-group='refused'] {
		--tone: var(--refused);
	}

	.diverted {
		--tone: var(--gate-protect);
		background: var(--gate-protect-wash);
		border-left-color: var(--gate-protect-edge);
	}

	/* ── where these cases are ───────────────────────────────────────────── */

	.where {
		display: grid;
		/* The map is the point of this panel, so it gets the larger share and the list sits beside
		   it. Below 1150px of canvas the two stack and the map keeps a sensible size of its own. */
		/* A ceiling, not a share. As a percentage the map kept growing with the canvas until a
		   1,700px screen gave it a 760px-tall panel with a short list stranded beside it. */
		grid-template-columns: minmax(0, 480px) minmax(0, 1fr);
		gap: 40px;
		/* Centred, not top-aligned: the map is far taller than the list beside it, and hanging the
		   text off the top left the panel looking half-empty at the bottom. */
		align-items: center;
		padding: 22px 24px 24px;
		margin: 26px 0 22px;
	}

	.where-map {
		margin: 0 auto;
		width: 100%;
	}

	.where-side h2 {
		font-size: 19px;
		letter-spacing: -0.02em;
		margin-bottom: 7px;
	}

	.where-note {
		font-size: 12.5px;
		color: var(--ink-soft);
		max-width: 62ch;
		margin-bottom: 12px;
	}

	.places {
		list-style: none;
		margin: 0 0 12px;
		padding: 0;
		display: grid;
		gap: 2px;
	}

	.places button {
		width: 100%;
		display: flex;
		align-items: center;
		gap: 9px;
		padding: 6px 9px;
		font: inherit;
		font-size: 13px;
		text-align: left;
		background: none;
		border: 1px solid transparent;
		border-radius: 2px;
		color: inherit;
		cursor: pointer;
	}

	.places button:hover {
		background: var(--surface-2);
	}

	.places button.on {
		border-color: var(--rule-strong);
		background: var(--surface-2);
	}

	/* Carries the gate's colour as the map marker does, and the number that ties the two together. */
	.place-n {
		display: grid;
		place-items: center;
		width: 19px;
		height: 19px;
		flex: none;
		border-radius: 50%;
		background: var(--ink-soft);
		color: var(--paper);
		font-family: var(--mono);
		font-size: 10.5px;
		font-weight: 600;
		line-height: 1;
	}

	.place-n[data-gate='protect'] {
		background: var(--gate-protect);
	}
	.place-n[data-gate='phantom'] {
		background: var(--gate-phantom);
	}
	.place-n[data-gate='clear'] {
		background: var(--gate-clear);
		color: var(--surface);
	}

	.place-name {
		margin-right: auto;
	}

	.place-state {
		color: var(--ink-muted);
	}

	.place-count {
		font-size: 12px;
		color: var(--ink-muted);
		white-space: nowrap;
	}

	.place-diverted {
		font-size: 11px;
		padding: 1px 6px;
		border-radius: 9px;
		background: var(--gate-protect-wash);
		color: var(--gate-protect);
		border: 1px solid var(--gate-protect-edge);
		white-space: nowrap;
	}

	.tone-key {
		list-style: none;
		margin: 0 0 14px;
		padding: 13px 0 0;
		border-top: 1px solid var(--rule-soft);
		display: grid;
		gap: 6px;
		font-size: 12px;
		color: var(--ink-soft);
	}

	.tone-key li {
		display: flex;
		align-items: baseline;
		gap: 9px;
	}

	.key-dot {
		width: 10px;
		height: 10px;
		flex: none;
		border-radius: 50%;
		background: var(--ink-soft);
		transform: translateY(1px);
	}

	.key-dot[data-gate='protect'] {
		background: var(--gate-protect);
	}
	.key-dot[data-gate='phantom'] {
		background: var(--gate-phantom);
	}
	.key-dot[data-gate='clear'] {
		background: var(--gate-clear);
	}
	.key-dot[data-gate='unknown'] {
		background: var(--gate-unknown);
	}
	.key-dot[data-gate='none'] {
		background: var(--ink-faint);
	}

	.where-source {
		font-size: 11px;
		color: var(--ink-faint);
		max-width: 64ch;
		margin: 0;
		line-height: 1.45;
	}

	@media (max-width: 1200px) {
		.where {
			grid-template-columns: minmax(0, 1fr);
			gap: 20px;
		}

		.where-map {
			max-width: 340px;
		}
	}

	.degraded-run {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		max-width: none;
		border: 1px solid var(--rule);
		border-left: 3px solid var(--ink-muted);
		background: var(--surface-2);
		padding: 10px 14px;
		font-size: 13px;
		color: var(--ink-soft);
		margin-bottom: 18px;
	}

	/* The filters and the table are one object: a toolbar sitting on the sheet it filters, with the
	   join left unruled so the two read as a single panel rather than two stacked boxes. */
	.filters {
		display: flex;
		align-items: flex-end;
		gap: 18px;
		flex-wrap: wrap;
		padding: 13px 16px;
		background: var(--surface);
		border: 1px solid var(--rule);
		border-bottom-color: var(--rule-strong);
		border-radius: 3px 3px 0 0;
		box-shadow: var(--lift);
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
		min-width: 140px;
	}

	.toggle {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 13px;
		padding-bottom: 5px;
		cursor: pointer;
	}

	.count {
		margin: 0 0 4px auto;
		font-size: 12.5px;
		color: var(--ink-muted);
	}

	.link {
		background: none;
		border: none;
		padding: 0;
		font: inherit;
		color: var(--ink);
		text-decoration: underline;
		text-underline-offset: 0.2em;
		cursor: pointer;
	}

	.empty {
		border: 1px dashed var(--rule-strong);
		border-radius: 2px;
		padding: 40px 24px;
		text-align: center;
		margin-top: 24px;
		background: var(--surface);
	}

	.empty-title {
		font-weight: 600;
		font-size: 15px;
		margin: 0 auto 6px;
	}

	.empty p {
		color: var(--ink-muted);
		font-size: 13.5px;
		margin-left: auto;
		margin-right: auto;
	}

	.table-scroll {
		overflow-x: auto;
		border: 1px solid var(--rule);
		border-top: none;
		border-radius: 0 0 3px 3px;
		background: var(--surface);
		box-shadow: var(--lift);
	}

	.cases th[scope='row'] {
		font-weight: 500;
	}

	.cases tbody tr.is-gate {
		box-shadow: inset 3px 0 0 var(--gate-protect-edge);
	}

	.claim {
		white-space: nowrap;
	}

	.claim a {
		font-weight: 500;
		margin-right: 5px;
	}

	.hosp {
		color: var(--ink-soft);
		white-space: nowrap;
	}

	.trigger .ident {
		font-weight: 500;
	}

	/* Two lines at most: the action and gate columns are what a viewer scans, and a four-line
	   trigger name makes the rows uneven enough to slow that down. The full name is on the case. */
	.trigger-name {
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
		font-size: 11.5px;
		color: var(--ink-muted);
		max-width: 30ch;
		line-height: 1.3;
	}

	.district {
		white-space: nowrap;
	}

	.state {
		color: var(--ink-muted);
	}

	.none {
		color: var(--ink-faint);
	}

	.conf {
		min-width: 122px;
	}

	.floor-note {
		display: block;
		font-size: 10.5px;
		color: var(--deferred);
		font-weight: 500;
		margin-top: 1px;
	}

	.when {
		color: var(--ink-muted);
		font-size: 11.5px;
		white-space: nowrap;
	}

	.sorter {
		background: none;
		border: none;
		padding: 0;
		font: inherit;
		color: inherit;
		cursor: pointer;
		text-transform: inherit;
		letter-spacing: inherit;
	}

	.sorter:hover {
		color: var(--ink);
	}

	.arrow {
		display: inline-block;
		width: 0.7em;
	}

	@media (max-width: 960px) {
		.strip {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.stat {
			border-top: 1px solid var(--rule-soft);
		}
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
