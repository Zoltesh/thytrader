import { describe, expect, it } from 'vitest';
import {
	BACKTEST_ENGINE,
	BACKTEST_MODEL_HONESTY,
	backtestModelAssumptions,
	formatValidityLimit
} from './backtest-model';

describe('backtest model disclosure', () => {
	it('names the one unversioned model', () => {
		expect(BACKTEST_ENGINE).toBe('thytrader-backtest');
		expect(BACKTEST_MODEL_HONESTY).toContain('not a promise');
	});

	it('lists every assumption once and never names engine variants', () => {
		const assumptions = backtestModelAssumptions();
		const keys = assumptions.map((assumption) => assumption.key);
		expect(new Set(keys).size).toBe(keys.length);
		expect(keys).toEqual(
			expect.arrayContaining([
				'signal_timing',
				'maker_entries',
				'unfilled_entries',
				'stops_and_targets',
				'time_exit',
				'signal_exit',
				'evaluation_end',
				'fees',
				'slippage',
				'spread_stress',
				'queue_position'
			])
		);
		const text = assumptions.map((assumption) => `${assumption.label} ${assumption.detail}`).join();
		expect(text).not.toMatch(/\bV[1-4]\b/);
		expect(text).not.toContain(['thytrader', 'bar', ''].join('-'));
	});

	it('names max_entry_wait_bars or substitutes the strategy value', () => {
		const detail = (wait: number | null): string =>
			backtestModelAssumptions(wait).find((row) => row.key === 'unfilled_entries')?.detail ?? '';
		expect(detail(null)).toContain('max_entry_wait_bars candles');
		expect(detail(3)).toContain('3 candles (max_entry_wait_bars)');
		expect(detail(1)).toContain('1 candle (max_entry_wait_bars)');
		expect(detail(0)).toContain('max_entry_wait_bars candles');
	});

	it('explains disclosed validity limits and keeps unknown codes visible', () => {
		expect(formatValidityLimit('maker_touch_full_fill')).toContain('fill completely');
		expect(formatValidityLimit('stop_before_tp_same_bar')).toContain('stop is assumed');
		expect(formatValidityLimit('spot_short_synthetic')).toContain('synthetic');
		expect(formatValidityLimit('new_code')).toBe('Modeling limit: new_code');
	});

	it('explains the futures modeling limits (ADR 0128)', () => {
		expect(formatValidityLimit('futures_constant_margin')).toContain('constant');
		expect(formatValidityLimit('futures_conservative_liquidation')).toContain('before stops');
		expect(formatValidityLimit('futures_shared_usdc_collateral')).toContain('USDC');
		expect(formatValidityLimit('futures_constant_funding')).toContain('constant hourly rate');
		expect(formatValidityLimit('futures_funding_at_bar_close')).toContain('bar close');
	});
});
