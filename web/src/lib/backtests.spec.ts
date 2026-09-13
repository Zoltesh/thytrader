import { describe, expect, it } from 'vitest';
import {
	backtestListPageIsFull,
	formatBacktestListBound,
	formatBrokerAssumptions,
	formatEngineFillAssumptions,
	formatFillFee,
	formatListSpreadCue,
	formatPercent,
	formatPublishedCosts,
	formatSameBarPolicy,
	formatSpreadCostNote,
	parseResultFingerprintParam,
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
			formatBrokerAssumptions(
				{
					price_model: 'constant_spread_bps',
					spread_bps: '10',
					fill_policy: 'full',
					trigger_evaluation: 'bid_side',
					equity_marking: 'bid_close'
				},
				'thytrader-bar-backtest-v2'
			)
		).toContain('10 bps constant spread');
	});

	it('does not describe V3 post-only limits as a constant spread', () => {
		expect(
			formatBrokerAssumptions(
				{
					price_model: 'post_only_limit',
					spread_bps: '0',
					fill_policy: 'resting_limit',
					trigger_evaluation: 'bar_extreme',
					equity_marking: 'last_close'
				},
				'thytrader-bar-backtest-v3'
			)
		).toBe('post-only limit · resting-limit fills · bar-extreme triggers · last-close marking');
	});

	it('fail-closes unknown engine contracts instead of labeling V1/V2 fills', () => {
		expect(
			formatBrokerAssumptions(
				{
					price_model: 'constant_spread_bps',
					spread_bps: '10',
					fill_policy: 'full',
					trigger_evaluation: 'bid_side',
					equity_marking: 'bid_close'
				},
				'thytrader-bar-backtest-v9'
			)
		).toContain('Unknown engine contract thytrader-bar-backtest-v9');
		expect(formatEngineFillAssumptions('thytrader-bar-backtest-v9')).toContain(
			'will not invent fill semantics'
		);
		expect(formatSameBarPolicy('thytrader-bar-backtest-v9')).toBe('Same-bar policy unlabeled');
	});

	it('describes V3 fill semantics without next-open taker or entry slippage', () => {
		expect(formatEngineFillAssumptions('thytrader-bar-backtest-v3')).toContain(
			'rests a post-only buy'
		);
		expect(formatEngineFillAssumptions('thytrader-bar-backtest-v3')).not.toContain(
			'next-open taker fill'
		);
		expect(formatEngineFillAssumptions('thytrader-bar-backtest-v1')).toContain(
			'next-open taker fill'
		);
	});

	it('surfaces published cost assumptions without inventing missing fields', () => {
		expect(
			formatPublishedCosts({
				maker_fee_rate: '0.001',
				taker_fee_rate: '0.002',
				fixed_slippage_bps: '10'
			})
		).toBe(
			'maker 0.10% · taker 0.20% · fixed slippage 10 bps (published research-run CostAssumptions, not observed Coinbase fees)'
		);
		expect(formatPublishedCosts(null)).toBe(
			'Published maker/taker fee rates and fixed_slippage_bps are not included in this response.'
		);
		expect(formatPublishedCosts({ maker_fee_rate: '0.001' })).toContain('taker fee not recorded');
		expect(
			formatPublishedCosts(
				{
					maker_fee_rate: '0.001',
					taker_fee_rate: '0.002',
					fixed_slippage_bps: '10'
				},
				'thytrader-bar-backtest-v3'
			)
		).toContain('V3 modeled fills do not apply this slippage');
	});

	it('shows per-fill fee rates when the ledger recorded them', () => {
		expect(formatFillFee({ fee: '0.03', fee_rate: '0.002' })).toBe('$0.03 (0.20%)');
		expect(formatFillFee({ fee: '0.03' })).toBe('$0.03 (fee rate not recorded)');
	});

	it('keeps V2 spread-cost copy and omits a V3 constant-spread claim', () => {
		expect(formatSpreadCostNote('thytrader-bar-backtest-v2', '0.10')).toContain(
			'Total modeled spread cost: $0.10.'
		);
		expect(formatSpreadCostNote('thytrader-bar-backtest-v3', null)).toBeNull();
		expect(formatSpreadCostNote('thytrader-bar-backtest-v2', null)).toBe(
			'Total modeled spread cost was not recorded on this result.'
		);
	});

	it('discloses the newest-first list bound without claiming completeness', () => {
		expect(formatBacktestListBound({ limit: 50, offset: 0, returned: 1 })).toBe(
			'Showing 1 (newest)'
		);
		expect(formatBacktestListBound({ limit: 50, offset: 50, returned: 10 })).toBe(
			'Showing 10 (newest-first, offset 50)'
		);
		expect(backtestListPageIsFull({ limit: 50, returned: 50 })).toBe(true);
		expect(backtestListPageIsFull({ limit: 50, returned: 49 })).toBe(false);
	});

	it('shows a list spread cue only when modeled spread was recorded', () => {
		expect(formatListSpreadCue('0.10')).toBe('spread $0.10 recorded');
		expect(formatListSpreadCue(null)).toBeNull();
		expect(formatListSpreadCue(undefined)).toBeNull();
		expect(formatListSpreadCue('')).toBeNull();
	});

	it('accepts only canonical result fingerprints from the query string', () => {
		const fingerprint = `sha256:${'a'.repeat(64)}`;
		expect(parseResultFingerprintParam(fingerprint)).toBe(fingerprint);
		expect(parseResultFingerprintParam('sha256:not-a-fingerprint')).toBeNull();
		expect(parseResultFingerprintParam(null)).toBeNull();
	});
});
