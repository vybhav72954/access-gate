<!--
	One horizontal bar with its value printed. Used for the ablation, where the point is a single
	magnitude falling as each safeguard is added — one measure, one hue, no legend needed.
-->
<script lang="ts">
	interface Props {
		label: string;
		value: number;
		max: number;
		display?: string;
		tone?: 'series-1' | 'series-2' | 'ink';
		note?: string | null;
	}

	let { label, value, max, display, tone = 'series-1', note = null }: Props = $props();
	const fraction = $derived(max > 0 ? Math.max(0, Math.min(value / max, 1)) : 0);
</script>

<div class="row">
	<p class="label">{label}</p>
	<div class="track">
		<div
			class="fill"
			data-tone={tone}
			style="width: {(fraction * 100).toFixed(2)}%"
			role="img"
			aria-label="{label}: {display ?? value}"
		></div>
		<span class="value num">{display ?? value.toLocaleString('en-US')}</span>
	</div>
	{#if note}
		<p class="note">{note}</p>
	{/if}
</div>

<style>
	.row {
		display: grid;
		gap: 3px;
		padding: 9px 0;
		border-bottom: 1px solid var(--rule-soft);
	}

	.label {
		margin: 0;
		font-size: 13px;
		max-width: 80ch;
	}

	.track {
		display: flex;
		align-items: center;
		gap: 8px;
		height: 16px;
	}

	.fill {
		height: 12px;
		min-width: 2px;
		border-radius: 0 2px 2px 0;
		background: var(--fill, var(--series-1));
	}

	.fill[data-tone='series-1'] {
		--fill: var(--series-1);
	}
	.fill[data-tone='series-2'] {
		--fill: var(--series-2);
	}
	.fill[data-tone='ink'] {
		--fill: var(--ink-soft);
	}

	.value {
		font-size: 12.5px;
		font-weight: 600;
		color: var(--ink);
		white-space: nowrap;
	}

	.note {
		margin: 0;
		font-size: 11.5px;
		color: var(--ink-muted);
		max-width: 80ch;
	}
</style>
