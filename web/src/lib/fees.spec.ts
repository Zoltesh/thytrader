import { describe, expect, it } from 'vitest';

import {
	feeRatesMatch,
	formatFeeProfileAsOf,
	formatResearchFeeSourceChip,
	formatTierContext,
	readResearchFeeSuggestion,
	researchFeeFieldSource,
	shouldPrefillPaperFeeRates,
	shouldPrefillResearchFeeRates,
	type FeeProfile,
	type ResearchFeeSuggestion
} from './fees';

const suggestedProfile: FeeProfile = {
	taker_fee_rate: '0.0075',
	maker_fee_rate: '0.0035',
	usd_volume_30d: '25000',
	fee_tier: 'Advanced 1',
	as_of: '2026-09-13T16:00:00Z',
	source: 'coinbase',
	suggested_maker_fee_rate: '0.0035',
	suggested_taker_fee_rate: '0.0075',
	suggestion_source: 'coinbase_account',
	suggestion_unavailable_reason: null,
	suggestion_fee_tier: 'Advanced 1',
	suggestion_fetched_at: '2026-09-13T16:00:00Z',
	next_tier: {
		pricing_tier: 'Advanced 2',
		maker_fee_rate: '0.0015',
		taker_fee_rate: '0.003',
		spot_volume_from: '50000',
		spot_volume_to: '100000'
	},
	qualification: 'spot_volume',
	qualification_value: '25000',
	next_tier_threshold: '50000'
};

const suggestion: ResearchFeeSuggestion = {
	makerFeeRate: '0.0035',
	takerFeeRate: '0.0075',
	feeTier: 'Advanced 1',
	fetchedAt: '2026-09-13T16:00:00Z',
	nextTier: 'Advanced 2 from $50,000 30-day spot volume: 0.0015 maker / 0.003 taker (now $25,000)'
};

describe('account-rate suggestions (ADR 0090)', () => {
	it("prefills the account rates and names Coinbase's next tier (ADR 0144)", () => {
		const intro = readResearchFeeSuggestion({
			...suggestedProfile,
			maker_fee_rate: '0.005',
			taker_fee_rate: '0.009',
			usd_volume_30d: '710.4',
			fee_tier: 'Intro',
			suggested_maker_fee_rate: '0.005',
			suggested_taker_fee_rate: '0.009',
			suggestion_fee_tier: 'Intro',
			next_tier: {
				pricing_tier: 'Advanced 1',
				maker_fee_rate: '0.0035',
				taker_fee_rate: '0.0075',
				spot_volume_from: '10000',
				spot_volume_to: '50000'
			},
			qualification_value: '710.4',
			next_tier_threshold: '10000'
		});
		expect(intro?.makerFeeRate).toBe('0.005');
		expect(intro?.takerFeeRate).toBe('0.009');
		expect(intro === null ? '' : formatTierContext(intro)).toBe(
			'Account rates (Intro), as Coinbase bills them. Next tier Advanced 1 from $10,000 30-day spot volume: 0.0035 maker / 0.0075 taker (now $710).'
		);
	});

	it('says nothing about a next tier Coinbase does not report', () => {
		const top = readResearchFeeSuggestion({ ...suggestedProfile, next_tier: null });
		expect(top?.nextTier).toBe('');
		expect(top === null ? '' : formatTierContext(top)).toBe(
			'Account rates (Advanced 1), as Coinbase bills them.'
		);
	});

	it('ignores a retired schedule-sourced payload', () => {
		expect(
			readResearchFeeSuggestion({
				...suggestedProfile,
				suggestion_source: 'coinbase_fee_schedule' as unknown as 'coinbase_account'
			})
		).toBeNull();
	});
});

describe('formatFeeProfileAsOf', () => {
	it('formats a present UTC as_of with the locale timestamp', () => {
		expect(formatFeeProfileAsOf('2026-08-17T12:00:00Z')).toBe(
			new Date('2026-08-17T12:00:00Z').toLocaleString()
		);
	});

	it('returns null when as_of is missing or not a timestamp', () => {
		expect(formatFeeProfileAsOf('')).toBeNull();
		expect(formatFeeProfileAsOf('   ')).toBeNull();
		expect(formatFeeProfileAsOf('not-a-timestamp')).toBeNull();
	});
});

describe('readResearchFeeSuggestion', () => {
	it('reads account suggestion fields and the next tier', () => {
		expect(readResearchFeeSuggestion(suggestedProfile)).toEqual(suggestion);
	});

	it('fails closed for demo or missing suggestion metadata', () => {
		expect(
			readResearchFeeSuggestion({
				...suggestedProfile,
				suggestion_source: 'unavailable',
				suggested_maker_fee_rate: null,
				suggested_taker_fee_rate: null
			})
		).toBeNull();
		expect(
			readResearchFeeSuggestion({
				taker_fee_rate: '0.0060',
				maker_fee_rate: '0.0040',
				usd_volume_30d: '15250.00',
				fee_tier: 'Tier 1',
				as_of: '2026-08-17T12:00:00Z',
				source: 'coinbase'
			})
		).toBeNull();
	});
});

describe('research fee field source', () => {
	it('prefills only empty untouched fields', () => {
		expect(
			shouldPrefillResearchFeeRates({
				makerFeeRate: '',
				takerFeeRate: '',
				touched: false,
				suggestion
			})
		).toBe(true);
		expect(
			shouldPrefillResearchFeeRates({
				makerFeeRate: '0.001',
				takerFeeRate: '',
				touched: false,
				suggestion
			})
		).toBe(false);
		expect(
			shouldPrefillResearchFeeRates({
				makerFeeRate: '',
				takerFeeRate: '',
				touched: true,
				suggestion
			})
		).toBe(false);
	});

	it('treats equivalent decimal spellings as the same suggested rates', () => {
		expect(feeRatesMatch('0.0025', '0.00250')).toBe(true);
		expect(feeRatesMatch('0.0025', '0.0015')).toBe(false);
	});

	it('labels suggested, stale, custom, and unavailable states honestly', () => {
		const later: ResearchFeeSuggestion = {
			...suggestion,
			makerFeeRate: '0.0015',
			takerFeeRate: '0.0025',
			fetchedAt: '2026-09-13T18:00:00Z'
		};
		expect(
			researchFeeFieldSource({
				makerFeeRate: '0.0035',
				takerFeeRate: '0.0075',
				applied: suggestion,
				latest: suggestion,
				loading: false
			})
		).toBe('suggested');
		expect(
			researchFeeFieldSource({
				makerFeeRate: '0.0035',
				takerFeeRate: '0.0075',
				applied: suggestion,
				latest: later,
				loading: false
			})
		).toBe('stale-suggestion');
		expect(
			researchFeeFieldSource({
				makerFeeRate: '0.001',
				takerFeeRate: '0.002',
				applied: suggestion,
				latest: suggestion,
				loading: false
			})
		).toBe('custom');
		expect(
			researchFeeFieldSource({
				makerFeeRate: '',
				takerFeeRate: '',
				applied: null,
				latest: null,
				loading: false
			})
		).toBe('unavailable');
		expect(
			formatResearchFeeSourceChip('suggested', formatFeeProfileAsOf('2026-09-13T16:00:00Z'))
		).toBe(
			`Suggested from your Coinbase account rates (as of ${new Date('2026-09-13T16:00:00Z').toLocaleString()})`
		);
		expect(formatResearchFeeSourceChip('custom', null)).toBe('Custom');
		expect(formatResearchFeeSourceChip('unavailable', null)).toBe(
			'Coinbase account fee rates unavailable. Enter modeled rates.'
		);
	});
});

describe('shouldPrefillPaperFeeRates', () => {
	it('prefills documented defaults from a suggestion without overwriting edits', () => {
		expect(
			shouldPrefillPaperFeeRates({
				makerFeeRate: '',
				takerFeeRate: '',
				touched: false,
				suggestion
			})
		).toBe(true);
		expect(
			shouldPrefillPaperFeeRates({
				makerFeeRate: '0.001',
				takerFeeRate: '0.002',
				touched: false,
				suggestion
			})
		).toBe(true);
		expect(
			shouldPrefillPaperFeeRates({
				makerFeeRate: '0.001',
				takerFeeRate: '0.002',
				touched: true,
				suggestion
			})
		).toBe(false);
		expect(
			shouldPrefillPaperFeeRates({
				makerFeeRate: '0.0035',
				takerFeeRate: '0.0075',
				touched: false,
				suggestion
			})
		).toBe(false);
	});
});
