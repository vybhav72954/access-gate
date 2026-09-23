<!--
	The investigation trail: every tool the agents called, in order.

	Long by design, and collapsed by default — it is the proof the agents did real work, not something
	to read first. A refused call is the tool declining, and it is kept on the record rather than
	filtered out: the officer's action tools execute only what the policy decided.
-->
<script lang="ts">
	import type { ToolCall } from '../types';

	interface Props {
		trail: readonly ToolCall[];
		degraded: boolean;
		/**
		 * The log's own `agent:tool` summary. A case that issues no artefact — a released claim, a
		 * refused one — has no Markdown for the export to parse the full trail out of, but the
		 * decision log recorded every call regardless. Without this the page tells a reader that a
		 * case with sixteen tool calls has none.
		 */
		toolsCalled?: readonly string[];
	}

	let { trail, degraded, toolsCalled = [] }: Props = $props();
	let open = $state(false);

	const refused = $derived(trail.filter((call) => call.refused).length);
	// Only a fallback: whenever the artefact was parsed, `trail` is richer and wins.
	const summary = $derived(
		trail.length === 0
			? toolsCalled.map((entry) => {
					const [agent, tool] = entry.split(':');
					return { agent, tool: tool ?? entry };
				})
			: []
	);
</script>

{#if trail.length === 0 && summary.length > 0}
	<details bind:open>
		<summary>
			<span class="chev" aria-hidden="true">{open ? '▾' : '▸'}</span>
			<span class="count">{summary.length} tool calls</span>
			<span class="hint">{open ? 'hide' : 'show'} the investigation trail</span>
		</summary>
		<p class="note">
			This case issued no artefact, so only the decision log's summary of the trail survives — the
			acting agent and the tool, without the arguments or what came back.
		</p>
		<table class="record trail">
			<caption class="sr-only">Tool calls recorded for this case, in order</caption>
			<thead>
				<tr><th scope="col">#</th><th scope="col">Agent</th><th scope="col">Tool</th></tr>
			</thead>
			<tbody>
				{#each summary as call, i (i)}
					<tr>
						<td class="n">{i + 1}</td>
						<td>{call.agent}</td>
						<td><code>{call.tool}</code></td>
					</tr>
				{/each}
			</tbody>
		</table>
	</details>
{:else if trail.length === 0}
	<p class="void">
		{#if degraded}
			No tool was called. The rules investigated this case from the claim store directly, with no
			agent and so no trail.
		{:else}
			The record lists no tool calls for this case.
		{/if}
	</p>
{:else}
	<details bind:open>
		<summary>
			<span class="chev" aria-hidden="true">{open ? '▾' : '▸'}</span>
			<span class="label">
				{trail.length} tool {trail.length === 1 ? 'call' : 'calls'}
				{#if refused > 0}
					<span class="refused-count">· {refused} refused</span>
				{/if}
			</span>
			<span class="hint">{open ? 'hide' : 'show'} the investigation trail</span>
		</summary>

		<div class="table-scroll">
			<table class="record trail">
				<caption>
					Every tool called on this case, in order. A refused call is the tool declining.
				</caption>
				<thead>
					<tr>
						<th scope="col" class="n">#</th>
						<th scope="col">Agent</th>
						<th scope="col">Tool</th>
						<th scope="col">Arguments</th>
						<th scope="col">Outcome</th>
					</tr>
				</thead>
				<tbody>
					{#each trail as call (call.n)}
						<tr class:refused={call.refused}>
							<td class="n mono">{call.n}</td>
							<td class="agent">{call.agent_title}</td>
							<td><code>{call.tool}</code></td>
							<td class="args">
								{#if call.arguments.length === 0}
									<span class="empty">—</span>
								{:else}
									<ul>
										{#each call.arguments as argument (argument.name)}
											<li>
												<span class="arg-name mono">{argument.name}</span>
												<span class="arg-value">{argument.value}</span>
											</li>
										{/each}
									</ul>
								{/if}
							</td>
							<td class="outcome">
								{#if call.refused}
									<span class="flag">refused</span>
								{/if}
								{call.outcome}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
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

	details {
		border: 1px solid var(--rule);
		border-radius: 2px;
		background: var(--surface);
	}

	summary {
		display: flex;
		align-items: baseline;
		gap: 9px;
		padding: 10px 14px;
		cursor: pointer;
		list-style: none;
		font-size: 13.5px;
	}

	summary::-webkit-details-marker {
		display: none;
	}

	summary:hover {
		background: var(--surface-2);
	}

	.chev {
		color: var(--ink-muted);
		font-size: 11px;
	}

	.label {
		font-weight: 500;
	}

	.refused-count {
		color: var(--gate-unknown);
		font-weight: 400;
	}

	.hint {
		margin-left: auto;
		font-size: 11.5px;
		color: var(--ink-muted);
	}

	.table-scroll {
		overflow-x: auto;
		border-top: 1px solid var(--rule);
	}

	.trail {
		font-size: 12.5px;
	}

	.trail caption {
		padding: 9px 14px 6px;
	}

	.trail td,
	.trail th {
		padding-left: 14px;
	}

	.agent {
		white-space: nowrap;
		color: var(--ink-soft);
	}

	.args ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 1px;
	}

	.arg-name {
		color: var(--ink-muted);
		font-size: 11px;
	}

	.arg-name::after {
		content: '=';
		color: var(--ink-faint);
	}

	.arg-value {
		word-break: break-word;
	}

	.outcome {
		max-width: 52ch;
		color: var(--ink-soft);
	}

	tr.refused {
		background: var(--gate-unknown-wash);
	}

	.flag {
		display: inline-block;
		font-family: var(--mono);
		font-size: 10px;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		border: 1px solid var(--gate-unknown);
		color: var(--gate-unknown);
		padding: 0 4px;
		border-radius: 2px;
		margin-right: 6px;
	}

	.empty {
		color: var(--ink-faint);
	}
</style>
