<script lang="ts">
	import '../app.css';
	import { base } from '$app/paths';
	import { page } from '$app/state';
	import { formatInt, formatNumber } from '$lib/domain';
	import type { Snippet } from 'svelte';
	import type { LayoutData } from './$types';

	let { data, children }: { data: LayoutData; children: Snippet } = $props();

	let theme = $state<'system' | 'light' | 'dark'>('system');
	let open = $state(false);

	$effect(() => {
		// Proof that the module entry ran; app.html watches for its absence (see there).
		document.documentElement.dataset.hydrated = 'true';
		try {
			const saved = localStorage.getItem('ag-theme');
			if (saved === 'light' || saved === 'dark') theme = saved;
		} catch {
			/* private browsing: the OS setting stands */
		}
	});

	// Close the small-screen drawer on navigation, or it stays over the page it just opened.
	$effect(() => {
		void page.url.pathname;
		open = false;
	});

	function cycle() {
		theme = theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system';
		try {
			if (theme === 'system') {
				delete document.documentElement.dataset.theme;
				localStorage.removeItem('ag-theme');
			} else {
				document.documentElement.dataset.theme = theme;
				localStorage.setItem('ag-theme', theme);
			}
		} catch {
			/* storage unavailable; the toggle still applies for this page */
		}
	}

	const meta = $derived(data.meta);

	const nav = [
		{
			href: `${base}/`,
			label: 'Cases',
			note: 'the decided run',
			match: (p: string) => p === `${base}/` || p.startsWith(`${base}/case`),
			// Rows of a decision log.
			icon: 'M3 5h18M3 10h18M3 15h12M3 20h12'
		},
		{
			href: `${base}/evaluation/`,
			label: 'Evaluation',
			note: 'the same rules at scale',
			match: (p: string) => p.startsWith(`${base}/evaluation`),
			// A curve against an axis.
			icon: 'M3 20V4M3 20h18M6 16c4 0 5-9 9-9 2 0 3 2 5 3'
		},
		{
			href: `${base}/benchmark/`,
			label: 'Benchmark',
			note: 'rules against the crew',
			match: (p: string) => p.startsWith(`${base}/benchmark`),
			// Two columns, measured against each other.
			icon: 'M6 20V9M12 20V4M18 20v-7M3 20h18'
		}
	];
</script>

<a class="skip-link" href="#main">Skip to content</a>

<p class="static-note">
	Opened directly from disk, so the filters, sorting and the theme toggle are inert — browsers will
	not load modules over <code>file://</code>. Everything on the page is here and complete. To get
	the controls back, serve the folder: <code>npx vite preview</code>, or
	<code>python3 -m http.server</code> inside <code>build/</code>.
</p>

<div class="shell" class:drawer-open={open}>
	<!--
		The rail, not a masthead. The app is an instrument someone works in for a while, so the
		sections, the run it is showing and the two constants every figure is read against stay on
		screen the whole time instead of scrolling away with a header.
	-->
	<aside class="rail">
		<div class="rail-inner">
			<a class="brand" href="{base}/">
				<svg class="mark" viewBox="0 0 24 24" aria-hidden="true">
					<rect x="2.6" y="3" width="2.8" height="17" rx="0.6" />
					<rect x="18.6" y="3" width="2.8" height="17" rx="0.6" />
					<rect class="arm" x="5.4" y="5.4" width="13.2" height="2.6" rx="0.6" />
					<rect x="2.6" y="20.4" width="18.8" height="1.6" rx="0.6" opacity="0.55" />
				</svg>
				<span class="brand-text">
					<span class="name">The Access Gate</span>
					<span class="sub">PM-JAY hospital-fraud decisions</span>
				</span>
			</a>

			<button
				type="button"
				class="drawer-toggle"
				aria-expanded={open}
				aria-controls="rail-body"
				onclick={() => (open = !open)}
			>
				<span class="sr-only">{open ? 'Hide' : 'Show'} navigation</span>
				<span class="bars" aria-hidden="true"></span>
			</button>

			<div class="rail-body" id="rail-body">
				<nav aria-label="Sections">
					<p class="rail-label">Sections</p>
					<ul>
						{#each nav as item (item.href)}
							{@const here = item.match(page.url.pathname)}
							<li>
								<a href={item.href} aria-current={here ? 'page' : undefined} class:here>
									<svg class="glyph" viewBox="0 0 24 24" aria-hidden="true">
										<path d={item.icon} />
									</svg>
									<span class="nav-text">
										<span class="nav-label">{item.label}</span>
										<span class="nav-note">{item.note}</span>
									</span>
								</a>
							</li>
						{/each}
					</ul>
				</nav>

				{#if meta}
					<!-- Provenance, permanently on screen. Every figure in the app comes from this run. -->
					<div class="run">
						<p class="rail-label">This run</p>
						<dl>
							<div>
								<dt>Source</dt>
								<dd class="mono">{meta.source_run}</dd>
							</div>
							<div>
								<dt>Model</dt>
								<dd class="mono">{meta.model ?? 'rules only'}</dd>
							</div>
							<div>
								<dt>Cases</dt>
								<dd>
									{formatInt(meta.counts.cases)}
									<span class="dim"
										>· {formatInt(meta.counts.crewed)} crewed{meta.counts.degraded > 0
											? `, ${formatInt(meta.counts.degraded)} degraded`
											: ''}</span
									>
								</dd>
							</div>
							<div>
								<dt>Floor</dt>
								<dd>
									{formatNumber(meta.confidence_floor, 2)}
									<span class="dim">confidence</span>
								</dd>
							</div>
							<div>
								<dt>Gate</dt>
								<dd>
									{formatInt(meta.distance_material_km)} km
									<span class="dim">to an alternative</span>
								</dd>
							</div>
						</dl>
					</div>
				{/if}

				<div class="rail-foot">
					<button type="button" class="theme" onclick={cycle} aria-live="polite">
						<span class="rail-label">Theme</span>
						<span class="mono">{theme}</span>
					</button>
					<p class="rail-fine">
						Read-only. It runs no agents and changes no decision. Simulated cases; hospitals appear
						by pseudonymous reference only.
					</p>
				</div>
			</div>
		</div>
	</aside>

	<div class="canvas">
		<main id="main" tabindex="-1">
			{@render children()}
		</main>

		<footer class="colophon">
			<div class="wrap">
				<p>
					A read-only view of decisions already made. It performs no fraud detection, runs no agents
					and changes no decision. Every figure on this page comes from the exported record.
				</p>
				<p class="fine">
					Simulated cases on simulated claims. Hospitals appear by pseudonymous reference only.
					District network figures are real published data; no real hospital is alleged to have done
					anything.
				</p>
			</div>
		</footer>
	</div>
</div>

<style>
	.static-note {
		display: none;
		margin: 0;
		max-width: none;
		padding: 9px 24px;
		font-size: 12.5px;
		color: var(--ink-soft);
		background: var(--surface-2);
		border-bottom: 1px solid var(--rule-strong);
	}

	:global(html[data-hydrated='no']) .static-note {
		display: block;
	}

	/* ── the shell ─────────────────────────────────────────────────────── */

	.shell {
		display: grid;
		grid-template-columns: var(--rail-w) minmax(0, 1fr);
		min-height: 100vh;
	}

	.canvas {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}

	main {
		display: block;
		outline: none;
		flex: 1;
	}

	/* ── the rail ──────────────────────────────────────────────────────── */

	.rail {
		background: var(--rail);
		color: var(--rail-ink-soft);
		border-right: 1px solid var(--rail-rule);
	}

	.rail-inner {
		position: sticky;
		top: 0;
		display: flex;
		flex-direction: column;
		height: 100vh;
		overflow-y: auto;
		overscroll-behavior: contain;
	}

	.brand {
		display: flex;
		align-items: center;
		gap: 11px;
		padding: 18px 18px 17px;
		text-decoration: none;
		color: var(--rail-ink);
		border-bottom: 1px solid var(--rail-rule);
	}

	.mark {
		width: 26px;
		height: 26px;
		flex: none;
		fill: var(--rail-ink-faint);
	}

	/* The raised arm is the only thing in the rail that carries the accent: the gate, open. */
	.mark .arm {
		fill: var(--accent);
	}

	.brand-text {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
	}

	.name {
		font-weight: 600;
		font-size: 14.5px;
		letter-spacing: -0.015em;
		line-height: 1.1;
	}

	.sub {
		font-size: 10.5px;
		color: var(--rail-ink-faint);
		letter-spacing: 0.01em;
		line-height: 1.25;
	}

	.rail-body {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-height: 0;
	}

	.rail-label {
		font-family: var(--mono);
		font-size: 9.5px;
		letter-spacing: 0.13em;
		text-transform: uppercase;
		color: var(--rail-ink-faint);
		margin: 0 0 8px;
		max-width: none;
	}

	nav {
		padding: 16px 12px 14px;
	}

	nav .rail-label {
		padding-left: 6px;
	}

	nav ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 2px;
	}

	nav a {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 10px;
		border-radius: 3px;
		text-decoration: none;
		color: var(--rail-ink-soft);
		/* The current section is marked by an accent edge as well as by fill, so the rail still
		   reads on a projector and in a grayscale recording. */
		border-left: 2px solid transparent;
	}

	nav a:hover {
		background: var(--rail-2);
		color: var(--rail-ink);
	}

	nav a.here {
		background: var(--rail-active);
		border-left-color: var(--accent);
		color: var(--rail-ink);
	}

	.glyph {
		width: 17px;
		height: 17px;
		flex: none;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.7;
		stroke-linecap: round;
		opacity: 0.8;
	}

	nav a.here .glyph {
		opacity: 1;
		color: var(--accent);
	}

	.nav-text {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}

	.nav-label {
		font-size: 13.5px;
		font-weight: 500;
		line-height: 1.2;
	}

	.nav-note {
		font-size: 10.5px;
		color: var(--rail-ink-faint);
		line-height: 1.3;
	}

	.run {
		padding: 14px 18px 16px;
		border-top: 1px solid var(--rail-rule);
		border-bottom: 1px solid var(--rail-rule);
		margin: 4px 0 0;
	}

	.run dl {
		margin: 0;
		display: grid;
		gap: 7px;
	}

	.run dl div {
		display: grid;
		grid-template-columns: 46px minmax(0, 1fr);
		gap: 8px;
		align-items: baseline;
	}

	.run dt {
		font-size: 10.5px;
		color: var(--rail-ink-faint);
		text-transform: uppercase;
		letter-spacing: 0.06em;
		font-family: var(--mono);
	}

	.run dd {
		margin: 0;
		font-size: 11.5px;
		color: var(--rail-ink);
		overflow-wrap: anywhere;
		line-height: 1.35;
	}

	.run .dim {
		color: var(--rail-ink-faint);
	}

	.rail-foot {
		margin-top: auto;
		padding: 14px 18px 18px;
	}

	.theme {
		display: flex;
		flex-direction: column;
		gap: 3px;
		width: 100%;
		text-align: left;
		background: none;
		border: 1px solid var(--rail-rule);
		border-radius: 3px;
		padding: 7px 10px;
		cursor: pointer;
		color: var(--rail-ink-soft);
		font: inherit;
		font-size: 11.5px;
	}

	.theme:hover {
		background: var(--rail-2);
		color: var(--rail-ink);
	}

	.theme .rail-label {
		margin: 0;
	}

	.rail-fine {
		margin: 12px 0 0;
		font-size: 10.5px;
		line-height: 1.45;
		color: var(--rail-ink-faint);
		max-width: none;
	}

	/* The drawer button only exists below the breakpoint. */
	.drawer-toggle {
		display: none;
	}

	/* ── the colophon ──────────────────────────────────────────────────── */

	.colophon {
		border-top: 1px solid var(--rule);
		margin-top: 56px;
		padding: 22px 0 40px;
		background: var(--surface);
	}

	.colophon p {
		font-size: 12.5px;
		color: var(--ink-muted);
		max-width: 78ch;
	}

	.colophon .fine {
		margin-bottom: 0;
		color: var(--ink-faint);
		font-size: 11.5px;
	}

	/* ── narrow: the rail becomes a bar with a drawer ──────────────────── */

	@media (max-width: 1000px) {
		.shell {
			grid-template-columns: minmax(0, 1fr);
		}

		.rail {
			border-right: none;
			border-bottom: 1px solid var(--rail-rule);
			position: sticky;
			top: 0;
			z-index: 30;
		}

		.rail-inner {
			position: static;
			height: auto;
			overflow: visible;
		}

		.brand {
			border-bottom: none;
			padding: 11px 16px;
		}

		.drawer-toggle {
			display: block;
			position: absolute;
			top: 9px;
			right: 12px;
			width: 36px;
			height: 32px;
			background: none;
			border: 1px solid var(--rail-rule);
			border-radius: 3px;
			cursor: pointer;
			padding: 0;
		}

		/* Three rules, drawn rather than typed, so no icon font is needed. */
		.bars,
		.bars::before,
		.bars::after {
			position: absolute;
			left: 9px;
			width: 16px;
			height: 1.5px;
			background: var(--rail-ink-soft);
			content: '';
		}

		.bars {
			top: 15px;
		}
		.bars::before {
			top: -5px;
			left: 0;
		}
		.bars::after {
			top: 5px;
			left: 0;
		}

		.rail-body {
			display: none;
			border-top: 1px solid var(--rail-rule);
		}

		.drawer-open .rail-body {
			display: flex;
		}

		/* Off disk nothing hydrates, so the drawer could never be opened — show the sections
		   instead of a button that does nothing. */
		:global(html[data-hydrated='no']) .rail-body {
			display: flex;
		}

		:global(html[data-hydrated='no']) .drawer-toggle {
			display: none;
		}

		.rail-foot {
			margin-top: 0;
		}
	}
</style>
