import { redirect } from '@sveltejs/kit';
import { workspaceHref } from '$lib/strategy-workspace';
import type { PageLoad } from './$types';

/**
 * `/research?strategy=X[&strategy_fingerprint=F]` moved to the strategy
 * workspace's Test stage. The fingerprint travels as `?version=` so the
 * workspace still fails closed when it does not belong to X.
 */
export const load: PageLoad = ({ url }) => {
	const strategy = url.searchParams.get('strategy');
	if (strategy !== null && strategy !== '') {
		redirect(
			307,
			workspaceHref(strategy, 'test', { version: url.searchParams.get('strategy_fingerprint') })
		);
	}
	return {};
};
