/**
 * A small Markdown renderer for the artefacts.
 *
 * The artefacts are written by `crew/actions.py` and use a fixed, known subset: headings, a
 * blockquote banner, bold and italic spans, inline code, bullet lists and pipe tables. Rendering
 * that subset here — rather than pulling a general parser — keeps two properties that matter:
 * every scrap of text is HTML-escaped before anything else happens, so free text written by a model
 * can never introduce markup; and the output carries the classes the document stylesheet expects.
 */

const ESCAPES: Record<string, string> = {
	'&': '&amp;',
	'<': '&lt;',
	'>': '&gt;',
	'"': '&quot;',
	"'": '&#39;'
};

export function escapeHtml(text: string): string {
	return text.replace(/[&<>"']/g, (char) => ESCAPES[char] ?? char);
}

/** Inline spans, applied to already-escaped text. Escaped pipes (`\|`) come back as pipes. */
export function renderInline(text: string): string {
	return escapeHtml(text)
		.replace(/\\\|/g, '|')
		.replace(/`([^`]+)`/g, '<code>$1</code>')
		.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
		.replace(/(^|[\s(])\*([^*]+)\*(?=$|[\s.,;:)])/g, '$1<em>$2</em>');
}

type Row = string[];

function splitRow(line: string): Row {
	return line
		.trim()
		.replace(/^\|/, '')
		.replace(/\|$/, '')
		.split(/(?<!\\)\|/)
		.map((cell) => cell.trim());
}

function isSeparator(line: string): boolean {
	const cells = splitRow(line);
	return cells.length > 0 && cells.every((cell) => /^:?-{2,}:?$/.test(cell));
}

function renderTable(lines: string[]): string {
	const [headerLine, , ...bodyLines] = lines;
	if (headerLine === undefined) return '';
	const header = splitRow(headerLine);
	const head = header.map((cell) => `<th scope="col">${renderInline(cell)}</th>`).join('');
	const body = bodyLines
		.map((line) => {
			const cells = splitRow(line)
				.map((cell) => `<td>${renderInline(cell) || '<span class="empty">—</span>'}</td>`)
				.join('');
			return `<tr>${cells}</tr>`;
		})
		.join('');
	return `<div class="doc-table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/** Render the artefact subset of Markdown to HTML. Every input is escaped before it is used. */
export function renderMarkdown(markdown: string): string {
	if (!markdown || !markdown.trim()) return '';
	const lines = markdown.replace(/\r\n/g, '\n').split('\n');
	const out: string[] = [];

	let paragraph: string[] = [];
	let list: string[] = [];
	let quote: string[] = [];
	let table: string[] = [];

	const flushParagraph = () => {
		if (paragraph.length) out.push(`<p>${renderInline(paragraph.join(' '))}</p>`);
		paragraph = [];
	};
	const flushList = () => {
		if (list.length) {
			out.push(`<ul>${list.map((item) => `<li>${renderInline(item)}</li>`).join('')}</ul>`);
		}
		list = [];
	};
	const flushQuote = () => {
		if (quote.length) out.push(`<blockquote><p>${renderInline(quote.join(' '))}</p></blockquote>`);
		quote = [];
	};
	const flushTable = () => {
		if (table.length) out.push(renderTable(table));
		table = [];
	};
	const flushAll = () => {
		flushParagraph();
		flushList();
		flushQuote();
		flushTable();
	};

	for (const raw of lines) {
		const line = raw.trimEnd();
		const trimmed = line.trim();

		if (table.length) {
			// A table runs until a line that is not a row.
			if (trimmed.startsWith('|')) {
				table.push(trimmed);
				continue;
			}
			flushTable();
		}

		if (!trimmed) {
			flushAll();
			continue;
		}

		const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed);
		if (heading && heading[1] && heading[2] !== undefined) {
			flushAll();
			const level = Math.min(heading[1].length + 1, 6); // the artefact's `#` is an h2 in the page
			out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
			continue;
		}

		if (/^(\*\s*){3,}$|^-{3,}$|^_{3,}$/.test(trimmed)) {
			flushAll();
			out.push('<hr />');
			continue;
		}

		if (trimmed.startsWith('>')) {
			flushParagraph();
			flushList();
			quote.push(trimmed.replace(/^>\s?/, ''));
			continue;
		}
		flushQuote();

		if (trimmed.startsWith('|')) {
			flushParagraph();
			flushList();
			table.push(trimmed);
			continue;
		}

		const bullet = /^[-*]\s+(.*)$/.exec(trimmed);
		if (bullet && bullet[1] !== undefined) {
			flushParagraph();
			list.push(bullet[1]);
			continue;
		}
		flushList();

		paragraph.push(trimmed);
	}

	// A table whose separator row never arrived is not a table.
	if (table.length && !table.some((line, index) => index === 1 && isSeparator(line))) {
		paragraph.push(...table);
		table = [];
	}
	flushAll();
	return out.join('\n');
}
