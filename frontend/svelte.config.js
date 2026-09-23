import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
export default {
	preprocess: vitePreprocess(),
	kit: {
		// A fully static, prerendered site: no server, no database, no runtime API calls.
		adapter: adapter({ precompress: false, strict: true }),
		prerender: { handleHttpError: 'fail', handleMissingId: 'fail' },
		paths: { relative: true }
	}
};
