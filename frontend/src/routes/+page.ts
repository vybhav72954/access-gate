import { loadCases, loadMeta } from '$lib/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => ({
	cases: await loadCases(fetch),
	meta: await loadMeta(fetch)
});
