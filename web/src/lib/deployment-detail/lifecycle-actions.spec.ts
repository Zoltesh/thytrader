import { describe, expect, it } from 'vitest';
import { acceptedAction, breakerLatchText, isAmbiguousTransportFailure } from './lifecycle-actions';

describe('acceptedAction', () => {
	it('names a flattening stop as a flatten and leaves every other action alone', () => {
		expect(acceptedAction('stop', true)).toBe('flatten');
		expect(acceptedAction('stop', false)).toBe('stop');
		expect(acceptedAction('pause', true)).toBe('pause');
		expect(acceptedAction('reset-breakers', true)).toBe('reset-breakers');
	});
});

describe('isAmbiguousTransportFailure', () => {
	it('treats Chromium and Firefox network failures as an unknown outcome', () => {
		expect(isAmbiguousTransportFailure('Failed to fetch')).toBe(true);
		expect(isAmbiguousTransportFailure('NetworkError when attempting to fetch resource.')).toBe(
			true
		);
	});

	it('keeps a server rejection a plain failure', () => {
		expect(isAmbiguousTransportFailure('Revision conflict.')).toBe(false);
	});
});

describe('breakerLatchText', () => {
	it('names each latched breaker in order', () => {
		expect(breakerLatchText({ daily_loss_latched: true, drawdown_latched: true })).toBe(
			'Daily-loss breaker latched · Drawdown breaker latched'
		);
		expect(breakerLatchText({ daily_loss_latched: false, drawdown_latched: true })).toBe(
			'Drawdown breaker latched'
		);
		expect(breakerLatchText({ daily_loss_latched: false, drawdown_latched: false })).toBe('');
	});
});
