<!--
	The distance-threshold sweep, as a line chart.

	One y-axis, two series, direct labels at the right-hand end and a legend, so identity never rests
	on colour. A crosshair reads the nearest km step on hover or keyboard focus, and the same numbers
	are available as a table below the chart.
-->
<script lang="ts">
	interface Series {
		key: string;
		label: string;
		tone: 'series-1' | 'series-2' | 'series-3';
		values: number[];
	}

	interface Props {
		xs: number[];
		series: Series[];
		xLabel: string;
		yLabel: string;
		/** Drawn as a vertical rule: the operating point the policy actually uses. */
		marker?: { x: number; label: string } | null;
		height?: number;
	}

	let { xs, series, xLabel, yLabel, marker = null, height = 250 }: Props = $props();

	const pad = { top: 14, right: 196, bottom: 36, left: 56 };
	const width = 760;
	const plotW = width - pad.left - pad.right;
	const plotH = $derived(height - pad.top - pad.bottom);

	const xMin = $derived(Math.min(...xs));
	const xMax = $derived(Math.max(...xs));
	const yMax = $derived(
		niceCeil(Math.max(1, ...series.flatMap((s) => s.values.filter((v) => Number.isFinite(v)))))
	);

	function niceCeil(value: number): number {
		const magnitude = 10 ** Math.floor(Math.log10(value));
		return Math.ceil(value / (magnitude / 2)) * (magnitude / 2);
	}

	const sx = $derived((x: number) => pad.left + ((x - xMin) / (xMax - xMin || 1)) * plotW);
	const sy = $derived((y: number) => pad.top + plotH - (y / yMax) * plotH);

	const path = $derived((values: number[]) =>
		values
			.map((v, i) => `${i === 0 ? 'M' : 'L'}${sx(xs[i] ?? 0).toFixed(1)},${sy(v).toFixed(1)}`)
			.join(' ')
	);

	const yTicks = $derived([0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(yMax * f)));
	const xTicks = $derived(xs.filter((_, i) => i % Math.ceil(xs.length / 9) === 0));

	let hover = $state<number | null>(null);
	const hoverIndex = $derived(hover === null ? null : hover);

	/** The end labels wrap on ` / `; everywhere else the label is one phrase. */
	const flat = (label: string) => label.replace(' / ', ' ');

	function onMove(event: MouseEvent) {
		const svg = event.currentTarget as SVGSVGElement;
		const rect = svg.getBoundingClientRect();
		const x = ((event.clientX - rect.left) / rect.width) * width;
		const value = xMin + ((x - pad.left) / plotW) * (xMax - xMin);
		let best = 0;
		for (let i = 1; i < xs.length; i++) {
			if (Math.abs((xs[i] ?? 0) - value) < Math.abs((xs[best] ?? 0) - value)) best = i;
		}
		hover = best;
	}
</script>

<div class="chart">
	<svg
		viewBox="0 0 {width} {height}"
		role="img"
		aria-label="{yLabel} against {xLabel}, {series.map((s) => flat(s.label)).join(' and ')}."
		onmousemove={onMove}
		onmouseleave={() => (hover = null)}
	>
		<!-- grid -->
		{#each yTicks as tick (tick)}
			<line x1={pad.left} x2={pad.left + plotW} y1={sy(tick)} y2={sy(tick)} class="grid" />
			<text x={pad.left - 8} y={sy(tick)} class="tick y">{tick.toLocaleString('en-US')}</text>
		{/each}

		{#each xTicks as tick (tick)}
			<text x={sx(tick)} y={pad.top + plotH + 18} class="tick x">{tick}</text>
		{/each}

		<line
			x1={pad.left}
			x2={pad.left + plotW}
			y1={pad.top + plotH}
			y2={pad.top + plotH}
			class="axis"
		/>

		{#if marker}
			<line x1={sx(marker.x)} x2={sx(marker.x)} y1={pad.top} y2={pad.top + plotH} class="marker" />
			<text x={sx(marker.x) + 5} y={pad.top + 11} class="marker-label">{marker.label}</text>
		{/if}

		{#if hoverIndex !== null}
			<line
				x1={sx(xs[hoverIndex] ?? 0)}
				x2={sx(xs[hoverIndex] ?? 0)}
				y1={pad.top}
				y2={pad.top + plotH}
				class="crosshair"
			/>
		{/if}

		{#each series as s (s.key)}
			<path d={path(s.values)} class="line" style="stroke: var(--{s.tone})" />
			<!-- direct label at the series end -->
			<text
				x={pad.left + plotW + 9}
				y={sy(s.values[s.values.length - 1] ?? 0)}
				class="series-label"
				style="fill: var(--{s.tone})"
			>
				{#each s.label.split(' / ') as part, li (part)}
					<tspan x={pad.left + plotW + 9} dy={li === 0 ? 0 : 13}>{part}</tspan>
				{/each}
			</text>
			{#if hoverIndex !== null}
				<circle
					cx={sx(xs[hoverIndex] ?? 0)}
					cy={sy(s.values[hoverIndex] ?? 0)}
					r="4.5"
					class="dot"
					style="fill: var(--{s.tone})"
				/>
			{/if}
		{/each}

		<text x={pad.left + plotW / 2} y={height - 3} class="axis-label">{xLabel}</text>
	</svg>

	<p class="readout" role="status">
		{#if hoverIndex !== null}
			<span class="mono">{xs[hoverIndex]} km</span>
			{#each series as s (s.key)}
				<span class="pair">
					<span class="swatch" style="background: var(--{s.tone})"></span>
					{flat(s.label)}:
					<strong class="num">{(s.values[hoverIndex] ?? 0).toLocaleString('en-US')}</strong>
				</span>
			{/each}
		{:else}
			<span class="hint">{yLabel} — hover the chart to read a threshold.</span>
		{/if}
	</p>
</div>

<style>
	.chart {
		border: 1px solid var(--rule);
		background: var(--surface);
		border-radius: 2px;
		padding: 10px 12px 6px;
	}

	svg {
		width: 100%;
		height: auto;
		display: block;
	}

	.grid {
		stroke: var(--rule-soft);
		stroke-width: 1;
	}

	.axis {
		stroke: var(--rule-strong);
		stroke-width: 1;
	}

	.line {
		fill: none;
		stroke-width: 2;
		stroke-linejoin: round;
		stroke-linecap: round;
	}

	.dot {
		stroke: var(--surface);
		stroke-width: 2;
	}

	.tick {
		font-family: var(--mono);
		font-size: 10px;
		fill: var(--ink-muted);
	}

	.tick.y {
		text-anchor: end;
		dominant-baseline: middle;
	}

	.tick.x {
		text-anchor: middle;
	}

	.axis-label {
		font-size: 10.5px;
		fill: var(--ink-muted);
		text-anchor: middle;
		font-family: var(--mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}

	.series-label {
		font-size: 11px;
		dominant-baseline: middle;
		font-weight: 500;
	}

	.marker {
		stroke: var(--ink);
		stroke-width: 1.5;
		stroke-dasharray: 4 3;
	}

	.marker-label {
		font-family: var(--mono);
		font-size: 10px;
		fill: var(--ink);
	}

	.crosshair {
		stroke: var(--ink-muted);
		stroke-width: 1;
	}

	.readout {
		margin: 4px 0 0;
		min-height: 20px;
		font-size: 12px;
		display: flex;
		gap: 16px;
		flex-wrap: wrap;
		align-items: center;
		max-width: none;
		color: var(--ink-soft);
	}

	.pair {
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}

	.swatch {
		width: 9px;
		height: 9px;
		border-radius: 1px;
		display: inline-block;
	}

	.hint {
		color: var(--ink-muted);
	}
</style>
