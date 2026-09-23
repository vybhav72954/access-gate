<!--
	Confidence against the 0.70 floor.

	A bare "0.46" means nothing. The floor is drawn on the same track as the value, so "below the
	floor" is visible rather than inferred by arithmetic, and the accessible text always says both
	numbers. The value is read from the record; nothing here recomputes it or re-applies the floor.
-->
<script lang="ts">
	import { formatConfidence, readConfidence } from '../domain';

	interface Props {
		value: number;
		floor?: number;
		size?: 'sm' | 'md' | 'lg';
		/** Hide the caption when the surrounding layout already carries it. */
		caption?: boolean;
	}

	let { value, floor = undefined, size = 'md', caption = true }: Props = $props();

	const reading = $derived(
		floor === undefined ? readConfidence(value) : readConfidence(value, floor)
	);
</script>

<div class="confidence {size}" class:below={reading.belowFloor}>
	<div class="row">
		<span class="value mono" aria-hidden="true">{formatConfidence(reading.value)}</span>
		<div
			class="track"
			role="img"
			aria-label="Confidence {reading.text}."
			style="--fraction: {reading.fraction}; --floor: {reading.floorFraction}"
		>
			<div class="fill"></div>
			<div class="floor-line"></div>
		</div>
	</div>
	{#if caption}
		<p class="caption">
			{#if reading.belowFloor}
				<span class="mark" aria-hidden="true">▾</span> below the
				<span class="mono">{formatConfidence(reading.floor)}</span> floor
			{:else}
				at or above the <span class="mono">{formatConfidence(reading.floor)}</span> floor
			{/if}
		</p>
	{/if}
</div>

<style>
	.confidence {
		display: flex;
		flex-direction: column;
		gap: 3px;
		min-width: 0;
	}

	.row {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.value {
		font-weight: 500;
		font-variant-numeric: tabular-nums;
		color: var(--ink);
		letter-spacing: -0.02em;
	}

	.track {
		position: relative;
		flex: 1;
		min-width: 46px;
		height: 8px;
		overflow: visible;
		background: var(--surface-3);
		border: 1px solid var(--rule);
		border-radius: 1px;
	}

	.fill {
		position: absolute;
		inset: 0 auto 0 0;
		width: calc(var(--fraction) * 100%);
		background: var(--ink-soft);
	}

	/* The floor is a hard edge across the track, not a gradient: it is a threshold, not a mood. */
	.floor-line {
		position: absolute;
		top: -4px;
		bottom: -4px;
		left: calc(var(--floor) * 100%);
		width: 2px;
		margin-left: -1px;
		background: var(--ink);
	}

	.floor-line::after {
		content: '';
		position: absolute;
		inset: 0 auto 0 2px;
		width: 3px;
		background: repeating-linear-gradient(135deg, var(--rule-strong) 0 1px, transparent 1px 3px);
	}

	.below .fill {
		background: var(--deferred);
	}

	.caption {
		margin: 0;
		font-size: 11.5px;
		color: var(--ink-muted);
		line-height: 1.3;
	}

	.below .caption {
		color: var(--deferred);
		font-weight: 500;
	}

	.mark {
		font-size: 10px;
	}

	.sm .value {
		font-size: 12px;
	}
	.sm .track {
		height: 6px;
		min-width: 38px;
	}

	.lg {
		gap: 5px;
	}
	.lg .value {
		font-size: 26px;
		line-height: 1;
	}
	.lg .track {
		height: 12px;
	}
	.lg .caption {
		font-size: 13px;
	}
</style>
