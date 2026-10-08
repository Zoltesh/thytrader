/**
 * Pure text helpers of the portfolio Backtest tab (`BacktestTab.svelte` and its
 * result, correlation and runs components): the costs line and a return's tone.
 */
import { formatPercent } from '$lib/portfolio';
import type { PortfolioBacktestDetail } from '$lib/portfolios';

export function costsText(costs: PortfolioBacktestDetail['result']['costs']): string {
	const spreadText = costs.spread_bps === '0' ? '' : ` · spread stress ${costs.spread_bps} bps`;
	return `maker ${formatPercent(costs.maker_fee_rate)} · taker ${formatPercent(costs.taker_fee_rate)} · slippage ${costs.fixed_slippage_bps} bps${spreadText}`;
}

export function tone(fraction: string): 'pos' | 'neg' | '' {
	if (fraction.startsWith('-')) return 'neg';
	return /^0(?:\.0*)?$/.test(fraction) ? '' : 'pos';
}
