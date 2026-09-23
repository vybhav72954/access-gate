<!--
	Degraded: the model was unavailable and the deterministic rules decided alone.

	A degraded case must never be presented as the crew's work, so the mark is unmistakable and it
	appears everywhere the case does.
-->
<script lang="ts">
	interface Props {
		compact?: boolean;
	}
	let { compact = false }: Props = $props();
</script>

<span class="degraded" class:compact title="Decided by the rules alone — the model was unavailable">
	<span class="glyph" aria-hidden="true">⚑</span>
	<span class="word">Degraded</span>
</span>

<style>
	.degraded {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		border: 1px solid var(--ink-muted);
		border-radius: 2px;
		padding: 0 5px;
		background: repeating-linear-gradient(135deg, var(--surface-2) 0 4px, var(--surface-3) 4px 8px);
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--ink-soft);
		white-space: nowrap;
	}

	.glyph {
		font-size: 9px;
	}

	.compact .word {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
	}

	.compact {
		padding: 0 3px;
	}
</style>
