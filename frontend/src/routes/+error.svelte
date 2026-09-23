<script lang="ts">
	import { base } from '$app/paths';
	import { page } from '$app/state';

	const missingData = $derived(/could not read|export/i.test(page.error?.message ?? ''));
</script>

<svelte:head>
	<title>{page.status} — The Access Gate</title>
</svelte:head>

<div class="wrap">
	<p class="eyebrow">Error {page.status}</p>
	<h1>{page.status === 404 ? 'Nothing at that address' : 'This page could not be built'}</h1>

	<p class="detail">{page.error?.message ?? 'No further detail is on the record.'}</p>

	{#if missingData}
		<div class="fix">
			<p class="fix-title">The exported record is missing or unreadable.</p>
			<p>Regenerate it from the Python system, then rebuild:</p>
			<pre><code
					>npm run export      # or, from the repository root:
python -m scripts.export_frontend</code
				></pre>
			<p>
				The committed <code>out_live/</code> is the live nine-agent run, so this needs no API key and
				no virtualenv — the exporter is standard library only.
			</p>
		</div>
	{/if}

	<p><a href="{base}/">Back to the case list</a></p>
</div>

<style>
	.wrap {
		max-width: 68ch;
		margin: 0 auto;
		padding: 70px 24px 0;
	}

	h1 {
		font-size: 25px;
		margin: 4px 0 12px;
	}

	.detail {
		font-size: 14.5px;
		color: var(--ink-soft);
		border-left: 3px solid var(--rule-strong);
		padding-left: 12px;
	}

	.fix {
		border: 1px solid var(--rule);
		background: var(--surface);
		border-radius: 2px;
		padding: 14px 16px;
		margin: 20px 0;
	}

	.fix-title {
		font-weight: 600;
		margin-bottom: 4px;
	}

	.fix p {
		font-size: 13.5px;
		color: var(--ink-soft);
	}

	pre {
		background: var(--surface-2);
		border: 1px solid var(--rule);
		border-radius: 2px;
		padding: 10px 12px;
		overflow-x: auto;
		margin: 0;
	}

	code {
		font-family: var(--mono);
		font-size: 12.5px;
		line-height: 1.6;
	}
</style>
