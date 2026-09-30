import { describe, expect, it } from 'vitest';
import {
	backtestListPageIsFull,
	formatBacktestListBound,
	formatFillFee,
	formatListSpreadCue,
	formatPercent,
	formatPublishedCosts,
	formatSpreadCostNote,
	optionalSpreadStress,
	parseResultFingerprintParam,
	shortFingerprint,
	spreadStressBps,
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

	it('surfaces published cost assumptions without inventing missing fields', () => {
		expect(
			formatPublishedCosts({
				maker_fee_rate: '0.001',
				taker_fee_rate: '0.002',
				fixed_slippage_bps: '10'
			})
		).toBe(
			'maker 0.10% · taker 0.20% · fixed slippage 10 bps on taker exits (published research-run CostAssumptions, not observed Coinbase fees)'
		);
		expect(formatPublishedCosts(null)).toBe(
			'Published maker/taker fee rates and fixed_slippage_bps are not included in this response.'
		);
		expect(formatPublishedCosts({ maker_fee_rate: '0.001' })).toContain('taker fee not recorded');
	});

	it('shows per-fill fee rates when the ledger recorded them', () => {
		expect(formatFillFee({ fee: '0.03', fee_rate: '0.002' })).toBe('$0.03 (0.20%)');
		expect(formatFillFee({ fee: '0.03' })).toBe('$0.03 (fee rate not recorded)');
	});

	it('discloses spread stress only when the run was stressed', () => {
		expect(spreadStressBps({ spread_bps: '0' })).toBeNull();
		expect(spreadStressBps({ spread_bps: '0.000' })).toBeNull();
		expect(spreadStressBps(null)).toBeNull();
		expect(spreadStressBps({ spread_bps: '12.5' })).toBe('12.5');
		expect(formatSpreadCostNote({ spread_bps: '0' }, null)).toBeNull();
		expect(formatSpreadCostNote(null, undefined)).toBeNull();
		expect(formatSpreadCostNote({ spread_bps: '10' }, '0.10')).toBe(
			'Spread stress 10 bps (total bid-ask) · total modeled spread cost $0.10. This is a disclosed stress input, not observed bid/ask data.'
		);
		expect(formatSpreadCostNote(null, '0.10')).toContain('total modeled spread cost $0.10');
	});

	it('sends spread stress only when it is non-empty and non-zero', () => {
		expect(optionalSpreadStress('')).toEqual({});
		expect(optionalSpreadStress('  ')).toEqual({});
		expect(optionalSpreadStress('0')).toEqual({});
		expect(optionalSpreadStress('0.0')).toEqual({});
		expect(optionalSpreadStress(' 8 ')).toEqual({ spread_bps: '8' });
		expect(optionalSpreadStress('abc')).toEqual({ spread_bps: 'abc' });
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
