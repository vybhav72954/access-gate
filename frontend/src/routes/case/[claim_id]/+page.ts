import { error } from '@sveltejs/kit';
import { loadCases, loadMeta } from '$lib/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, params }) => {
	const cases = await loadCases(fetch);
	const record = cases.find((c) => c.claim_id === params.claim_id);
	if (!record) error(404, `No case with claim id ${params.claim_id} in the exported record.`);
	const index = cases.indexOf(record);
	return {
		case: record,
		meta: await loadMeta(fetch),
		previous: cases[index - 1] ?? null,
		next: cases[index + 1] ?? null
	};
};
