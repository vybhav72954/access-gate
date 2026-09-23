<!--
	The access gate, as a compact badge.

	A null gate is not `clear`: the gate is only computed for actions that would remove a provider,
	so a case that never reached it renders as "not computed", never as a clear pass.
-->
<script lang="ts">
	import { gateSpec } from '../domain';
	import type { GateState } from '../types';

	interface Props {
		gate: GateState | null;
		size?: 'sm' | 'md';
	}

	let { gate, size = 'md' }: Props = $props();
	const spec = $derived(gateSpec(gate));
</script>

{#if spec}
	<span class="gate {size}" data-gate={spec.key} class:emphatic={spec.emphatic}>
		<span class="mark" aria-hidden="true">{spec.mark}</span>
		<span class="label">{spec.label}</span>
	</span>
{:else}
	<span class="gate {size} none">
		<span class="mark" aria-hidden="true">·</span>
		<span class="label">Not computed</span>
	</span>
{/if}

<style>
	.gate {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 1px 7px;
		border: 1px solid var(--edge, var(--rule));
		border-radius: 2px;
		background: var(--wash, transparent);
		white-space: nowrap;
	}

	.gate[data-gate='clear'] {
		--edge: var(--rule-strong);
		--wash: var(--gate-clear-wash);
		--tone: var(--gate-clear);
	}
	.gate[data-gate='protect'] {
		--edge: var(--gate-protect-edge);
		--wash: var(--gate-protect-wash);
		--tone: var(--gate-protect);
	}
	.gate[data-gate='phantom'] {
		--edge: var(--gate-phantom);
		--wash: var(--gate-phantom-wash);
		--tone: var(--gate-phantom);
	}
	.gate[data-gate='unknown'] {
		--edge: var(--gate-unknown);
		--wash: var(--gate-unknown-wash);
		--tone: var(--gate-unknown);
	}

	.none {
		--edge: var(--rule);
		--tone: var(--ink-faint);
		border-style: dashed;
	}

	.mark {
		color: var(--tone, var(--ink-faint));
		font-size: 11px;
		line-height: 1;
	}

	.label {
		font-family: var(--mono);
		font-size: 11px;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--ink);
	}

	.none .label {
		color: var(--ink-faint);
		text-transform: none;
		letter-spacing: 0;
	}

	.emphatic {
		border-width: 1px;
		box-shadow: inset 0 0 0 1px var(--edge);
	}

	.emphatic .label {
		font-weight: 500;
	}

	.sm .label {
		font-size: 10px;
	}
</style>
