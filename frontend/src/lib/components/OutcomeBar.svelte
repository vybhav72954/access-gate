<!--
	Benchmark outcomes for one truth, as a stacked bar.

	Scoring is asymmetric and the bar has to say so. For an innocent claim: released is correct,
	deferred is costly, enforced is wrong. For a fraud: enforced is correct, deferred is acceptable,
	released is wrong. So "costly" and "acceptable" are the same decision — the case was deferred —
	and differ only in what deferring cost. They share a hue and separate by label and by texture;
	"wrong" never shares a hue with either.

	Every segment carries its count as a direct label and a 2px gap, which is the secondary encoding
	the palette requires: identity is never left to colour.
-->
<script lang="ts">
	import type { OutcomeSplit } from '../types';

	interface Props {
		split: OutcomeSplit;
		truth: 'innocent' | 'fraud';
		/** Scale every bar to the largest n in the group, so widths compare across rows. */
		scaleTo?: number;
		compact?: boolean;
	}

	let { split, truth, scaleTo, compact = false }: Props = $props();

	interface Segment {
		key: 'correct' | 'deferred' | 'wrong';
		label: string;
		count: number;
		tone: string;
		hatched: boolean;
	}

	const side = $derived(truth === 'innocent' ? split.innocent : split.fraud);

	const segments = $derived<Segment[]>(
		truth === 'innocent'
			? [
					{
						key: 'correct',
						label: 'released — correct',
						count: split.innocent.correct,
						tone: 'correct',
						hatched: false
					},
					{
						key: 'deferred',
						label: 'deferred — costly',
						count: split.innocent.costly,
						tone: 'deferred',
						hatched: true
					},
					{
						key: 'wrong',
						label: 'enforced — wrong',
						count: split.innocent.wrong,
						tone: 'wrong',
						hatched: false
					}
				]
			: [
					{
						key: 'correct',
						label: 'enforced — correct',
						count: split.fraud.correct,
						tone: 'correct',
						hatched: false
					},
					{
						key: 'deferred',
						label: 'deferred — acceptable',
						count: split.fraud.acceptable,
						tone: 'deferred',
						hatched: false
					},
					{
						key: 'wrong',
						label: 'released — wrong',
						count: split.fraud.wrong,
						tone: 'wrong',
						hatched: false
					}
				]
	);

	const total = $derived(side.n);
	const scale = $derived(scaleTo && scaleTo > 0 ? scaleTo : total || 1);
</script>

<div class="outcome" class:compact>
	<div
		class="bar"
		role="img"
		aria-label="{truth === 'innocent' ? 'Innocent' : 'Fraud'} cases, {total} in total: {segments
			.map((s) => `${s.count} ${s.label}`)
			.join(', ')}."
		style="--width: {total > 0 ? (total / scale) * 100 : 0}%"
	>
		{#each segments as segment (segment.key)}
			{#if segment.count > 0}
				<div
					class="seg"
					data-tone={segment.tone}
					class:hatched={segment.hatched}
					style="flex-grow: {segment.count}"
					title="{segment.count} — {segment.label}"
				>
					<span class="seg-count num">{segment.count}</span>
				</div>
			{/if}
		{/each}
		{#if total === 0}
			<div class="seg none"><span class="seg-count">0</span></div>
		{/if}
	</div>

	{#if !compact}
		<ul class="legend">
			{#each segments as segment (segment.key)}
				<li>
					<span class="swatch" data-tone={segment.tone} class:hatched={segment.hatched}></span>
					<span class="legend-label">{segment.label}</span>
					<span class="legend-count num">{segment.count}</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.outcome {
		display: grid;
		gap: 6px;
		min-width: 0;
	}

	.bar {
		display: flex;
		width: var(--width);
		min-width: 2px;
		height: 22px;
		/* The 2px surface gap between fills is part of the encoding, not decoration. */
		gap: 2px;
		background: var(--surface);
	}

	.compact .bar {
		height: 15px;
	}

	.seg {
		position: relative;
		min-width: 3px;
		display: flex;
		align-items: center;
		justify-content: center;
		background: var(--fill, var(--surface-3));
		border-radius: 0 2px 2px 0;
		overflow: hidden;
	}

	.seg:first-child {
		border-radius: 2px 0 0 2px;
	}

	.seg:only-child {
		border-radius: 2px;
	}

	.seg[data-tone='correct'] {
		--fill: var(--outcome-correct);
	}
	.seg[data-tone='deferred'] {
		--fill: var(--outcome-deferred);
	}
	.seg[data-tone='wrong'] {
		--fill: var(--outcome-wrong);
	}

	/* Texture separates "costly" from "acceptable": the same decision, a different price. */
	.seg.hatched {
		background-image: repeating-linear-gradient(
			45deg,
			rgba(0, 0, 0, 0.32) 0 2px,
			transparent 2px 6px
		);
	}

	.seg.none {
		background: var(--surface-2);
		border: 1px dashed var(--rule);
		flex-grow: 1;
	}

	.seg-count {
		font-size: 11px;
		font-weight: 600;
		color: #fff;
		text-shadow: 0 0 2px rgba(0, 0, 0, 0.45);
		padding: 0 4px;
		line-height: 1;
	}

	.seg.none .seg-count {
		color: var(--ink-faint);
		text-shadow: none;
	}

	.compact .seg-count {
		font-size: 10px;
	}

	.legend {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		gap: 14px;
		flex-wrap: wrap;
	}

	.legend li {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font-size: 11.5px;
		color: var(--ink-muted);
	}

	.swatch {
		width: 10px;
		height: 10px;
		border-radius: 1px;
		background: var(--fill, var(--surface-3));
		flex: none;
	}

	.swatch[data-tone='correct'] {
		--fill: var(--outcome-correct);
	}
	.swatch[data-tone='deferred'] {
		--fill: var(--outcome-deferred);
	}
	.swatch[data-tone='wrong'] {
		--fill: var(--outcome-wrong);
	}

	.swatch.hatched {
		background-image: repeating-linear-gradient(
			45deg,
			rgba(0, 0, 0, 0.32) 0 2px,
			transparent 2px 6px
		);
	}

	.legend-count {
		color: var(--ink);
		font-weight: 500;
	}
</style>
