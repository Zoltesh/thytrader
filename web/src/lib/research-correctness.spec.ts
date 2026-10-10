import { describe, expect, it } from 'vitest';
import {
	formatDiagnosticsFunnel,
	formatSkipReason,
	unfilledEntryLines,
	type BacktestDiagnostics
} from './backtests';
import { positionSnapshotText } from './decisions';
import { takeProfitMultiple, takeProfitPhrase } from './strategies';

/** ADR 0090: optional take-profit, backtest entry funnel, and untargeted positions. */

const zeroTrades: BacktestDiagnostics = {
	diagnostics_version: 'thytrader-backtest-diagnostics-v1',
	signals_matched: 12,
	entries_rested: 0,
	entries_filled: 0,
	entries_expired: 0,
	entries_repriced: 0,
	entries_refused_at_fill: 0,
	entries_unfilled_at_end: 0,
	entries_size_capped: 0,
	warmup_bars: 99,
	skipped: [{ reason: 'target_not_positive', count: 12 }]
};

describe('backtest diagnostics', () => {
	it('explains a zero-trade short with its geometry skip', () => {
		expect(formatDiagnosticsFunnel(zeroTrades)).toBe(
			'12 signals matched → 0 entries rested → 0 filled'
		);
		expect(formatSkipReason('target_not_positive')).toBe(
			'Short take-profit would be at or below zero'
		);
		expect(unfilledEntryLines(zeroTrades)).toEqual([]);
	});

	it('lists only the non-zero reasons a rested entry never traded', () => {
		expect(
			unfilledEntryLines({
				...zeroTrades,
				entries_rested: 3,
				entries_expired: 2,
				entries_unfilled_at_end: 1,
				skipped: [{ reason: 'target_not_positive', count: 9 }]
			})
		).toEqual(['2 expired unfilled and canceled', '1 still resting when the window ended']);
		expect(formatSkipReason('some_future_code')).toBe('some_future_code');
		expect(formatSkipReason('below_one_contract')).toContain('zero contracts');
		expect(formatSkipReason('expiry_window')).toContain('expiry');
	});
});

describe('optional take-profit', () => {
	it('reads and phrases reward/risk and none', () => {
		expect(takeProfitMultiple({ kind: 'reward_risk', multiple: '3' })).toBe('3');
		expect(takeProfitMultiple({ kind: 'none' })).toBeNull();
		expect(takeProfitPhrase({ kind: 'reward_risk', multiple: '3' })).toBe('take profit at 3× risk');
		expect(takeProfitPhrase({ kind: 'none' })).toBe('no take-profit');
	});

	it('describes a decision position without a target', () => {
		expect(
			positionSnapshotText({
				side: 'short',
				quantity: '2',
				entry_price: '30',
				stop_price: '39',
				target_price: null
			})
		).toBe('short 2 @ 30 · stop 39 · no take-profit');
	});
});
