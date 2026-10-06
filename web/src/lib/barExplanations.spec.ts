import { describe, expect, it } from 'vitest';
import { indicatorText, isExitFill, outcomeLabel } from './barExplanations';

describe('bar explanation presentation', () => {
	it('does not turn an undefined indicator into zero', () => {
		expect(indicatorText([{ indicator_id: 'sma', value: null }])).toBe('sma=undefined');
		expect(outcomeLabel(null)).toBe('no signal-exit rule');
		expect(outcomeLabel('undefined')).toBe('undefined');
	});

	it('distinguishes an evaluation-end exit from an entry fill', () => {
		expect(isExitFill({ price: '1', quantity: '1', notional: '1', fee: '0', fee_rate: '0' })).toBe(
			false
		);
		expect(
			isExitFill({
				price: '1',
				quantity: '1',
				notional: '1',
				fee: '0',
				fee_rate: '0',
				reason: 'evaluation_end',
				gross_pnl: '1',
				net_pnl: '1',
				holding_bars: 1
			})
		).toBe(true);
	});
});
