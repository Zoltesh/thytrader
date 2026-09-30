import { describe, expect, it } from 'vitest';
import { tradeReview, type TradeReviewInput } from './trade-review';

const base: TradeReviewInput = {
	productId: 'BTC-USDC',
	side: 'long',
	entryKind: 'post_only_limit',
	limitPrice: '63380',
	quantity: '',
	quoteNotional: '50',
	stopPrice: '61800',
	takeProfitPrice: '66900'
};

describe('tradeReview', () => {
	it('computes loss at stop from quote notional and reward:risk exactly', () => {
		const review = tradeReview(base);
		expect(review.entry).toBe('63,380.00 USDC post-only limit');
		// 50 × (63380 − 61800) / 63380 = 1.2464…
		expect(review.maxLoss).toBe('1.25 USDC before fees');
		// (66900 − 63380) / (63380 − 61800) = 2.2278…
		expect(review.rewardRisk).toBe('2.23');
		expect(review.warnings).toEqual([]);
	});

	it('computes loss at stop from a base quantity', () => {
		const review = tradeReview({
			...base,
			quoteNotional: '',
			quantity: '0.01',
			limitPrice: '100000',
			stopPrice: '90000',
			takeProfitPrice: '120000'
		});
		expect(review.maxLoss).toBe('100.00 USDC before fees');
		expect(review.rewardRisk).toBe('2.00');
	});

	it('leaves a marketable entry unknown instead of guessing a price', () => {
		const review = tradeReview({ ...base, entryKind: 'marketable' });
		expect(review.entry).toBe('Marketable · fills at the market price');
		expect(review.maxLoss).toBe('Unknown');
		expect(review.rewardRisk).toBe('Unknown');
	});

	it('flags stops and targets on the wrong side for longs and shorts', () => {
		expect(tradeReview({ ...base, stopPrice: '64000' }).warnings[0]).toMatch(/below the entry/);
		const short = tradeReview({
			...base,
			side: 'short',
			stopPrice: '65000',
			takeProfitPrice: '60000'
		});
		expect(short.warnings).toEqual([]);
		expect(short.maxLoss).toBe('1.28 USDC before fees');
		expect(tradeReview({ ...base, side: 'short' }).warnings).toHaveLength(2);
	});

	it('is Unknown when inputs are blank or not decimals', () => {
		const review = tradeReview({ ...base, limitPrice: '', stopPrice: 'x' });
		expect(review.entry).toBe('Unknown until a limit price is entered');
		expect(review.maxLoss).toBe('Unknown');
	});
});
