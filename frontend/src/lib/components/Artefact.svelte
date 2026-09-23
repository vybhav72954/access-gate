<!--
	The issued notice or order.

	A legal-style instrument, so it is set as one: serif, a measured column, ruled tables, and a
	presentation deliberately unlike the app around it. The Markdown is the artefact the pipeline
	wrote; it is escaped before rendering, so free text written by a model cannot introduce markup.
-->
<script lang="ts">
	import { renderMarkdown } from '../markdown';

	interface Props {
		markdown: string | null;
		path: string | null;
		action: string;
	}

	let { markdown, path, action }: Props = $props();
	const html = $derived(markdown ? renderMarkdown(markdown) : '');
</script>

{#if html}
	<figure class="instrument">
		<div class="sheet">
			<!-- eslint-disable-next-line svelte/no-at-html-tags — renderMarkdown escapes every input -->
			{@html html}
		</div>
		{#if path}
			<figcaption>
				Issued as <span class="ident">{path}</span>
			</figcaption>
		{/if}
	</figure>
{:else}
	<p class="void">
		{#if action === 'release_claim'}
			No artefact was issued. A released claim needs no notice: the withheld claim is simply
			released.
		{:else if action === 'refuse_malformed'}
			No artefact was issued. The claim was refused as malformed, so nothing was decided to write an
			order about.
		{:else}
			No artefact is on the record for this case.
		{/if}
	</p>
{/if}

<style>
	.instrument {
		margin: 0;
	}

	.sheet {
		background: var(--surface);
		border: 1px solid var(--rule-strong);
		border-radius: 2px;
		padding: 34px 38px 30px;
		font-family: var(--serif);
		font-size: 15px;
		line-height: 1.6;
		color: var(--ink);
		max-width: 78ch;
		box-shadow: inset 0 0 0 1px var(--surface);
	}

	figcaption {
		margin-top: 7px;
		font-size: 11.5px;
		color: var(--ink-muted);
	}

	/* The artefact's own `#` title becomes the sheet's h2. */
	.sheet :global(h2) {
		font-family: var(--serif);
		font-size: 21px;
		font-weight: 600;
		letter-spacing: 0;
		margin: 0 0 14px;
		padding-bottom: 10px;
		border-bottom: 2px solid var(--ink);
	}

	.sheet :global(h3) {
		font-family: var(--serif);
		font-size: 15px;
		font-weight: 600;
		letter-spacing: 0.02em;
		text-transform: uppercase;
		margin: 24px 0 8px;
		padding-bottom: 4px;
		border-bottom: 1px solid var(--rule);
	}

	.sheet :global(p) {
		margin: 0 0 11px;
		max-width: none;
	}

	.sheet :global(blockquote) {
		margin: 0 0 18px;
		padding: 9px 13px;
		border-left: 3px solid var(--rule-strong);
		background: var(--surface-2);
		font-family: var(--sans);
		font-size: 12.5px;
		line-height: 1.5;
		color: var(--ink-soft);
	}

	.sheet :global(blockquote p) {
		margin: 0;
	}

	.sheet :global(ul) {
		margin: 0 0 12px;
		padding-left: 20px;
	}

	.sheet :global(li) {
		margin-bottom: 4px;
	}

	.sheet :global(code) {
		font-family: var(--mono);
		font-size: 0.8em;
		background: var(--surface-2);
		padding: 0 3px;
		border-radius: 2px;
		/* Reason codes run to 54 characters (ASPIRATIONAL_DISTRICT_THIN_NETWORK=…). Unbroken, one of
		   them is wider than a phone, and since this sits in prose rather than a scroll container it
		   drags the whole page sideways. `anywhere` also lets the span shrink below its longest token,
		   which `break-word` does not. */
		overflow-wrap: anywhere;
	}

	.sheet :global(.doc-table-wrap) {
		overflow-x: auto;
		margin: 0 0 16px;
	}

	.sheet :global(table) {
		width: 100%;
		border-collapse: collapse;
		font-family: var(--sans);
		font-size: 12.5px;
		font-variant-numeric: tabular-nums;
	}

	.sheet :global(th),
	.sheet :global(td) {
		text-align: left;
		padding: 6px 10px 6px 0;
		border-bottom: 1px solid var(--rule-soft);
		vertical-align: top;
	}

	.sheet :global(thead th) {
		border-bottom: 1px solid var(--ink-soft);
		font-size: 10.5px;
		font-family: var(--mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
		font-weight: 500;
		color: var(--ink-muted);
	}

	.sheet :global(hr) {
		border: none;
		border-top: 1px solid var(--rule);
		margin: 20px 0;
	}

	.sheet :global(.empty) {
		color: var(--ink-faint);
	}

	.void {
		border: 1px dashed var(--rule-strong);
		background: var(--surface-2);
		padding: 14px 16px;
		color: var(--ink-soft);
		font-size: 13.5px;
		margin: 0;
		max-width: 72ch;
	}

	@media (max-width: 720px) {
		.sheet {
			padding: 20px 18px 18px;
			font-size: 14.5px;
		}
	}
</style>
