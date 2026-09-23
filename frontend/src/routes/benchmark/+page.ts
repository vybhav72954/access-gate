import { loadBenchmark, loadMeta } from '$lib/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => ({
	benchmark: await loadBenchmark(fetch),
	meta: await loadMeta(fetch)
});
