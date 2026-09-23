<script lang="ts">
	import { base } from '$app/paths';
	import AccessGate from '$lib/components/AccessGate.svelte';
	import ActionBadge from '$lib/components/ActionBadge.svelte';
	import Artefact from '$lib/components/Artefact.svelte';
	import Confidence from '$lib/components/Confidence.svelte';
	import DegradedMark from '$lib/components/DegradedMark.svelte';
	import FindingCard from '$lib/components/FindingCard.svelte';
	import GateBadge from '$lib/components/GateBadge.svelte';
	import Roster from '$lib/components/Roster.svelte';
	import Trail from '$lib/components/Trail.svelte';
	import {
		ACTION_LABELS,
		formatInt,
		formatTimestamp,
		gateSpec,
		groupSpec,
		recordSections
	} from '$lib/domain';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const c = $derived(data.case);
	const gate = $derived(gateSpec(c.gate));
	const hasCrew = $derived(!c.degraded && c.agents.length > 0);

	// ── the index beside the record ──────────────────────────────────────────
	// A case record is eight sections long and a reader arriving from the list is usually after one
	// of them. `recordSections` decides which are on the page; this file only draws them.
	const sections = $derived(recordSections(c));

	let here = $state<string | null>(null);

	$effect(() => {
		// Which section is being read. `rootMargin` pulls the trip line down to a quarter of the
		// viewport, so a heading counts as current once it is actually up near the top rather than
		// the moment its first pixel appears.
		const ids = sections.map((s) => s.id);
		const seen = new Set<string>();
		const observer = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) {
					if (entry.isIntersecting) seen.add(entry.target.id);
					else seen.delete(entry.target.id);
				}
				here = ids.find((id) => seen.has(id)) ?? here;
			},
			{ rootMargin: '-25% 0px -60% 0px' }
		);
		for (const id of ids) {
			const node = document.getElementById(id);
			if (node) observer.observe(node);
		}
		return () => observer.disconnect();
	});
</script>

<svelte:head>
	<title>{c.claim_id} — {c.action_label} — The Access Gate</title>
</svelte:head>

<div class="hero" class:gate-emphatic={gate?.emphatic ?? false}>
	<div class="wrap hero-in">
		<nav class="breadcrumb" aria-label="Breadcrumb">
			<a href="{base}/">Cases</a>
			<span aria-hidden="true">/</span>
			<span class="ident current">{c.claim_id}</span>
		</nav>

		<!-- 1 · Header: what was flagged, and what was decided. -->
		<header class="case-header">
			<!--
			The record's own heading. It is set visually by the claim id and the action badge below, which
			are not headings, so without this the page had none at all and its seven h2s hung off nothing.
			Hidden rather than shown, the same way the access-gate section names itself.
		-->
			<h1 class="sr-only">
				Claim {c.claim_id}, hospital {c.hospital_ref} — {ACTION_LABELS[c.action]}
			</h1>
			<div class="ids">
				<div>
					<p class="eyebrow">Claim</p>
					<p class="ident big">{c.claim_id}</p>
				</div>
				<div>
					<p class="eyebrow">Hospital</p>
					<p class="ident big">{c.hospital_ref}</p>
				</div>
				<div>
					<p class="eyebrow">Case</p>
					<p class="ident">{c.case_id}</p>
				</div>
				<div>
					<p class="eyebrow">Decided</p>
					<p class="mono">{formatTimestamp(c.decided_ts)}</p>
				</div>
			</div>

			<div class="decision">
				<div class="decided">
					<p class="eyebrow">Decided action</p>
					<ActionBadge action={c.action} group={c.action_group} size="lg" showGroup />
					<p class="group-note">{groupSpec(c.action_group).note}</p>
				</div>

				<div class="conf-block">
					<p class="eyebrow">Confidence</p>
					<Confidence value={c.confidence} size="lg" />
				</div>

				<div class="gate-block">
					<p class="eyebrow">Access gate</p>
					<GateBadge gate={c.gate} />
				</div>
			</div>

			<div class="flags">
				<span class="flag" data-on={c.claim_withheld}>
					{c.claim_withheld ? 'Claim withheld' : 'Claim not withheld'}
				</span>
				<span class="flag" data-on={c.human_required}>
					{c.human_required ? 'A human must decide' : 'No human required'}
				</span>
				<span class="flag mono">acted by {c.acted_by ?? '—'}</span>
				{#if c.model}
					<span class="flag mono">{c.model}</span>
				{/if}
			</div>

			{#if c.degraded}
				<div class="degraded-banner">
					<DegradedMark />
					<p>
						<strong
							>The model was unavailable; the deterministic rules decided this case alone.</strong
						>
						No agent worked it, so there is no roster, no dispute, no hospital’s side and no investigation
						trail. The decision itself is unaffected — the policy decides identically on both paths —
						but nothing below may be read as the crew’s work.
					</p>
				</div>
			{/if}
		</header>
	</div>
</div>

<div class="wrap case-body">
	<!--
		The index rides alongside the record rather than above it: a case is eight sections long and
		the reader arriving from the list usually wants one of them. It is a plain list of links, so
		it works with no JavaScript; only the "you are here" mark needs hydration.
	-->
	<nav class="index" aria-label="Sections of this record">
		<p class="index-label">This record</p>
		<ol>
			{#each sections as section (section.id)}
				<li>
					<a
						href="#{section.id}"
						class:on={here === section.id}
						class:emphatic={section.emphatic}
						aria-current={here === section.id ? 'true' : undefined}
					>
						<span class="index-mark" aria-hidden="true">{section.mark}</span>
						<span>{section.label}</span>
					</a>
				</li>
			{/each}
		</ol>
	</nav>

	<div class="record-col">
		<!-- The trigger. `refuse_malformed` has none, and that is the point of it. -->
		<section class="block" aria-labelledby="trigger-h">
			<h2 id="trigger-h"><span class="num-mark" aria-hidden="true">1</span> What was flagged</h2>
			{#if c.trigger && c.trigger.trigger_id}
				<div class="trigger-card">
					<p class="trigger-line">
						<span class="ident tid">{c.trigger.trigger_id}</span>
						<span class="tname">{c.trigger.name ?? 'unnamed trigger'}</span>
						{#if c.trigger.severity !== null}
							<span class="sev">severity {c.trigger.severity}</span>
						{/if}
					</p>
					{#if c.trigger.evidence}
						<p class="evidence">{c.trigger.evidence}.</p>
					{/if}
					{#if c.trigger.source}
						<p class="source">{c.trigger.source}</p>
					{/if}
					{#if c.triggers_fired.length > 1}
						<p class="also">
							<span class="eyebrow">Also fired</span>
							{#each c.triggers_fired.filter((t) => t !== c.trigger_id) as t (t)}
								<span class="ident chip">{t}</span>
							{/each}
						</p>
					{/if}
				</div>

				{#if c.package}
					<dl class="package">
						<div>
							<dt>Package</dt>
							<dd><span class="ident">{c.package.code}</span> {c.package.name}</dd>
						</div>
						<div>
							<dt>Published rate</dt>
							<dd>{c.package.published_rate}</dd>
						</div>
						<div>
							<dt>Claimed</dt>
							<dd class="num">Rs {formatInt(c.package.claimed_rs)}</dd>
						</div>
					</dl>
				{/if}
			{:else}
				<p class="void">
					No trigger fired on this claim. It was refused before any check could run —
					{#each c.reason_codes as code, i (code)}<span class="ident chip">{code}</span
						>{#if i < c.reason_codes.length - 1}{' '}{/if}{/each}
					— so nothing was inferred about the hospital.
				</p>
			{/if}

			{#if c.reason_codes.length > 0 && c.trigger?.trigger_id}
				<p class="codes">
					<span class="eyebrow">Reason codes</span>
					{#each c.reason_codes as code (code)}
						<span class="ident chip">{code}</span>
					{/each}
				</p>
			{/if}
		</section>

		<!-- 2 · The roster. -->
		<section class="block" aria-labelledby="roster-h">
			<h2 id="roster-h"><span class="num-mark" aria-hidden="true">2</span> Who looked at it</h2>
			<p class="section-note">
				The rules open a case’s mandatory investigations from its trigger and its file. The Case
				Router may add to them and may never take from them — a model deciding what
				<em>not</em> to investigate would be a model deciding the case.
			</p>
			<Roster roster={c.roster} degraded={c.degraded} />
		</section>

		<!-- 3 · Findings. -->
		<section class="block" aria-labelledby="findings-h">
			<h2 id="findings-h"><span class="num-mark" aria-hidden="true">3</span> What they found</h2>
			{#if c.findings.length === 0}
				<p class="void">
					No reading is on the record for this case.
					{#if c.action === 'refuse_malformed'}
						A refused claim is never read: the system declines to judge it.
					{:else if c.action === 'release_claim'}
						The trigger was cleared on the file itself.
					{/if}
				</p>
			{:else}
				<div class="findings">
					{#each c.findings as finding (finding.reading + finding.channel)}
						<FindingCard {finding} reviewReason={finding.disputed ? c.review : null} />
					{/each}
				</div>

				{#if c.conflicts.length > 0}
					<div class="conflicts">
						<p class="eyebrow">Conflicts between the readings</p>
						<ul>
							{#each c.conflicts as conflict (conflict)}
								<li>{conflict}</li>
							{/each}
						</ul>
						<p class="conflict-note">
							Confidence is the winning share of the evidence mass times the strongest winning
							finding, so two readings that disagree land below the 0.70 floor by construction.
						</p>
					</div>
				{/if}
			{/if}
		</section>

		<!-- 4 · The hospital's side. -->
		<section class="block" aria-labelledby="defence-h">
			<h2 id="defence-h"><span class="num-mark" aria-hidden="true">4</span> The hospital’s side</h2>
			{#if c.defence && c.defence.explanation}
				<div class="defence" class:stood={c.defence.excluded === false}>
					<p class="eyebrow">Provider Advocate</p>
					<p class="claim-text">{c.defence.explanation}</p>
					{#if c.defence.excluded === true}
						<p class="verdict">
							<strong>The evidence on file excludes it.</strong> The innocent account does not survive
							the documents, so it did not change the action.
						</p>
					{:else if c.defence.excluded === false}
						<p class="verdict">
							<strong>The evidence on file does not exclude it.</strong> That stopped an action no human
							had reviewed: an unexcluded innocent explanation turns a show-cause notice or a suspension
							into a field audit or a human review. It can never produce a release.
						</p>
					{/if}
				</div>
			{:else}
				<p class="void">
					{#if c.degraded}
						No Provider Advocate ran: the rules decided this case alone.
					{:else}
						No Provider Advocate ran on this case, so no innocent account was put and none was
						weighed.
					{/if}
				</p>
			{/if}
		</section>

		<!-- 5 · Audit review. -->
		<section class="block" aria-labelledby="review-h">
			<h2 id="review-h">
				<span class="num-mark" aria-hidden="true">5</span> Who checked the readings
			</h2>
			{#if c.review || c.disputed.length > 0 || c.disputes_refused.length > 0}
				<div class="review">
					<p class="eyebrow">Audit Reviewer</p>
					{#if c.review}
						<p class="review-text">{c.review}</p>
					{/if}
					<ul class="dispute-list">
						{#if c.disputed.length > 0}
							<li class="set-aside">
								<strong>Set aside:</strong>
								{#each c.disputed as name (name)}<span class="ident chip">{name}</span>{/each}
								— these readings weigh nothing.
							</li>
						{/if}
						{#if c.disputes_refused.length > 0}
							<li class="overruled">
								<strong>Dispute refused:</strong>
								{#each c.disputes_refused as name (name)}<span class="ident chip">{name}</span
									>{/each}
								— the reading repeats a measurement the claim store makes itself, and a dispute may set
								aside a reading, never a measurement. The reading stands.
							</li>
						{/if}
						{#if c.disputed.length === 0 && c.disputes_refused.length === 0}
							<li class="none">No finding was disputed.</li>
						{/if}
					</ul>
				</div>
			{:else}
				<p class="void">
					{#if c.degraded}
						No Audit Reviewer ran: the rules decided this case alone.
					{:else}
						No review is on the record for this case.
					{/if}
				</p>
			{/if}
		</section>

		<!-- 6 · The access gate — the decisive panel. -->
		<section class="block gate-section" aria-labelledby="gate-heading">
			<h2 class="sr-only">The access gate</h2>
			<AccessGate
				gate={c.gate}
				action={c.action}
				access={c.access}
				atStake={c.at_stake}
				specialtiesAtStake={c.specialties_at_stake}
			/>

			{#if c.committee_question}
				<div class="committee">
					<p class="eyebrow">Question for the Committee</p>
					<p>{c.committee_question}</p>
				</div>
			{/if}

			{#if c.brief}
				<div class="committee brief">
					<p class="eyebrow">Brief for the State Empanelment Committee</p>
					{#if c.brief.summary}<p>{c.brief.summary}</p>{/if}
					{#if c.brief.options.length > 0}
						<table class="record">
							<thead>
								<tr><th scope="col">Option</th><th scope="col">Consequence</th></tr>
							</thead>
							<tbody>
								{#each c.brief.options as option (option.option)}
									<tr>
										<th scope="row">{option.option}</th>
										<td>{option.consequence}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					{/if}
					{#if c.brief.recommendation}
						<p><strong>Recommendation.</strong> {c.brief.recommendation}</p>
					{/if}
					{#if c.brief.question}
						<p><strong>Question.</strong> {c.brief.question}</p>
					{/if}
					<p class="fine">It recommends; the Committee decides.</p>
				</div>
			{/if}
		</section>

		<!-- Field work, when the decision ordered any. -->
		{#if c.field_channels_ordered.length > 0 || c.field_questions.length > 0 || c.documents_requested.length > 0}
			<section class="block" aria-labelledby="field-h">
				<h2 id="field-h"><span class="num-mark" aria-hidden="true">·</span> What was ordered</h2>
				<div class="ordered">
					{#if c.field_channels_ordered.length > 0}
						<div>
							<p class="eyebrow">Field channels ordered</p>
							<ul class="plain">
								{#each c.field_channels_ordered as channel (channel)}
									<li class="ident chip">{channel}</li>
								{/each}
							</ul>
						</div>
					{/if}
					{#if c.channels_used.length > 0}
						<div>
							<p class="eyebrow">Channels already on file</p>
							<ul class="plain">
								{#each c.channels_used as channel (channel)}
									<li class="ident chip">{channel}</li>
								{/each}
							</ul>
						</div>
					{/if}
				</div>
				{#if c.field_questions.length > 0}
					<p class="eyebrow spaced">What the field team must establish</p>
					<ul class="prose-list">
						{#each c.field_questions as question (question)}<li>{question}</li>{/each}
					</ul>
				{/if}
				{#if c.field_checklist.length > 0}
					<details class="checklist">
						<summary>Guidebook checklist for this trigger ({c.field_checklist.length})</summary>
						<ul class="prose-list">
							{#each c.field_checklist as item (item)}<li>{item}</li>{/each}
						</ul>
					</details>
				{/if}
				{#if c.documents_requested.length > 0}
					<p class="eyebrow spaced">Documents requested</p>
					<ul class="prose-list">
						{#each c.documents_requested as document (document)}<li>{document}</li>{/each}
					</ul>
				{/if}
			</section>
		{/if}

		<!-- 7 · The investigation trail. -->
		<section class="block" aria-labelledby="trail-h">
			<h2 id="trail-h"><span class="num-mark" aria-hidden="true">6</span> How they worked</h2>
			<Trail trail={c.trail} degraded={c.degraded} toolsCalled={c.tools_called} />
		</section>

		<!-- 8 · The artefact. -->
		<section class="block" aria-labelledby="artefact-h">
			<h2 id="artefact-h"><span class="num-mark" aria-hidden="true">7</span> What was issued</h2>
			{#if c.explanation}
				<p class="explanation">{c.explanation}</p>
			{/if}
			<Artefact markdown={c.artefact} path={c.artefact_path} action={c.action} />
		</section>

		<nav class="pager" aria-label="Adjacent cases">
			{#if data.previous}
				<a class="prev" href="{base}/case/{data.previous.claim_id}/">
					<span class="eyebrow">Previous</span>
					<span class="ident">{data.previous.claim_id}</span>
					<span class="pager-action">{data.previous.action_label}</span>
				</a>
			{:else}
				<span></span>
			{/if}
			{#if data.next}
				<a class="next" href="{base}/case/{data.next.claim_id}/">
					<span class="eyebrow">Next</span>
					<span class="ident">{data.next.claim_id}</span>
					<span class="pager-action">{data.next.action_label}</span>
				</a>
			{/if}
		</nav>

		{#if hasCrew}
			<p class="provenance">
				Worked by {c.agents.length} agents on
				<span class="mono">{c.model ?? 'the configured model'}</span>. The agents established the
				findings; <code>rules/policy.py</code> decided the action.
			</p>
		{/if}
	</div>
</div>

<style>
	.hero {
		background: var(--surface-2);
		border-bottom: 1px solid var(--rule-strong);
	}

	/* A case the gate diverted says so from the top edge of the page, not only in its own section. */
	.hero.gate-emphatic {
		box-shadow: inset 0 3px 0 var(--gate-protect-edge);
	}

	.hero-in {
		padding-top: 18px;
		padding-bottom: 22px;
	}

	/* The record reads best at a measured width; the index takes the room beside it rather than
	   letting a paragraph run to 1,600px. */
	.case-body {
		display: grid;
		grid-template-columns: 224px minmax(0, 1fr);
		gap: 40px;
		align-items: start;
		padding-top: 26px;
	}

	/* No ceiling here: prose is already held to `--measure` by the global rule, and the things that
	   are not prose — the trail, the findings, the sensitivity table — all read better with room. */
	.record-col {
		min-width: 0;
	}

	.index {
		position: sticky;
		top: 18px;
		min-width: 0;
	}

	.index-label {
		font-family: var(--mono);
		font-size: 9.5px;
		letter-spacing: 0.13em;
		text-transform: uppercase;
		color: var(--ink-muted);
		margin: 0 0 9px 10px;
		max-width: none;
	}

	.index ol {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 1px;
		border-left: 1px solid var(--rule);
	}

	.index a {
		display: flex;
		align-items: baseline;
		gap: 9px;
		padding: 6px 10px;
		font-size: 12.5px;
		line-height: 1.3;
		color: var(--ink-muted);
		text-decoration: none;
		border-left: 2px solid transparent;
		margin-left: -1px;
	}

	.index a:hover {
		color: var(--ink);
		background: var(--surface-2);
	}

	.index a.on {
		color: var(--ink);
		font-weight: 500;
		border-left-color: var(--ink);
		background: var(--surface-2);
	}

	.index a.emphatic .index-mark {
		color: var(--gate-protect);
	}

	.index a.emphatic.on {
		border-left-color: var(--gate-protect-edge);
	}

	.index-mark {
		font-family: var(--mono);
		font-size: 10.5px;
		color: var(--ink-faint);
		width: 11px;
		flex: none;
		text-align: center;
	}

	.index a.on .index-mark {
		color: inherit;
	}

	@media (max-width: 1080px) {
		.case-body {
			grid-template-columns: minmax(0, 1fr);
			gap: 0;
		}

		/* Stacked, a sticky index would sit on top of the record it points into. */
		.index {
			position: static;
			margin-bottom: 22px;
		}

		.index ol {
			display: flex;
			flex-wrap: wrap;
			gap: 2px;
			border-left: none;
		}

		.index a {
			border: 1px solid var(--rule);
			border-radius: 3px;
			margin-left: 0;
		}

		.index-label {
			margin-left: 0;
		}
	}

	.breadcrumb {
		display: flex;
		gap: 8px;
		align-items: baseline;
		font-size: 12.5px;
		color: var(--ink-muted);
		margin-bottom: 14px;
	}

	.current {
		color: var(--ink);
	}

	/* ── header ───────────────────────────────────────────────────── */

	/* The band is the header. Boxing it again inside its own ground gave two frames around the same
	   thing; the rules between the three rows are enough structure. */
	.case-header {
		padding: 0;
	}

	.ids {
		display: flex;
		gap: 28px;
		flex-wrap: wrap;
		padding-bottom: 14px;
		border-bottom: 1px solid var(--rule);
	}

	.ids p {
		margin: 0;
	}

	.ids .big {
		font-size: 18px;
		font-weight: 500;
		letter-spacing: -0.01em;
	}

	.decision {
		display: flex;
		gap: 34px;
		flex-wrap: wrap;
		align-items: flex-start;
		padding: 16px 0 14px;
		border-bottom: 1px solid var(--rule);
	}

	.decided {
		min-width: 0;
	}

	.decided .eyebrow,
	.conf-block .eyebrow,
	.gate-block .eyebrow {
		display: block;
		margin-bottom: 5px;
	}

	.group-note {
		margin: 6px 0 0;
		font-size: 12.5px;
		color: var(--ink-muted);
	}

	.conf-block {
		min-width: 190px;
	}

	.flags {
		display: flex;
		gap: 7px;
		flex-wrap: wrap;
		padding-top: 12px;
	}

	.flag {
		font-size: 11.5px;
		border: 1px solid var(--rule);
		border-radius: 2px;
		padding: 1px 7px;
		color: var(--ink-muted);
	}

	.flag[data-on='true'] {
		color: var(--ink);
		border-color: var(--ink-muted);
		font-weight: 500;
	}

	.degraded-banner {
		display: flex;
		gap: 12px;
		align-items: flex-start;
		margin-top: 14px;
		padding: 12px 14px;
		border: 1px solid var(--ink-muted);
		border-left-width: 4px;
		background: var(--surface-2);
	}

	.degraded-banner p {
		margin: 0;
		font-size: 13px;
		color: var(--ink-soft);
		max-width: 80ch;
	}

	/* ── sections ─────────────────────────────────────────────────── */

	.block {
		margin-top: 34px;
	}

	.block h2 {
		font-size: 20px;
		letter-spacing: -0.02em;
		display: flex;
		align-items: baseline;
		gap: 10px;
		padding-bottom: 9px;
		border-bottom: 1px solid var(--rule-strong);
		margin-bottom: 15px;
		/* The index jumps to these, and a heading flush against the top edge reads as cut off. */
		scroll-margin-top: 18px;
	}

	.num-mark {
		font-family: var(--mono);
		font-size: 11px;
		color: var(--ink-muted);
		border: 1px solid var(--rule);
		border-radius: 50%;
		width: 20px;
		height: 20px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		flex: none;
		align-self: center;
	}

	.section-note {
		font-size: 13px;
		color: var(--ink-muted);
		max-width: 76ch;
		margin-bottom: 14px;
	}

	.void {
		border: 1px dashed var(--rule-strong);
		background: var(--surface-2);
		padding: 14px 16px;
		color: var(--ink-soft);
		font-size: 13.5px;
		margin: 0;
		max-width: none;
	}

	/* ── trigger ──────────────────────────────────────────────────── */

	.trigger-card {
		border: 1px solid var(--rule);
		border-left: 3px solid var(--ink-soft);
		background: var(--surface);
		padding: 12px 14px;
	}

	.trigger-line {
		display: flex;
		align-items: baseline;
		gap: 10px;
		flex-wrap: wrap;
		margin: 0;
	}

	.tid {
		font-size: 17px;
		font-weight: 500;
	}

	.tname {
		font-size: 15px;
		font-weight: 500;
	}

	.sev {
		font-family: var(--mono);
		font-size: 10.5px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--ink-muted);
		border: 1px solid var(--rule);
		padding: 0 5px;
		border-radius: 2px;
	}

	.evidence {
		margin: 7px 0 0;
		font-size: 13.5px;
		color: var(--ink-soft);
	}

	.source {
		margin: 5px 0 0;
		font-size: 11.5px;
		color: var(--ink-muted);
		font-style: italic;
	}

	.also,
	.codes {
		display: flex;
		align-items: baseline;
		gap: 6px;
		flex-wrap: wrap;
		margin: 12px 0 0;
		max-width: none;
	}

	.chip {
		border: 1px solid var(--rule);
		border-radius: 2px;
		padding: 0 5px;
		font-size: 11px;
		color: var(--ink-soft);
		background: var(--surface-2);
		white-space: nowrap;
	}

	.package {
		display: flex;
		gap: 28px;
		flex-wrap: wrap;
		margin: 12px 0 0;
		padding: 10px 0 0;
		border-top: 1px solid var(--rule-soft);
	}

	.package dt {
		font-family: var(--mono);
		font-size: 10.5px;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--ink-muted);
	}

	.package dd {
		margin: 2px 0 0;
		font-size: 13.5px;
	}

	/* ── findings ─────────────────────────────────────────────────── */

	.findings {
		display: grid;
		gap: 10px;
	}

	.conflicts {
		margin-top: 14px;
		border: 1px solid var(--rule);
		border-left: 3px solid var(--deferred);
		background: var(--deferred-wash);
		padding: 11px 14px;
	}

	.conflicts ul {
		margin: 4px 0 0;
		padding-left: 18px;
		font-size: 13px;
	}

	.conflict-note {
		margin: 8px 0 0;
		font-size: 12px;
		color: var(--ink-muted);
		max-width: 76ch;
	}

	/* ── defence and review ───────────────────────────────────────── */

	.defence,
	.review {
		border: 1px solid var(--rule);
		border-left: 3px solid var(--rule-strong);
		background: var(--surface);
		padding: 12px 14px;
	}

	.defence.stood {
		border-left-color: var(--deferred);
		background: var(--deferred-wash);
	}

	.claim-text,
	.review-text {
		margin: 6px 0 0;
		font-size: 14px;
		line-height: 1.55;
		max-width: 76ch;
	}

	.verdict {
		margin: 10px 0 0;
		padding-top: 9px;
		border-top: 1px solid var(--rule);
		font-size: 13px;
		color: var(--ink-soft);
		max-width: 76ch;
	}

	.dispute-list {
		list-style: none;
		margin: 10px 0 0;
		padding: 0;
		display: grid;
		gap: 7px;
		font-size: 13px;
	}

	.dispute-list li {
		padding-left: 11px;
		border-left: 2px solid var(--rule-strong);
		color: var(--ink-soft);
		max-width: 80ch;
	}

	.dispute-list .set-aside {
		border-left-color: var(--ink-muted);
	}

	.dispute-list .overruled {
		border-left-color: var(--gate-unknown);
	}

	.dispute-list .none {
		color: var(--ink-muted);
	}

	/* ── gate section ─────────────────────────────────────────────── */

	.gate-section {
		margin-top: 40px;
	}

	.committee {
		margin-top: 14px;
		border: 1px solid var(--rule);
		border-left: 3px solid var(--gate-protect-edge);
		background: var(--surface);
		padding: 12px 14px;
	}

	.committee p {
		font-size: 13.5px;
		max-width: 78ch;
	}

	.committee .eyebrow {
		margin-bottom: 5px;
	}

	.committee table {
		margin: 10px 0;
	}

	.committee .fine {
		margin: 6px 0 0;
		font-size: 11.5px;
		color: var(--ink-muted);
		font-style: italic;
	}

	/* ── ordered work ─────────────────────────────────────────────── */

	.ordered {
		display: flex;
		gap: 34px;
		flex-wrap: wrap;
	}

	.plain {
		list-style: none;
		margin: 5px 0 0;
		padding: 0;
		display: flex;
		gap: 5px;
		flex-wrap: wrap;
	}

	.prose-list {
		margin: 6px 0 0;
		padding-left: 18px;
		font-size: 13.5px;
		max-width: 78ch;
	}

	.prose-list li {
		margin-bottom: 3px;
	}

	.spaced {
		display: block;
		margin-top: 14px;
	}

	.checklist {
		margin-top: 12px;
		border: 1px solid var(--rule);
		padding: 9px 12px;
	}

	.checklist summary {
		cursor: pointer;
		font-size: 13px;
		color: var(--ink-soft);
	}

	/* ── artefact ─────────────────────────────────────────────────── */

	.explanation {
		font-size: 14.5px;
		line-height: 1.6;
		max-width: 78ch;
		margin-bottom: 16px;
		padding-left: 12px;
		border-left: 3px solid var(--rule-strong);
	}

	/* ── pager ────────────────────────────────────────────────────── */

	.pager {
		display: flex;
		justify-content: space-between;
		gap: 16px;
		margin-top: 40px;
		padding-top: 14px;
		border-top: 1px solid var(--rule);
	}

	.pager a {
		display: grid;
		gap: 1px;
		text-decoration: none;
		border: 1px solid var(--rule);
		border-radius: 2px;
		padding: 8px 12px;
		min-width: 190px;
		max-width: 100%;
		min-width: 0;
		overflow: hidden;
	}

	.pager a > * {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.pager a:hover {
		background: var(--surface-2);
	}

	.pager .next {
		text-align: right;
	}

	.pager-action {
		font-size: 12px;
		color: var(--ink-muted);
	}

	.provenance {
		margin-top: 16px;
		font-size: 12px;
		color: var(--ink-muted);
		max-width: 80ch;
	}

	@media (max-width: 720px) {
		.hero-in {
			padding-top: 14px;
			padding-bottom: 18px;
		}
		.case-body {
			padding-top: 20px;
		}
		.decision {
			gap: 20px;
		}
		.ids {
			gap: 18px;
		}
		.pager a {
			min-width: 0;
		}
	}
</style>
