/**
 * Reading the exported record.
 *
 * Every page loads its data through SvelteKit's `fetch` inside a `load`, which runs at build time
 * because the whole site is prerendered. The JSON is therefore baked into the pages: the built site
 * makes no request at runtime, needs no server and works with the network off.
 */
import { base } from '$app/paths';
import type { Benchmark, Case, Evaluation, Meta } from './types';

export class DataError extends Error {
	constructor(
		readonly file: string,
		readonly detail: string
	) {
		super(`could not read ${file}: ${detail}`);
		this.name = 'DataError';
	}
}

type Fetch = typeof globalThis.fetch;

async function readJson<T>(fetch: Fetch, file: string): Promise<T> {
	let response: Response;
	try {
		response = await fetch(`${base}/data/${file}`);
	} catch (error) {
		throw new DataError(file, error instanceof Error ? error.message : String(error));
	}
	if (!response.ok) {
		throw new DataError(
			file,
			`${response.status} ${response.statusText}. Run the export first:\n` +
				'  cd .. && python -m scripts.export_frontend'
		);
	}
	try {
		return (await response.json()) as T;
	} catch (error) {
		throw new DataError(file, error instanceof Error ? error.message : 'invalid JSON');
	}
}

export const loadCases = (fetch: Fetch) => readJson<Case[]>(fetch, 'cases.json');
export const loadMeta = (fetch: Fetch) => readJson<Meta>(fetch, 'meta.json');
export const loadEvaluation = (fetch: Fetch) => readJson<Evaluation>(fetch, 'evaluation.json');
export const loadBenchmark = (fetch: Fetch) => readJson<Benchmark>(fetch, 'benchmark.json');
