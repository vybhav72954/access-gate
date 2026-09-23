<!--
	The decided action, with its group.

	Colour is never the only carrier: each group also has a shape mark and its name in words, so the
	badge survives a grayscale screen recording and a projector.
-->
<script lang="ts">
	import { ACTION_LABELS, groupSpec } from '../domain';
	import type { ActionGroup, ActionKey } from '../types';

	interface Props {
		action: ActionKey;
		group: ActionGroup;
		size?: 'sm' | 'md' | 'lg';
		/** Show the group name beside the action. */
		showGroup?: boolean;
	}

	let { action, group, size = 'md', showGroup = false }: Props = $props();
	const spec = $derived(groupSpec(group));
</script>

<span class="badge {size}" data-group={group}>
	<span class="mark" aria-hidden="true">{spec.mark}</span>
	<span class="label">{ACTION_LABELS[action]}</span>
	{#if showGroup}
		<span class="group mono">{spec.label}</span>
	{/if}
</span>

<style>
	.badge {
		display: inline-flex;
		align-items: baseline;
		gap: 6px;
		padding: 2px 8px 2px 6px;
		border: 1px solid var(--edge, var(--rule));
		border-left-width: 3px;
		border-radius: 2px;
		background: var(--wash, var(--surface-2));
		color: var(--ink);
		line-height: 1.35;
		white-space: nowrap;
		max-width: 100%;
	}

	@media (max-width: 480px) {
		.badge {
			white-space: normal;
		}
	}

	.badge[data-group='released'] {
		--edge: var(--released);
		--wash: var(--released-wash);
	}
	.badge[data-group='deferred'] {
		--edge: var(--deferred);
		--wash: var(--deferred-wash);
	}
	.badge[data-group='enforced'] {
		--edge: var(--enforced);
		--wash: var(--enforced-wash);
	}
	.badge[data-group='refused'] {
		--edge: var(--refused);
		--wash: var(--refused-wash);
	}

	.mark {
		color: var(--edge);
		font-size: 0.85em;
		align-self: center;
	}

	.label {
		font-weight: 500;
		font-size: 13px;
	}

	.group {
		font-size: 10px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	.sm .label {
		font-size: 12px;
	}
	.sm {
		padding: 1px 6px 1px 5px;
	}

	.lg {
		padding: 6px 14px 6px 11px;
		border-left-width: 4px;
	}
	.lg .label {
		font-size: 20px;
		font-weight: 600;
		letter-spacing: -0.015em;
	}
	.lg .group {
		font-size: 11px;
	}
</style>
