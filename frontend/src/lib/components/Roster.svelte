<!--
	The nine agents on a case.

	Two things this has to make visible. The Router may only widen: the rules compute a mandatory set
	from the trigger, and the Router may add to it and never take from it — so mandatory and
	router-opened are distinguished, with the Router's stated reason. And agents that did not run stay
	on the list, greyed: that a Medical Auditor was not needed is information, not an absence to hide.
-->
<script lang="ts">
	import { splitRoster } from '../domain';
	import type { RosterEntry } from '../types';

	interface Props {
		roster: readonly RosterEntry[];
		degraded: boolean;
	}

	let { roster, degraded }: Props = $props();
	const split = $derived(splitRoster(roster));
</script>

{#if degraded}
	<p class="void">
		No agent worked this case. The model was unavailable, so the deterministic rules investigated
		and the policy decided on their findings. There is no roster to show — not a crew that did
		nothing.
	</p>
{:else if split.worked.length === 0}
	<p class="void">The record lists no agents for this case.</p>
{:else}
	<p class="legend">
		<span class="key"><span class="chip mandatory" aria-hidden="true"></span> opened by rule</span>
		<span class="key"
			><span class="chip opened" aria-hidden="true"></span> opened by the Router</span
		>
		<span class="key"><span class="chip idle" aria-hidden="true"></span> did not run</span>
	</p>

	<ol class="worked">
		{#each split.worked as agent, index (agent.key)}
			<li class:opened={agent.opened_by_router}>
				<span class="seq mono">{index + 1}</span>
				<div class="body">
					<h3>
						{agent.title}
						{#if agent.opened_by_router}
							<span class="tag">opened by the Router</span>
						{:else}
							<span class="tag rule">mandatory</span>
						{/if}
					</h3>
					<p class="owns">{agent.owns}</p>
					{#if agent.router_reason}
						<p class="reason">
							<span class="eyebrow">Router’s reason</span>
							{agent.router_reason}
						</p>
					{/if}
				</div>
			</li>
		{/each}
	</ol>

	{#if split.absent.length > 0}
		<div class="absent">
			<p class="eyebrow">Did not run on this case</p>
			<ul>
				{#each split.absent as agent (agent.key)}
					<li><span class="name">{agent.title}</span><span class="owns">{agent.owns}</span></li>
				{/each}
			</ul>
		</div>
	{/if}
{/if}

<style>
	.void {
		border: 1px dashed var(--rule-strong);
		background: var(--surface-2);
		padding: 14px 16px;
		color: var(--ink-soft);
		font-size: 13.5px;
		margin: 0;
		max-width: none;
	}

	.legend {
		display: flex;
		gap: 16px;
		flex-wrap: wrap;
		margin: 0 0 12px;
		font-size: 11.5px;
		color: var(--ink-muted);
		max-width: none;
	}

	.key {
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}

	.chip {
		width: 10px;
		height: 10px;
		border: 1px solid var(--rule-strong);
		display: inline-block;
	}

	.chip.mandatory {
		background: var(--ink-soft);
		border-color: var(--ink-soft);
	}

	.chip.opened {
		background: repeating-linear-gradient(45deg, var(--ink-soft) 0 2px, transparent 2px 4px);
		border-color: var(--ink-soft);
	}

	.chip.idle {
		background: var(--surface-2);
	}

	.worked {
		list-style: none;
		margin: 0;
		padding: 0;
		border-top: 1px solid var(--rule);
	}

	.worked li {
		display: flex;
		gap: 12px;
		padding: 10px 0 10px 10px;
		border-bottom: 1px solid var(--rule-soft);
		border-left: 3px solid var(--ink-soft);
	}

	.worked li.opened {
		border-left: 3px solid transparent;
		border-image: repeating-linear-gradient(45deg, var(--ink-soft) 0 3px, var(--surface) 3px 6px) 1;
		background: var(--surface-2);
	}

	.seq {
		font-size: 11px;
		color: var(--ink-muted);
		padding-top: 2px;
		min-width: 1.2em;
	}

	.body {
		min-width: 0;
	}

	h3 {
		font-size: 14px;
		font-weight: 600;
		display: flex;
		align-items: baseline;
		gap: 8px;
		flex-wrap: wrap;
	}

	.tag {
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--ink-muted);
		border: 1px solid var(--rule);
		padding: 0 4px;
		border-radius: 2px;
		font-weight: 400;
	}

	.tag.rule {
		border-style: solid;
		color: var(--ink-faint);
	}

	.owns {
		margin: 2px 0 0;
		font-size: 12.5px;
		color: var(--ink-muted);
		max-width: 70ch;
	}

	.reason {
		margin: 6px 0 0;
		font-size: 13px;
		color: var(--ink-soft);
		border-left: 2px solid var(--rule-strong);
		padding-left: 10px;
		max-width: 70ch;
	}

	.reason .eyebrow {
		display: block;
		margin-bottom: 1px;
	}

	.absent {
		margin-top: 14px;
		padding-top: 10px;
		border-top: 1px solid var(--rule);
	}

	.absent ul {
		list-style: none;
		margin: 6px 0 0;
		padding: 0;
		display: grid;
		gap: 2px;
	}

	.absent li {
		display: flex;
		gap: 10px;
		align-items: baseline;
		font-size: 12.5px;
		color: var(--ink-faint);
		padding: 2px 0 2px 10px;
		border-left: 3px solid var(--surface-3);
	}

	.absent .name {
		min-width: 22ch;
		text-decoration: line-through;
		text-decoration-color: var(--rule-strong);
	}

	.absent .owns {
		margin: 0;
		color: var(--ink-faint);
		font-size: 12px;
	}

	@media (max-width: 720px) {
		.absent li {
			flex-direction: column;
			gap: 0;
		}
		.absent .name {
			min-width: 0;
		}
	}
</style>
