<!--
	The access gate: what suspending this hospital would do to the district.

	This is the panel the project exists for. When the gate read `protect` or `phantom` it dominates
	the screen, because a suspension was diverted rather than executed and that is the whole argument.

	Two values that must not be softened:
	  · a null gate is NOT `clear` — the gate is only computed for actions that would remove a
	    provider, and a case that never reached it never had the question asked;
	  · `km_to_alternative` of null means "no alternative listed", which is a stronger access signal
	    than a large number, not a missing value to hide.
-->
<script lang="ts">
	import { formatDistance, formatInt, gateNotApplicable, gateSpec } from '../domain';
	import { districtByName, findDistrict, type MapConnector, type MapPin } from '../map';
	import type { Access, ActionKey, AtStake, GateState } from '../types';
	import IndiaMap from './IndiaMap.svelte';

	interface Props {
		gate: GateState | null;
		action: ActionKey;
		access: Access | null;
		atStake: readonly AtStake[];
		specialtiesAtStake: readonly string[];
	}

	let { gate, action, access, atStake, specialtiesAtStake }: Props = $props();

	const spec = $derived(gateSpec(gate));
	const distance = $derived(formatDistance(access?.km_to_alternative));

	// ── the locator ──────────────────────────────────────────────────────────
	// The gate's whole question is a distance: this district, and how far the next provider is. A
	// table states it; the map is the one place a reader can see it. Drawn only when the geometry
	// is actually there, so a district the map does not carry degrades to the table alone.
	const here = $derived(
		access?.district && access?.state ? districtByName(access.district, access.state) : undefined
	);

	/**
	 * Every district a patient might be sent to instead — the billed specialty's alternative and
	 * each one at stake. S5 alone reaches two, in a different state from the hospital.
	 */
	const alternatives = $derived.by(() => {
		const names = new Set<string>();
		if (access?.nearest_alternative_district) names.add(access.nearest_alternative_district);
		for (const stake of atStake) {
			if (stake.nearest_alternative_district) names.add(stake.nearest_alternative_district);
		}
		return [...names]
			.map((name) => ({ name, shape: findDistrict(name) }))
			.filter((entry) => entry.shape !== undefined);
	});

	/**
	 * How far each alternative is, keyed by district.
	 *
	 * The billed specialty's distance is a number on the record. The at-stake rows carry theirs only
	 * inside the prose the artefact wrote, so it is read back from there rather than measured off
	 * the map — a measured figure would be a second, slightly different number beside the one the
	 * gate actually used.
	 */
	const distances = $derived.by(() => {
		const km = new Map<string, number>();
		if (access?.nearest_alternative_district && access.km_to_alternative !== null) {
			km.set(access.nearest_alternative_district, access.km_to_alternative);
		}
		for (const stake of atStake) {
			const found = /(\d+(?:\.\d+)?)\s*km away/.exec(stake.nearest_alternative)?.[1];
			if (
				stake.nearest_alternative_district &&
				found &&
				!km.has(stake.nearest_alternative_district)
			) {
				km.set(stake.nearest_alternative_district, Number(found));
			}
		}
		return km;
	});

	/** One list behind the markers, the legend and the connectors, so the three cannot disagree. */
	const places = $derived.by(() => {
		if (!here || !access) return [];
		const out: { pin: MapPin; note: string; km: number | null }[] = [
			{
				pin: {
					name: here.name,
					state: here.state,
					tone: gate === 'protect' ? 'protect' : gate === 'phantom' ? 'phantom' : 'clear',
					label: `${here.name}, ${here.state}`,
					detail: `${formatInt(access.n_providers)} empanelled ${
						access.n_providers === 1 ? 'provider' : 'providers'
					} of ${access.specialty_name ?? 'this specialty'}`
				},
				note: `this district — ${formatInt(access.n_providers)} ${
					access.n_providers === 1 ? 'provider' : 'providers'
				}`,
				km: null
			}
		];
		for (const entry of alternatives) {
			if (!entry.shape || entry.shape.name === here.name) continue;
			const km = distances.get(entry.shape.name) ?? null;
			out.push({
				pin: {
					name: entry.shape.name,
					state: entry.shape.state,
					tone: 'alternative',
					label: `${entry.shape.name}, ${entry.shape.state}`,
					detail: km === null ? 'the alternative' : `${Math.round(km)} km away`
				},
				note: km === null ? 'alternative provider' : `${Math.round(km)} km away`,
				km
			});
		}
		return out;
	});

	const pins = $derived(places.map((place) => place.pin));

	const connectors = $derived<MapConnector[]>(
		here
			? places
					.filter((place) => place.km !== null && place.pin.name !== here.name)
					.map((place) => ({
						from: { name: here.name, state: here.state },
						to: { name: place.pin.name, state: place.pin.state },
						label: `${Math.round(place.km ?? 0)} km`
					}))
			: []
	);

	const focus = $derived(pins.map((pin) => ({ name: pin.name, state: pin.state })));
</script>

<section
	class="gate-panel"
	data-gate={gate ?? 'none'}
	class:emphatic={spec?.emphatic ?? false}
	aria-labelledby="gate-heading"
>
	<header>
		<p class="eyebrow">The access gate</p>
		<h2 id="gate-heading">
			<span class="mark" aria-hidden="true">{spec?.mark ?? '·'}</span>
			{spec?.label ?? 'Not computed'}
		</h2>
		<p class="summary">{spec?.summary ?? gateNotApplicable(action)}</p>
	</header>

	{#if access}
		<div class="with-map" class:has-map={here !== undefined}>
			{#if here}
				<figure class="locator">
					<IndiaMap
						label="{here.name} district{connectors.length > 0
							? `, and the ${connectors.length === 1 ? 'district' : 'districts'} holding the nearest alternative provider`
							: ''}."
						{pins}
						{connectors}
						{focus}
					/>
					<figcaption>
						<ol class="key">
							{#each places as place, i (place.pin.name)}
								<li>
									<span class="key-n" data-tone={place.pin.tone} aria-hidden="true">{i + 1}</span>
									<span class="key-name">{place.pin.name}</span>
									<span class="key-note">{place.note}</span>
								</li>
							{/each}
						</ol>
						<p>
							{#if connectors.length > 0}
								The dashed line is the journey a patient would make if this hospital left the
								scheme. Distances are the gate's own, straight-line.
							{:else}
								No journey is drawn: the alternative providers are inside the same district.
							{/if}
						</p>
					</figcaption>
				</figure>
			{/if}

			<dl class="facts">
				<div>
					<dt>District</dt>
					<dd>{access.district ?? '—'}{access.state ? `, ${access.state}` : ''}</dd>
				</div>
				<div>
					<dt>Billed specialty</dt>
					<dd>{access.specialty_name ?? access.specialty ?? '—'}</dd>
				</div>
				<div>
					<dt>Empanelled providers in the district</dt>
					<dd class="big num">{formatInt(access.n_providers)}</dd>
				</div>
				<div class:strong={distance.noneListed}>
					<dt>Nearest other provider</dt>
					<dd>
						{#if access.nearest_alternative}
							{access.nearest_alternative}
						{:else if distance.noneListed}
							<span class="none-listed">none listed</span>
						{:else}
							{distance.text}
						{/if}
						{#if distance.noneListed}
							<span class="foot">no alternative anywhere in the data — not a missing value</span>
						{/if}
					</dd>
				</div>
				<div>
					<dt>District population</dt>
					<dd class="num">{formatInt(access.population)}</dd>
				</div>
				<div class:strong={access.aspirational === true}>
					<dt>NITI aspirational district</dt>
					<dd>{access.aspirational === null ? '—' : access.aspirational ? 'Yes' : 'No'}</dd>
				</div>
				<div class:strong={access.capability_ok === false}>
					<dt>Capability flag</dt>
					<dd>
						{access.capability_ok === null
							? '—'
							: access.capability_ok
								? 'Plausible'
								: 'IMPLAUSIBLE'}
						{#if access.capability_reason}
							<span class="foot">{access.capability_reason}</span>
						{/if}
					</dd>
				</div>
				<div>
					<dt>Hospital empanelled for it</dt>
					<dd>
						{access.hospital_listed === null ? '—' : access.hospital_listed ? 'Yes' : 'No'}
					</dd>
				</div>
			</dl>
		</div>
	{:else}
		<p class="void">
			No network adequacy was computed for this case, so there are no district figures on the
			record.
		</p>
	{/if}

	{#if atStake.length > 0}
		<div class="stake">
			<h3>What a suspension would remove</h3>
			<p class="stake-note">
				A suspension takes the hospital out of the scheme for every specialty it is empanelled for,
				not only the one it billed.
			</p>
			<div class="table-scroll">
				<table class="record">
					<thead>
						<tr>
							<th scope="col">Specialty at stake</th>
							<th scope="col" class="n">Providers in district</th>
							<th scope="col">Nearest other provider</th>
							<th scope="col">Why it is protected</th>
						</tr>
					</thead>
					<tbody>
						{#each atStake as row (row.specialty_name)}
							<tr>
								<th scope="row">{row.specialty_name}</th>
								<td class="n"><strong>{formatInt(row.n_providers)}</strong></td>
								<td>{row.nearest_alternative}</td>
								<td class="why">{row.why_protected}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	{:else if specialtiesAtStake.length > 0}
		<div class="stake">
			<h3>Specialties at stake</h3>
			<ul class="plain">
				{#each specialtiesAtStake as name (name)}
					<li class="ident">{name}</li>
				{/each}
			</ul>
		</div>
	{:else if gate === 'clear'}
		<p class="cleared">
			No specialty this hospital is empanelled for would lose its only real provider beyond reach,
			so the suspension proceeded.
		</p>
	{/if}
</section>

<style>
	.gate-panel {
		border: 1px solid var(--rule-strong);
		border-radius: 2px;
		background: var(--surface);
		padding: 18px 20px 20px;
	}

	.gate-panel[data-gate='protect'] {
		--tone: var(--gate-protect);
		--edge: var(--gate-protect-edge);
		--wash: var(--gate-protect-wash);
	}
	.gate-panel[data-gate='phantom'] {
		--tone: var(--gate-phantom);
		--edge: var(--gate-phantom);
		--wash: var(--gate-phantom-wash);
	}
	.gate-panel[data-gate='unknown'] {
		--tone: var(--gate-unknown);
		--edge: var(--gate-unknown);
		--wash: var(--gate-unknown-wash);
	}
	.gate-panel[data-gate='clear'] {
		--tone: var(--gate-clear);
		--edge: var(--rule-strong);
		--wash: var(--surface);
	}
	.gate-panel[data-gate='none'] {
		--tone: var(--ink-faint);
		--edge: var(--rule);
		--wash: var(--surface);
		border-style: dashed;
	}

	/* The emphatic states get the boldest treatment in the app. */
	.emphatic {
		border-width: 2px;
		border-color: var(--edge);
		background: var(--wash);
		box-shadow: inset 0 3px 0 var(--edge);
	}

	.eyebrow {
		margin: 0;
	}

	h2 {
		font-size: 25px;
		margin: 4px 0 6px;
		display: flex;
		align-items: center;
		gap: 10px;
		letter-spacing: -0.02em;
	}

	.emphatic h2 {
		font-size: 31px;
	}

	.mark {
		color: var(--tone);
		font-size: 0.85em;
	}

	.summary {
		font-size: 14.5px;
		color: var(--ink-soft);
		max-width: 72ch;
		margin: 0;
	}

	.emphatic .summary {
		font-size: 16px;
		color: var(--ink);
		font-weight: 500;
	}

	/* The map sits beside the figures rather than above them, so the distance in the table and the
	   distance on the map are in the eye at the same time. */
	.with-map.has-map {
		display: grid;
		grid-template-columns: minmax(0, 300px) minmax(0, 1fr);
		gap: 24px;
		align-items: start;
		margin-top: 18px;
	}

	.locator {
		margin: 0;
		padding: 10px 10px 0;
		border: 1px solid var(--rule);
		border-radius: 2px;
		background: var(--surface-2);
	}

	.locator figcaption {
		font-size: 11px;
		color: var(--ink-muted);
		line-height: 1.45;
		padding: 8px 2px 10px;
	}

	.locator figcaption p {
		margin: 0;
		max-width: none;
	}

	/* The markers carry a number, not a name: three neighbouring districts are closer together than
	   three name labels can be. The names are here instead. */
	.key {
		list-style: none;
		margin: 0 0 8px;
		padding: 0;
		display: grid;
		gap: 3px;
	}

	.key li {
		display: flex;
		align-items: baseline;
		gap: 6px;
	}

	.key-n {
		display: inline-grid;
		place-items: center;
		width: 15px;
		height: 15px;
		flex: none;
		border-radius: 50%;
		background: var(--key-tone, var(--ink-soft));
		color: var(--paper);
		font-family: var(--mono);
		font-size: 9.5px;
		font-weight: 600;
		line-height: 1;
		transform: translateY(2px);
	}

	.key-n[data-tone='protect'] {
		--key-tone: var(--gate-protect);
	}
	.key-n[data-tone='phantom'] {
		--key-tone: var(--gate-phantom);
	}
	.key-n[data-tone='clear'] {
		--key-tone: var(--gate-clear);
	}
	.key-n[data-tone='alternative'] {
		--key-tone: var(--surface-3);
		color: var(--ink);
		border: 1px solid var(--ink-soft);
	}

	.key-name {
		color: var(--ink);
		font-size: 12px;
		font-weight: 500;
	}

	.key-note {
		font-size: 11px;
	}

	.facts {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
		gap: 0;
		margin: 18px 0 0;
		border-top: 1px solid var(--rule);
	}

	.with-map.has-map .facts {
		margin-top: 0;
	}

	@media (max-width: 780px) {
		.with-map.has-map {
			grid-template-columns: minmax(0, 1fr);
			gap: 16px;
		}

		.locator {
			max-width: 320px;
		}
	}

	.facts > div {
		padding: 9px 12px 10px 0;
		border-bottom: 1px solid var(--rule-soft);
		min-width: 0;
	}

	dt {
		font-family: var(--mono);
		font-size: 10.5px;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	dd {
		margin: 3px 0 0;
		font-size: 14.5px;
		line-height: 1.4;
	}

	dd.big {
		font-size: 24px;
		font-weight: 600;
		letter-spacing: -0.02em;
	}

	.facts > div.strong dd {
		font-weight: 600;
		color: var(--tone);
	}

	.none-listed {
		font-weight: 600;
	}

	.foot {
		display: block;
		font-size: 11.5px;
		color: var(--ink-muted);
		font-weight: 400;
		line-height: 1.35;
		margin-top: 1px;
	}

	.stake {
		margin-top: 20px;
		border-top: 1px solid var(--rule-strong);
		padding-top: 14px;
	}

	h3 {
		font-size: 15px;
	}

	.stake-note {
		font-size: 12.5px;
		color: var(--ink-muted);
		margin: 3px 0 10px;
		max-width: 72ch;
	}

	.why {
		color: var(--ink-soft);
	}

	.table-scroll {
		overflow-x: auto;
	}

	.cleared,
	.void {
		margin: 16px 0 0;
		font-size: 13.5px;
		color: var(--ink-soft);
		border-left: 3px solid var(--rule-strong);
		padding-left: 12px;
		max-width: 72ch;
	}

	.plain {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}

	.plain li {
		border: 1px solid var(--rule);
		padding: 1px 7px;
		border-radius: 2px;
		font-size: 12px;
	}

	@media (max-width: 720px) {
		.gate-panel {
			padding: 14px 14px 16px;
		}
		.emphatic h2 {
			font-size: 25px;
		}
	}
</style>
