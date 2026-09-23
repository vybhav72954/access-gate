import { loadEvaluation, loadMeta } from '$lib/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => ({
	evaluation: await loadEvaluation(fetch),
	meta: await loadMeta(fetch)
});
