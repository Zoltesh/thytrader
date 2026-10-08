/** Pure helpers of the portfolio workspace (`PortfolioWorkspace.svelte`). */
import type { PortfolioActionResponse } from '$lib/portfolios-types';

/** Notice after a portfolio-wide or sleeve action, e.g. `Paused 2 sleeves. 1 could not: …`. */
export function outcomeNotice(result: PortfolioActionResponse): string {
	const changed = result.outcomes.filter(
		(item) => item.outcome !== 'unchanged' && item.outcome !== 'failed'
	);
	const failed = result.outcomes.filter((item) => item.outcome === 'failed');
	const verb = { start: 'Started', pause: 'Paused', resume: 'Resumed', stop: 'Stopped' }[
		result.action
	];
	const count = `${changed.length} sleeve${changed.length === 1 ? '' : 's'}`;
	const failures =
		failed.length === 0
			? ''
			: ` ${failed.length} could not: ${failed.map((item) => `${item.strategy_name} (${item.message ?? 'no reason'})`).join('; ')}.`;
	return `${verb} ${count}.${failures}`;
}
