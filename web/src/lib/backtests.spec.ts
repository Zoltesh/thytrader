import { describe, expect, it } from 'vitest';
import {
	formatBrokerAssumptions,
	formatPercent,
	shortFingerprint,
	type BacktestSummary
} from './backtests';

const apiSummary = {
	initial_equity: '10000',
	final_equity: '10198.15',
	total_net_pnl: '198.15',
	total_return_fraction: '0.019815',
	gross_profit: '198.15',
	gross_loss: '0',
	win_rate: '1',
	profit_factor: null,
	average_win: '198.15',
	average_loss: null,
	trade_count: 1,
	winning_trade_count: 1,
	maximum_drawdown: '0',
	maximum_drawdown_fraction: '0',
	exposure_bars: 1,
	evaluation_bars: 2,
	total_spread_cost: '0.10'
} satisfies BacktestSummary;

describe('backtest presentation', () => {
	it('formats stored return fractions as percentages for display', () => {
		expect(formatPercent('0.01981496505')).toBe('1.98%');
	});

	it('matches the winning-trade API wire key', () => {
		expect(apiSummary.winning_trade_count).toBe(1);
	});

	it('formats very large stored return fractions without Number overflow', () => {
		const fraction = `1${'0'.repeat(400)}`;
		expect(formatPercent(fraction)).toBe(`1${'0'.repeat(402)}.00%`);
	});

	it('shortens a canonical fingerprint without discarding its identity prefix', () => {
		const fingerprint = `sha256:${'a'.repeat(64)}`;
		expect(shortFingerprint(fingerprint)).toBe(`sha256:${'a'.repeat(9)}…${'a'.repeat(8)}`);
	});

	it('renders disclosed V2 broker assumptions instead of inventing a spread', () => {
		expect(
			formatBrokerAssumptions({
				price_model: 'constant_spread_bps',
				spread_bps: '10',
				fill_policy: 'full',
				trigger_evaluation: 'bid_side',
				equity_marking: 'bid_close'
			})
		).toContain('10 bps constant spread');
	});
});
