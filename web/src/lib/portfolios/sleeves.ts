/**
 * Pure helpers of the Sleeves tab (`SleevesTab.svelte`, `SleevesTable.svelte`):
 * a sleeve's portfolio bot, whether that bot holds the sleeve, its paper/live
 * twin side, and the label of a bot of the same strategy outside the portfolio.
 */
import type { Deployment } from '$lib/deployments';
import type { PaperLiveFillComparison } from '$lib/fill-comparison';
import type { PortfolioDeployment, SleeveDeployment } from '$lib/portfolios-types';

/** The twin side opposite this sleeve bot, if a paper/live twin exists. */
export function twinOf(
	fills: readonly PaperLiveFillComparison[],
	deploymentId: string
): 'paper' | 'live' | null {
	for (const row of fills) {
		if (row.paper.deployment_id === deploymentId) return 'live';
		if (row.live.deployment_id === deploymentId) return 'paper';
	}
	return null;
}

/** The sleeve's portfolio bot (running, paused, or its last stopped one). */
export function sleeveBot(
	deployment: PortfolioDeployment | null,
	sleeveId: string
): SleeveDeployment | null {
	return deployment?.sleeves.find((book) => book.sleeve_id === sleeveId)?.deployment ?? null;
}

/** The bot holds its sleeve (running or paused): the sleeve stops instead of being removed. */
export function occupied(bot: SleeveDeployment | null): boolean {
	return bot !== null && (bot.status === 'running' || bot.status === 'paused');
}

/** `Running bot`, `Paused bot`, … for a bot of the sleeve's strategy outside this portfolio. */
export function botLabel(deployment: Deployment): string {
	const status = deployment.status;
	return `${status.charAt(0).toUpperCase()}${status.slice(1)} bot`;
}
