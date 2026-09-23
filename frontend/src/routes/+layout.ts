import { loadMeta } from '$lib/load';
import type { LayoutLoad } from './$types';

// The whole site is prerendered: no server, no runtime data fetching.
export const prerender = true;
export const ssr = true;
export const trailingSlash = 'always';

// The rail carries the run it is showing on every page, so the layout reads the manifest itself.
// It is the smallest of the four exports and it is inlined once per page by the prerenderer.
export const load: LayoutLoad = async ({ fetch }) => ({ meta: await loadMeta(fetch) });
