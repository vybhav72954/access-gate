<!--
	One reading, with what it rests on.

	Three states, and they are not the same thing:
	  · stands           — the reading counted towards the aggregate.
	  · disputed         — the Audit Reviewer set it aside; it now weighs nothing.
	  · dispute refused  — the reviewer tried and was overruled, because the reading repeats a
	                       measurement the claim store makes itself. The reading still counts.
-->
<script lang="ts">
	import Confidence from './Confidence.svelte';
	import { STANCE_LABELS, STANCE_MARKS, findingState } from '../domain';
	import type { Finding } from '../types';

	interface Props {
		finding: Finding;
		reviewReason?: string | null;
	}

	let { finding, reviewReason = null }: Props = $props();
	const state = $derived(findingState(finding));
</script>

<article class="finding" data-state={state} data-stance={finding.stance}>
	<header>
		<div class="who">
			<h3>
				{finding.reading_title}
				{#if finding.channel}
					<span class="channel ident">{finding.channel}</span>
				{/if}
			</h3>
			{#if finding.agent_title}
				<p class="agent">{finding.agent_title}</p>
			{/if}
		</div>

		<div class="verdict">
			<span class="stance">
				<span class="stance-mark" aria-hidden="true">{STANCE_MARKS[finding.stance]}</span>
				{STANCE_LABELS[finding.stance]}
			</span>
			<Confidence value={finding.confidence} size="sm" caption={false} />
		</div>
	</header>

	<p class="conclusion">{finding.conclusion}</p>

	{#if finding.citation}
		<p class="citation">
			<span class="eyebrow">Rests on</span>
			<span class="ident">{finding.citation}</span>
		</p>
	{/if}

	{#if state === 'disputed'}
		<div class="note disputed">
			<p class="note-title">Disputed by the Audit Reviewer — set aside</p>
			<p>
				This reading weighs nothing in the decision. The reviewer can send a case to more evidence
				or to a human; it can never convict or clear one.
			</p>
			{#if reviewReason}
				<p class="quote">{reviewReason}</p>
			{/if}
		</div>
	{:else if state === 'dispute-refused'}
		<div class="note refused">
			<p class="note-title">Dispute refused — the reading stands</p>
			<p>
				The Audit Reviewer disputed this reading and was overruled. It repeats a measurement the
				claim store makes itself, and a dispute may set aside a reading, never a measurement.
			</p>
		</div>
	{/if}
</article>

<style>
	.finding {
		border: 1px solid var(--rule);
		border-left: 3px solid var(--tone, var(--rule-strong));
		border-radius: 2px;
		background: var(--surface);
		padding: 12px 14px;
	}

	.finding[data-stance='supports'] {
		--tone: var(--enforced);
	}
	.finding[data-stance='opposes'] {
		--tone: var(--released);
	}
	.finding[data-stance='inconclusive'] {
		--tone: var(--rule-strong);
	}

	header {
		display: flex;
		gap: 16px;
		align-items: flex-start;
		justify-content: space-between;
		flex-wrap: wrap;
	}

	h3 {
		font-size: 14px;
		display: flex;
		gap: 7px;
		align-items: baseline;
		flex-wrap: wrap;
	}

	.channel {
		font-size: 11px;
		color: var(--ink-muted);
		border: 1px solid var(--rule);
		padding: 0 4px;
		border-radius: 2px;
		font-weight: 400;
	}

	.agent {
		margin: 1px 0 0;
		font-size: 11.5px;
		color: var(--ink-muted);
	}

	.verdict {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-shrink: 0;
	}

	.stance {
		display: inline-flex;
		align-items: baseline;
		gap: 5px;
		font-size: 12px;
		font-weight: 500;
		color: var(--ink-soft);
		white-space: nowrap;
	}

	.stance-mark {
		color: var(--tone);
		font-size: 13px;
	}

	.conclusion {
		margin: 10px 0 0;
		font-size: 13.5px;
		line-height: 1.55;
		max-width: 76ch;
	}

	.citation {
		margin: 8px 0 0;
		display: flex;
		align-items: baseline;
		gap: 8px;
		flex-wrap: wrap;
		font-size: 12.5px;
		color: var(--ink-soft);
	}

	/* A set-aside reading is struck through: it is on the record and it weighs nothing. */
	.finding[data-state='disputed'] {
		--tone: var(--ink-faint);
		background: var(--surface-2);
	}

	.finding[data-state='disputed'] .conclusion,
	.finding[data-state='disputed'] h3 {
		text-decoration: line-through;
		text-decoration-color: var(--ink-faint);
		color: var(--ink-muted);
	}

	.note {
		margin-top: 11px;
		padding: 9px 11px;
		border: 1px solid var(--rule);
		border-radius: 2px;
		font-size: 12.5px;
		background: var(--surface);
	}

	.note p {
		margin: 0;
		color: var(--ink-soft);
		max-width: 72ch;
	}

	.note-title {
		font-weight: 600;
		color: var(--ink) !important;
		margin-bottom: 3px !important;
	}

	.note.disputed {
		border-left: 3px solid var(--ink-muted);
	}

	.note.refused {
		border-left: 3px solid var(--gate-unknown);
		background: var(--gate-unknown-wash);
	}

	.quote {
		margin-top: 6px !important;
		font-style: italic;
		border-left: 2px solid var(--rule-strong);
		padding-left: 9px;
	}

	@media (max-width: 720px) {
		.verdict {
			width: 100%;
			justify-content: space-between;
		}
	}
</style>
