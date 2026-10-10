import { describe, expect, it } from 'vitest';
import type { FuturesFeeEvidence } from '$lib/futures-book-api';
import { futuresFeeSourceText, futuresStartMissing, prefillFuturesFees } from './futures-start';

function evidence(overrides: Partial<FuturesFeeEvidence> = {}): FuturesFeeEvidence {
	return {
		status: 'available',
		maker_fee_rate: '0.0002',
		taker_fee_rate: '0.0005',
		fee_tier: 'Intro 1',
		as_of: '2026-10-10T00:00:00Z',
		fee_per_contract: null,
		fee_per_contract_source: 'operator_input',
		unavailable_reason: null,
		preview_product_id: null,
		preview_unavailable_reason: null,
		...overrides
	};
}

const blank = { cash: '', maker: '', taker: '', perContract: '' };

describe('futuresStartMissing', () => {
	it('requires cash and all three fees as decimals', () => {
		expect(futuresStartMissing(blank)).toEqual([
			'starting cash (USD)',
			'maker fee rate',
			'taker fee rate',
			'fee per contract (USD)'
		]);
		expect(
			futuresStartMissing({ cash: '1000', maker: '0.0002', taker: 'abc', perContract: '0.15' })
		).toEqual(['taker fee rate']);
		expect(
			futuresStartMissing({ cash: '1000', maker: '0', taker: '0.0005', perContract: '0.15' })
		).toEqual([]);
	});
});

describe('prefillFuturesFees', () => {
	it('fills blank rates from reported evidence and never invents a per-contract fee', () => {
		expect(prefillFuturesFees(blank, evidence())).toEqual({
			cash: '',
			maker: '0.0002',
			taker: '0.0005',
			perContract: ''
		});
	});

	it('keeps typed values and ignores unavailable evidence', () => {
		const typed = { cash: '500', maker: '0.001', taker: '', perContract: '0.2' };
		expect(prefillFuturesFees(typed, evidence({ fee_per_contract: '0.15' }))).toEqual({
			cash: '500',
			maker: '0.001',
			taker: '0.0005',
			perContract: '0.2'
		});
		expect(prefillFuturesFees(blank, evidence({ status: 'unavailable' }))).toEqual(blank);
		expect(prefillFuturesFees(blank, null)).toEqual(blank);
	});

	it('replaces the per-contract fee with an explicit quote', () => {
		const typed = { cash: '500', maker: '', taker: '', perContract: '0.2' };
		expect(
			prefillFuturesFees(typed, evidence({ fee_per_contract: '0.15' }), true).perContract
		).toBe('0.15');
	});
});

describe('futuresFeeSourceText', () => {
	it('names the source or why fields are blank', () => {
		expect(futuresFeeSourceText(null, 'loading')).toContain('Reading');
		expect(futuresFeeSourceText(null, 'error')).toContain('could not be read');
		expect(
			futuresFeeSourceText(
				evidence({ status: 'unavailable', unavailable_reason: 'unsupported' }),
				'ready'
			)
		).toBe('Coinbase reported no futures fee rates (unsupported); enter the rates you expect.');
		expect(futuresFeeSourceText(evidence(), 'ready')).toContain('enter it or quote it');
		expect(
			futuresFeeSourceText(
				evidence({ fee_per_contract: '0.15', preview_product_id: 'BIP-20DEC30-CDE' }),
				'ready'
			)
		).toContain('order preview of BIP-20DEC30-CDE (no order placed)');
	});
});
