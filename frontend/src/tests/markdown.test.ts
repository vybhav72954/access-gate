import { describe, expect, it } from 'vitest';
import { escapeHtml, renderInline, renderMarkdown } from '$lib/markdown';

describe('escaping', () => {
	it('escapes every character that could open markup', () => {
		expect(escapeHtml('<script>alert("x")</script>')).toBe(
			'&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;'
		);
	});

	it('escapes model-written text before applying any span', () => {
		// Free text in a finding is written by a model; it must never become markup.
		const html = renderInline('The hospital said <b>nothing</b> & left.');
		expect(html).toContain('&lt;b&gt;');
		expect(html).not.toContain('<b>');
		expect(html).toContain('&amp;');
	});
});

describe('inline spans', () => {
	it('renders bold, italic and code', () => {
		expect(renderInline('**Case** CASE-1')).toBe('<strong>Case</strong> CASE-1');
		expect(renderInline('*Source: guidebook*')).toBe('<em>Source: guidebook</em>');
		expect(renderInline('`read_document`')).toBe('<code>read_document</code>');
	});

	it('leaves an unmatched asterisk alone', () => {
		expect(renderInline('2 * 3 = 6')).toBe('2 * 3 = 6');
	});

	it('unescapes the pipes the artefact escaped for its tables', () => {
		expect(renderInline('a \\| b')).toBe('a | b');
	});
});

describe('block rendering', () => {
	it('renders the artefact title as an h2, so the page keeps one h1', () => {
		expect(renderMarkdown('# Escalation to the Committee')).toBe(
			'<h2>Escalation to the Committee</h2>'
		);
		expect(renderMarkdown('## Findings')).toBe('<h3>Findings</h3>');
	});

	it('renders the simulated-case banner as a blockquote', () => {
		const html = renderMarkdown('> **SIMULATED CASE.** Everything is fictional.');
		expect(html).toContain('<blockquote>');
		expect(html).toContain('<strong>SIMULATED CASE.</strong>');
	});

	it('renders a bullet list', () => {
		const html = renderMarkdown('- one\n- two');
		expect(html).toBe('<ul><li>one</li><li>two</li></ul>');
	});

	it('renders a pipe table with header cells scoped', () => {
		const html = renderMarkdown('| A | B |\n|---|---|\n| 1 | 2 |');
		expect(html).toContain('<th scope="col">A</th>');
		expect(html).toContain('<td>1</td>');
		expect(html).toContain('<tbody><tr><td>1</td><td>2</td></tr></tbody>');
	});

	it('keeps an escaped pipe inside a table cell', () => {
		const html = renderMarkdown('| Tool | Result |\n|---|---|\n| `x` | a \\| b |');
		expect(html).toContain('a | b');
	});

	it('marks an empty table cell rather than leaving it blank', () => {
		const html = renderMarkdown('| A | B |\n|---|---|\n| 1 |  |');
		expect(html).toContain('<span class="empty">—</span>');
	});

	it('joins wrapped lines into one paragraph', () => {
		expect(renderMarkdown('one line\nand its continuation')).toBe(
			'<p>one line and its continuation</p>'
		);
	});

	it('separates paragraphs on a blank line', () => {
		const html = renderMarkdown('first\n\nsecond');
		expect(html).toBe('<p>first</p>\n<p>second</p>');
	});

	it('returns nothing for an empty artefact', () => {
		expect(renderMarkdown('')).toBe('');
		expect(renderMarkdown('   \n  ')).toBe('');
	});

	it('renders a whole artefact section in order', () => {
		const html = renderMarkdown(
			[
				'# Show-cause notice',
				'',
				'> **SIMULATED CASE.** Fictional.',
				'',
				'**Case** CASE-1 · **Claim** CLM-1',
				'',
				'## Findings',
				'',
				'- **desk_audit** — supports (0.95): Something. *[death_certificate]*',
				'',
				'## Investigation trail',
				'',
				'| # | Agent | Tool |',
				'|---|---|---|',
				'| 1 | Desk Investigator | `read_document` |'
			].join('\n')
		);
		const order = [
			'<h2>',
			'<blockquote>',
			'<h3>Findings</h3>',
			'<ul>',
			'<h3>Investigation trail',
			'<table>'
		];
		let at = -1;
		for (const fragment of order) {
			const next = html.indexOf(fragment);
			expect(next, fragment).toBeGreaterThan(at);
			at = next;
		}
	});
});

// ── hostile input ────────────────────────────────────────────────────────────
// The artefact is Markdown written by `crew/actions.py`, but the prose inside it — an officer's
// explanation, a reviewer's summary, a committee question — is model output. It is scrubbed for
// identifiers, never for markup, and the page renders it through `{@html}`. These are the payloads
// that defeat naive escaping.

describe('a model cannot introduce markup', () => {
	const payloads = [
		'<script>alert(1)</script>',
		'<img src=x onerror=alert(1)>',
		'<svg/onload=alert(1)>',
		'<iframe src="javascript:alert(1)"></iframe>',
		'"><script>alert(1)</script>',
		"'><img src=x onerror=alert(1)>",
		'<a href="javascript:alert(1)">click</a>',
		'<style>body{display:none}</style>',
		'<!--<script>alert(1)</script>-->'
	];

	for (const payload of payloads) {
		it(`neutralises ${payload.slice(0, 32)}`, () => {
			const html = renderMarkdown(`## Explanation\n\n${payload}\n`);
			// What matters is that no live element reaches the page. An `onerror=` sitting inside
			// escaped text is inert prose, so asserting on that substring would fail a correct result.
			const tags = html.match(/<\/?[a-z][a-z0-9]*/gi) ?? [];
			const allowed = ['<h3', '</h3', '<p', '</p', '<code', '</code', '<strong', '</strong'];
			expect(tags.filter((tag) => !allowed.includes(tag.toLowerCase()))).toEqual([]);
			// escaped, not deleted: the reader still sees what the model wrote
			expect(html).toContain('&lt;');
		});
	}

	it('escapes a payload inside a code span', () => {
		expect(renderInline('`<script>alert(1)</script>`')).toBe(
			'<code>&lt;script&gt;alert(1)&lt;/script&gt;</code>'
		);
	});

	it('escapes a payload inside bold', () => {
		expect(renderInline('**<img src=x onerror=alert(1)>**')).toBe(
			'<strong>&lt;img src=x onerror=alert(1)&gt;</strong>'
		);
	});

	it('escapes a payload inside a table cell', () => {
		const html = renderMarkdown(
			'| Finding | Note |\n|---|---|\n| desk | <script>alert(1)</script> |\n'
		);
		expect(html).toContain('&lt;script&gt;');
		expect(html).not.toContain('<script>');
	});

	it('escapes a payload in a heading', () => {
		expect(renderMarkdown('## <img src=x onerror=alert(1)>\n')).not.toContain('<img');
	});

	it('does not decode an entity the model wrote, which would let it escape twice', () => {
		// If &lt;script&gt; were passed through, a second pass anywhere would yield a live tag.
		expect(renderInline('&lt;script&gt;')).toBe('&amp;lt;script&amp;gt;');
	});

	it("is not confused by JavaScript's replacement patterns", () => {
		// $&, $` and $' are special in String.replace replacements; text containing them must survive.
		expect(renderInline('a `$&` and `$`' + "` and `$'` b")).toContain('$&amp;');
		expect(renderInline('**$& $\u0060 $\u0027**')).toContain('<strong>');
	});

	it('leaves an unclosed tag inert', () => {
		expect(renderMarkdown('<script\n')).not.toContain('<script');
	});
});
