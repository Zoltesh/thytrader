import { describe, expect, it } from 'vitest';

import {
	feeRatesMatch,
	formatFeeProfileAsOf,
	formatResearchFeeSourceChip,
	readResearchFeeSuggestion,
	researchFeeFieldSource,
	shouldPrefillPaperFeeRates,
	shouldPrefillResearchFeeRates,
	type FeeProfile,
	type ResearchFeeSuggestion
} from './fees';

const suggestedProfile: FeeProfile = {
	taker_fee_rate: '0.0040',
	maker_fee_rate: '0.0025',
	usd_volume_30d: '25000',
	fee_tier: 'Tier 2 ($10k-$50k)',
	as_of: '2026-09-13T16:00:00Z',
	source: 'coinbase',
	suggested_maker_fee_rate: '0.0025',
	suggested_taker_fee_rate: '0.0040',
	suggestion_source: 'coinbase_fee_schedule',
	suggestion_unavailable_reason: null,
	suggestion_fee_tier: 'Tier 2 ($10k-$50k)',
	suggestion_schedule_tier_id: 'usd-10k-50k',
	suggestion_schedule_version: 'coinbase-advanced-spot-fees-v1',
	suggestion_schedule_as_of: '2026-09-13',
	suggestion_fetched_at: '2026-09-13T16:00:00Z'
};

const suggestion: ResearchFeeSuggestion = {
	makerFeeRate: '0.0025',
	takerFeeRate: '0.0040',
	feeTier: 'Tier 2 ($10k-$50k)',
	scheduleTierId: 'usd-10k-50k',
	scheduleVersion: 'coinbase-advanced-spot-fees-v1',
	scheduleAsOf: '2026-09-13',
	fetchedAt: '2026-09-13T16:00:00Z'
};

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
	it('reads schedule-backed suggestion fields', () => {
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
				makerFeeRate: '0.0025',
				takerFeeRate: '0.0040',
				applied: suggestion,
				latest: suggestion,
				loading: false
			})
		).toBe('suggested');
		expect(
			researchFeeFieldSource({
				makerFeeRate: '0.0025',
				takerFeeRate: '0.0040',
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
			`Suggested from Coinbase fee tier (as of ${new Date('2026-09-13T16:00:00Z').toLocaleString()})`
		);
		expect(formatResearchFeeSourceChip('custom', null)).toBe('Custom');
		expect(formatResearchFeeSourceChip('unavailable', null)).toBe(
			'Coinbase fee-tier suggestion unavailable. Enter modeled rates.'
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
				makerFeeRate: '0.0025',
				takerFeeRate: '0.0040',
				touched: false,
				suggestion
			})
		).toBe(false);
	});
});
