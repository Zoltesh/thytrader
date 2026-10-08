/**
 * Portfolio workspace view logic: tabs, which actions make sense, proposal review,
 * backtest result reading, and the sleeve picker. Re-exported by `portfolios.ts`.
 */
import type { Deployment } from './deployments';
import { compareDecimalStrings, subtractDecimalStrings } from './portfolio';
import type { StrategyLibraryEntry } from './strategies-types';
import type {
	Portfolio,
	PortfolioBacktestJob,
	PortfolioBacktestResult,
	PortfolioCorrelation,
	PortfolioDeployment,
	PortfolioMode,
	PortfolioSleeveResult,
	Proposal
} from './portfolios-types';

export type PortfolioTab = 'sleeves' | 'backtest' | 'manager' | 'limits';

export const PORTFOLIO_TABS: readonly { id: PortfolioTab; label: string }[] = [
	{ id: 'sleeves', label: 'Sleeves' },
	{ id: 'backtest', label: 'Portfolio backtest' },
	{ id: 'manager', label: 'Manager' },
	{ id: 'limits', label: 'Limits' }
];

/** Run PnL is unknown when current portfolio equity is not certified. */
export function portfolioRunPnl(
	view: Pick<PortfolioDeployment, 'breaker' | 'capital_quote'> | null
): string | null {
	return view === null || view.breaker.equity === null
		? null
		: subtractDecimalStrings(view.breaker.equity, view.capital_quote);
}

/** Which portfolio-wide actions make sense now. */
export function portfolioActions(view: PortfolioDeployment | null): {
	start: boolean;
	pause: boolean;
	resume: boolean;
	stop: boolean;
} {
	if (view === null) return { start: false, pause: false, resume: false, stop: false };
	const books = [...view.sleeves.map((sleeve) => sleeve.deployment), ...view.detached];
	const running = books.some((book) => book?.status === 'running');
	const paused = books.some((book) => book?.status === 'paused');
	const idle = view.sleeves.some(
		(sleeve) => sleeve.deployment === null || sleeve.deployment.status === 'stopped'
	);
	const latched = view.breaker.latched;
	return {
		start: idle && !latched && view.sleeves.length > 0,
		pause: running,
		resume: paused && !latched,
		stop: running || paused
	};
}

/** Approving a resume on a live portfolio re-arms real orders: it needs the checkbox. */
export function approvalNeedsLiveAck(
	mode: PortfolioMode,
	proposal: Pick<Proposal, 'kind'>
): boolean {
	return mode === 'live' && proposal.kind === 'resume_sleeve';
}

/** The agent-panel message behind "Ask why": the proposal as context, then the question. */
export function askWhyPrompt(
	portfolio: Pick<Portfolio, 'name' | 'mode'>,
	proposal: Proposal
): string {
	const evidence =
		proposal.evidence.length === 0
			? 'none cited'
			: proposal.evidence.map((item) => `${item.kind} ${item.ref}`).join('; ');
	return [
		`Explain this manager proposal for the ${portfolio.mode} portfolio “${portfolio.name}” before I decide.`,
		`Proposal ${proposal.proposal_id}: ${proposal.summary}`,
		`Rationale: ${proposal.rationale}`,
		`Evidence: ${evidence}`,
		`Why it needs approval: ${proposal.approval_reason ?? 'not stated'}`,
		'Check the evidence against the portfolio briefing, say what could go wrong, and recommend approve or decline. Do not approve or decline it yourself.'
	].join('\n');
}

export function isJobActive(job: Pick<PortfolioBacktestJob, 'status'>): boolean {
	return job.status === 'queued' || job.status === 'running';
}

/** Coefficient of one sleeve pair regardless of order; undefined pairs read null. */
export function pairCoefficient(
	correlation: PortfolioCorrelation,
	left: string,
	right: string
): string | null {
	const pair = correlation.pairs.find(
		(item) =>
			(item.sleeve_ids[0] === left && item.sleeve_ids[1] === right) ||
			(item.sleeve_ids[0] === right && item.sleeve_ids[1] === left)
	);
	return pair?.coefficient ?? null;
}

/** The sleeve with the highest standalone return (first on ties). */
export function bestSleeve(result: PortfolioBacktestResult): PortfolioSleeveResult | null {
	let best: PortfolioSleeveResult | null = null;
	for (const sleeve of result.sleeves) {
		if (
			best === null ||
			compareDecimalStrings(sleeve.total_return_fraction, best.total_return_fraction) > 0
		) {
			best = sleeve;
		}
	}
	return best;
}

/** Running or paused bots (not stopped) of one strategy in the portfolio's mode. */
export function sleeveBots(
	inventory: readonly Deployment[] | null,
	strategyId: string,
	mode: PortfolioMode
): Deployment[] {
	if (inventory === null) return [];
	return inventory.filter(
		(deployment) =>
			deployment.strategy_id === strategyId &&
			deployment.mode === mode &&
			deployment.status !== 'stopped'
	);
}

export type PickerOption = {
	entry: StrategyLibraryEntry;
	quote: string | null;
	/** Why it cannot be added, or null when it can. */
	disabledReason: string | null;
	/** Shown even when it can be added (invalid rules still backtest-blocked). */
	warning: string | null;
};

/** Library strategies for the sleeve picker, filtered by name or market. */
export function pickerOptions(
	entries: readonly StrategyLibraryEntry[],
	portfolio: Portfolio,
	query: string
): PickerOption[] {
	const needle = query.trim().toLowerCase();
	const held = new Set(portfolio.sleeves.map((sleeve) => sleeve.strategy_id));
	return entries
		.filter(
			(entry) =>
				needle === '' ||
				entry.name.toLowerCase().includes(needle) ||
				(entry.product_id ?? '').toLowerCase().includes(needle)
		)
		.map((entry) => {
			const quote = entry.product_id === null ? null : quoteOf(entry.product_id);
			let disabledReason: string | null = null;
			if (held.has(entry.strategy_id)) disabledReason = 'Already a sleeve';
			else if (quote === null) disabledReason = 'No readable market yet';
			else if (quote !== portfolio.quote_currency)
				disabledReason = `Trades in ${quote}; this portfolio holds ${portfolio.quote_currency}`;
			return {
				entry,
				quote,
				disabledReason,
				warning: entry.valid ? null : 'Rules invalid: fix before backtesting'
			};
		});
}

function quoteOf(productId: string): string | null {
	const separator = productId.indexOf('-');
	return separator === -1 ? null : productId.slice(separator + 1);
}

export function parseTab(value: string | null): PortfolioTab {
	return PORTFOLIO_TABS.some((tab) => tab.id === value) ? (value as PortfolioTab) : 'sleeves';
}
