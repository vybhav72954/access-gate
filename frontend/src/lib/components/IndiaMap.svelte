<!--
	India, drawn from bundled geometry.

	Two jobs, one component. With `values` it is a choropleth of whatever the evaluation measured
	per state; without, it is a locator that points at districts. Both draw from the same paths, so
	a district sits in exactly the same place on every page that shows it.

	Three things this deliberately does not do:

	* **No tile server, no map library.** The geometry is a bundled module (see `$lib/map`), so the
	  whole thing renders with the network off, which is the condition the built site has to meet.
	* **No colour-only meaning.** Every shaded state is also listed with its figure beside the map,
	  every pin carries a written label, and the legend prints the bounds of each step.
	* **No JavaScript needed to see it.** The pages are prerendered, so the paths, the pins and
	  their labels are in the HTML. Hover, focus and selection are enhancements on top.
-->
<script lang="ts">
	import {
		anchorPlacement,
		crop,
		districtByName,
		india,
		intersects,
		viewBox as boxToViewBox,
		WHOLE_COUNTRY,
		type Box,
		type MapConnector,
		type MapDistrict,
		type MapPin,
		type Scale
	} from '$lib/map';

	interface Props {
		/** Accessible name for the figure. Required: a map with no name is a decorative blob. */
		label: string;
		/** State name to value. Absent means locator mode: neutral land, no ramp, no legend. */
		values?: Map<string, number>;
		scale?: Scale;
		format?: (value: number) => string;
		/** What the shaded quantity is, for the legend and the screen-reader list. */
		measure?: string;
		/** Districts to call out. */
		pins?: MapPin[];
		connectors?: MapConnector[];
		/** Crop the frame to these districts rather than showing the whole country. */
		focus?: { name: string; state: string }[];
		/** The state or district the page has selected, kept by the parent. */
		selected?: string | null;
		onselect?: (name: string | null) => void;
		/**
		 * What the hatched states mean. The default is deliberately weak: the caller knows whether a
		 * state is absent because it was never modelled or because it fell outside an exported top ten,
		 * and those are very different claims to put under a map.
		 */
		offLabel?: string;
	}

	let {
		label,
		values,
		scale,
		format = (value: number) => String(value),
		measure = '',
		pins = [],
		connectors = [],
		focus = [],
		selected = null,
		onselect,
		offLabel = 'not shown'
	}: Props = $props();

	const focusShapes = $derived(
		focus
			.map((entry) => districtByName(entry.name, entry.state))
			.filter((shape): shape is MapDistrict => shape !== undefined)
	);
	const box = $derived<Box>(focusShapes.length ? crop(focusShapes) : WHOLE_COUNTRY);
	const cropped = $derived(focusShapes.length > 0);

	// Cropping is what keeps a case page small: only the states the frame actually touches reach
	// the HTML, rather than all thirty-six.
	const visibleStates = $derived(india.states.filter((state) => intersects(state, box)));

	const pinShapes = $derived(
		pins
			.map((pin) => ({ pin, shape: districtByName(pin.name, pin.state) }))
			.filter((entry): entry is { pin: MapPin; shape: MapDistrict } => entry.shape !== undefined)
	);

	const lines = $derived(
		connectors
			.map((connector) => ({
				connector,
				from: districtByName(connector.from.name, connector.from.state),
				to: districtByName(connector.to.name, connector.to.state)
			}))
			.filter(
				(entry): entry is { connector: MapConnector; from: MapDistrict; to: MapDistrict } =>
					entry.from !== undefined && entry.to !== undefined
			)
	);

	/**
	 * States holding a pinned district. On a locator the whole country is one flat grey, and three
	 * markers on it give the eye nothing to travel along; washing their states guides it there
	 * without implying the state itself carries a value.
	 */
	const pinnedStates = $derived(new Set(pinShapes.map((entry) => entry.pin.state)));

	/** What the tooltip is currently describing: hover and focus set it, selection outlives both. */
	let hot = $state<string | null>(null);
	const showing = $derived(hot ?? selected);

	const shown = $derived.by(() => {
		if (showing === null) return null;
		const pin = pinShapes.find((entry) => entry.pin.name === showing);
		if (pin) {
			return {
				title: pin.pin.label,
				body: pin.pin.detail ?? `${pin.pin.name}, ${pin.pin.state}`,
				cx: pin.shape.cx,
				cy: pin.shape.cy
			};
		}
		const state = visibleStates.find((entry) => entry.name === showing);
		const value = values?.get(showing);
		if (state && value !== undefined) {
			return {
				title: state.name,
				body: `${format(value)}${measure ? ` ${measure}` : ''}`,
				cx: state.cx,
				cy: state.cy
			};
		}
		return null;
	});

	const placement = $derived(shown ? anchorPlacement(shown.cx, shown.cy, box) : null);

	/** Percentages, so an HTML overlay lines up with the SVG at any size. */
	const left = (x: number): string => `${((x - box.x) / box.width) * 100}%`;
	const top = (y: number): string => `${((y - box.y) / box.height) * 100}%`;

	const fill = (name: string): string => {
		const value = values?.get(name);
		if (value === undefined || !scale) return 'var(--map-land)';
		return `var(--choro-${scale.bin(value) + 1})`;
	};

	function choose(name: string) {
		onselect?.(selected === name ? null : name);
	}

	const listed = $derived(values ? [...values.entries()].sort((a, b) => b[1] - a[1]) : []);
</script>

<figure class="map" class:cropped>
	<div class="frame" style="aspect-ratio: {box.width} / {box.height}">
		<svg
			viewBox={boxToViewBox(box)}
			role="group"
			aria-label={label}
			onpointerleave={() => (hot = null)}
		>
			<defs>
				<!-- The states the evaluation did not model are hatched as well as pale, so "no data"
				     survives a greyscale print and does not read as "a low number". -->
				<pattern
					id="ag-unmodelled"
					width="6"
					height="6"
					patternUnits="userSpaceOnUse"
					patternTransform="rotate(45)"
				>
					<rect width="6" height="6" fill="var(--map-land)" />
					<line x1="0" y1="0" x2="0" y2="6" stroke="var(--map-land-edge)" stroke-width="1.4" />
				</pattern>
			</defs>

			<!-- The country's own edge, drawn once beneath the states so the coastline stays crisp
			     where two states meet the sea. -->
			<path class="halo" d={india.outline} fill-rule="evenodd" />

			<g class="land">
				{#each visibleStates as state (state.name)}
					{@const value = values?.get(state.name)}
					{#if value !== undefined}
						<path
							class="state live"
							class:selected={selected === state.name}
							class:dimmed={selected !== null && selected !== state.name}
							d={state.d}
							fill-rule="evenodd"
							style="fill: {fill(state.name)}"
							role="button"
							tabindex="0"
							aria-pressed={selected === state.name}
							aria-label="{state.name}: {format(value)} {measure}"
							onpointerenter={() => (hot = state.name)}
							onfocus={() => (hot = state.name)}
							onblur={() => (hot = null)}
							onclick={() => choose(state.name)}
							onkeydown={(event) => {
								if (event.key === 'Enter' || event.key === ' ') {
									event.preventDefault();
									choose(state.name);
								}
							}}
						/>
					{:else}
						<path
							class="state"
							class:unmodelled={values !== undefined}
							class:holds-pin={values === undefined && pinnedStates.has(state.name)}
							d={state.d}
							fill-rule="evenodd"
							aria-hidden="true"
							focusable="false"
						/>
					{/if}
				{/each}
			</g>

			{#each lines as line (line.connector.label + line.from.name)}
				<line
					class="connector"
					x1={line.from.cx}
					y1={line.from.cy}
					x2={line.to.cx}
					y2={line.to.cy}
					aria-hidden="true"
				/>
			{/each}

			<g class="pins" aria-hidden="true">
				{#each pinShapes as entry (entry.pin.name + entry.pin.state)}
					<path
						class="district"
						data-tone={entry.pin.tone}
						class:lit={showing === entry.pin.name}
						d={entry.shape.d}
						fill-rule="evenodd"
					/>
				{/each}
			</g>
		</svg>

		<!-- Labels and hit targets live in HTML, not SVG: a real <button> is keyboard-operable and
		     screen-reader-legible for free, and text keeps one size whether the frame is cropped
		     to two districts or opened out to the whole country. -->
		<div class="overlay">
			<!--
				Only when there is one line. Several alternatives radiate from the same district, so
				their midpoints land within a few pixels of each other and the labels pile up. The
				caller's legend carries every distance in all cases; this is the flourish that fits
				when it fits.
			-->
			{#if lines.length === 1 && lines[0]}
				{@const only = lines[0]}
				<span
					class="distance"
					style="left: {left((only.from.cx + only.to.cx) / 2)}; top: {top(
						(only.from.cy + only.to.cy) / 2
					)}"
				>
					{only.connector.label}
				</span>
			{/if}

			{#each pinShapes as entry, i (entry.pin.name + entry.pin.state)}
				<button
					type="button"
					class="pin"
					data-tone={entry.pin.tone}
					class:lit={showing === entry.pin.name}
					style="left: {left(entry.shape.cx)}; top: {top(entry.shape.cy)}"
					aria-label="{entry.pin.label}{entry.pin.detail ? `. ${entry.pin.detail}` : ''}"
					onpointerenter={() => (hot = entry.pin.name)}
					onpointerleave={() => (hot = null)}
					onfocus={() => (hot = entry.pin.name)}
					onblur={() => (hot = null)}
					onclick={() => choose(entry.pin.name)}
				>
					<span class="marker" aria-hidden="true">{i + 1}</span>
				</button>
			{/each}

			{#if shown && placement}
				<div
					class="tip"
					data-side={placement.side}
					data-vertical={placement.vertical}
					style="left: {left(shown.cx)}; top: {top(shown.cy)}"
					role="status"
				>
					<span class="tip-title">{shown.title}</span>
					<span class="tip-body">{shown.body}</span>
				</div>
			{/if}
		</div>
	</div>

	{#if values && scale}
		<div class="legend">
			<span class="legend-label">{measure}</span>
			<ol>
				{#each Array.from({ length: scale.steps }, (_, i) => i) as step (step)}
					{@const [low, high] = scale.range(step)}
					<li>
						<span class="swatch" style="background: var(--choro-{step + 1})" aria-hidden="true"
						></span>
						<span class="bounds">{format(low)}–{format(high)}</span>
					</li>
				{/each}
				<li class="off">
					<span class="swatch hatched" aria-hidden="true"></span>
					<span class="bounds">{offLabel}</span>
				</li>
			</ol>
		</div>
	{/if}

	<!-- The map's content in words. A choropleth is unreadable to a screen reader otherwise, and
	     this is also what a reader gets if the SVG fails to paint. -->
	{#if listed.length > 0}
		<ul class="sr-only">
			{#each listed as [name, value] (name)}
				<li>{name}: {format(value)} {measure}</li>
			{/each}
		</ul>
	{/if}
</figure>

<style>
	.map {
		margin: 0;
	}

	.frame {
		position: relative;
		width: 100%;
	}

	svg {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		display: block;
		/* Must clip. A cropped frame still draws whole states — Maharashtra reaches well past a frame
		   around Barwani — and `visible` paints every one of them across the rest of the page. The
		   tooltip is HTML in the overlay above, so nothing here needs to escape. */
		overflow: hidden;
	}

	.halo {
		fill: var(--map-halo);
		stroke: var(--map-land-edge);
		stroke-width: 1.6;
		stroke-linejoin: round;
	}

	.state {
		fill: var(--map-land);
		stroke: var(--paper);
		stroke-width: 0.7;
		stroke-linejoin: round;
	}

	.state.unmodelled {
		fill: url(#ag-unmodelled);
	}

	.state.holds-pin {
		fill: var(--surface-3);
	}

	.state.live {
		cursor: pointer;
		transition:
			fill 160ms ease,
			opacity 160ms ease;
	}

	.state.live:hover,
	.state.live:focus-visible {
		stroke: var(--map-ink);
		stroke-width: 1.4;
	}

	.state.live:focus-visible {
		outline: none;
	}

	.state.dimmed {
		opacity: 0.42;
	}

	.state.selected {
		stroke: var(--map-ink);
		stroke-width: 1.8;
	}

	.connector {
		stroke: var(--ink-soft);
		stroke-width: 1.6;
		stroke-dasharray: 5 4;
		stroke-linecap: round;
	}

	/* The called-out districts. Tone matches the gate badge on the same case, so the map and the
	   table cannot tell different stories. */
	.district {
		fill: var(--tone);
		stroke: var(--map-ink);
		stroke-width: 1;
		stroke-linejoin: round;
		transition: fill 160ms ease;
	}

	.district.lit {
		stroke-width: 2;
	}

	[data-tone='protect'] {
		--tone: var(--gate-protect);
	}
	[data-tone='phantom'] {
		--tone: var(--gate-phantom);
	}
	[data-tone='clear'] {
		--tone: var(--gate-clear);
	}
	[data-tone='plain'] {
		--tone: var(--ink-soft);
	}
	[data-tone='alternative'] {
		--tone: var(--surface-3);
	}

	.overlay {
		position: absolute;
		inset: 0;
		pointer-events: none;
	}

	/*
		A marker is a fixed 22px whatever the frame holds.

		Named chips were the obvious thing and they do not work: a chip is about 90px wide however far
		the map is zoomed, so at national zoom — where a district is a few pixels across — two of them
		collide long before their districts do, and Ahmedabad sat on top of Barwani. A number is small
		enough never to collide, and the list beside the map carries the names against the same
		numbers.
	*/
	.pin {
		position: absolute;
		transform: translate(-50%, -50%);
		display: block;
		padding: 0;
		border: none;
		background: none;
		font: inherit;
		line-height: 1;
		cursor: pointer;
		pointer-events: auto;
	}

	.marker {
		display: grid;
		place-items: center;
		width: 22px;
		height: 22px;
		border-radius: 50%;
		background: var(--tone, var(--ink-soft));
		border: 2px solid var(--surface);
		box-shadow: 0 1px 4px rgb(0 0 0 / 0.3);
		color: var(--paper);
		font-family: var(--mono);
		font-size: 11px;
		font-weight: 600;
		line-height: 1;
		transition: transform 140ms ease;
	}

	/* `alternative` is a pale fill; ink on it, not paper. */
	.pin[data-tone='alternative'] .marker {
		color: var(--ink);
		border-color: var(--ink-soft);
	}

	.pin:hover .marker,
	.pin.lit .marker {
		transform: scale(1.18);
	}

	.distance {
		position: absolute;
		/* Lifted clear of the line it labels, and of the marker at the far end of it. */
		transform: translate(-50%, -155%);
		background: var(--paper);
		border: 1px solid var(--rule);
		border-radius: 2px;
		padding: 1px 5px;
		font-family: var(--mono);
		font-size: 10.5px;
		color: var(--ink-soft);
		white-space: nowrap;
	}

	.tip {
		position: absolute;
		z-index: 4;
		display: grid;
		gap: 1px;
		min-width: 120px;
		max-width: 220px;
		padding: 7px 10px;
		background: var(--surface);
		border: 1px solid var(--ink-soft);
		border-radius: 3px;
		box-shadow: 0 3px 12px rgb(0 0 0 / 0.2);
		pointer-events: none;
	}

	.tip[data-side='right'] {
		margin-left: 12px;
	}

	.tip[data-side='left'] {
		transform: translateX(-100%);
		margin-left: -12px;
	}

	.tip[data-vertical='above'] {
		margin-top: -10px;
	}

	.tip[data-vertical='above'][data-side='left'] {
		transform: translate(-100%, -100%);
	}

	.tip[data-vertical='above'][data-side='right'] {
		transform: translateY(-100%);
	}

	.tip-title {
		font-size: 12.5px;
		font-weight: 600;
	}

	.tip-body {
		font-size: 12px;
		color: var(--ink-soft);
		font-variant-numeric: tabular-nums;
	}

	.legend {
		margin-top: 12px;
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 4px 14px;
	}

	.legend-label {
		font-family: var(--mono);
		font-size: 10.5px;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	.legend ol {
		display: flex;
		flex-wrap: wrap;
		gap: 3px 12px;
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.legend li {
		display: flex;
		align-items: center;
		gap: 5px;
		font-size: 11.5px;
		color: var(--ink-soft);
		font-variant-numeric: tabular-nums;
	}

	.swatch {
		width: 15px;
		height: 10px;
		border: 1px solid var(--rule-strong);
		flex: none;
	}

	.swatch.hatched {
		background: repeating-linear-gradient(
			45deg,
			var(--map-land),
			var(--map-land) 2px,
			var(--map-land-edge) 2px,
			var(--map-land-edge) 3px
		);
	}

	/* On a phone the frame is narrow enough that full-size markers crowd the districts they sit on. */
	@media (max-width: 620px) {
		.marker {
			width: 19px;
			height: 19px;
			font-size: 10px;
			border-width: 1.5px;
		}
	}
</style>
