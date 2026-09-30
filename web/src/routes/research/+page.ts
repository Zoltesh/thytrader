import { redirect } from '@sveltejs/kit';
import { isStrategyFingerprint, workspaceHref } from '$lib/strategy-workspace';
import type { PageLoad } from './$types';

/**
 * `/research?strategy=X[&strategy_fingerprint=F]` moved to the strategy
 * workspace's Test stage. A strategy has one current definition (ADR 0082),
 * so a legacy `strategy_fingerprint` only identifies the owning strategy.
 */
export const load: PageLoad = ({ url }) => {
	const strategy = url.searchParams.get('strategy');
	if (strategy !== null && strategy !== '') {
		redirect(307, workspaceHref(strategy, 'test'));
	}
	const fingerprint = url.searchParams.get('strategy_fingerprint');
	if (isStrategyFingerprint(fingerprint)) {
		// The workspace resolves a snapshot fingerprint to its owning strategy.
		redirect(307, workspaceHref(fingerprint, 'test'));
	}
	return {};
};
