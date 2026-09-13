/**
 * Typed API client for Coinbase fee tiers and research maker/taker suggestions.
 */

import { compareDecimalStrings } from './portfolio';

export const RESEARCH_FEE_ENGINE_NOTE =
	'These are modeled research assumptions, not observed Coinbase fills. V1 and V2 next-open fills use the taker rate even when the strategy prefers maker.';

export interface FeeProfile {
	taker_fee_rate: string;
	maker_fee_rate: string;
	usd_volume_30d: string;
	fee_tier: string;
	as_of: string;
	source: 'coinbase';
	suggested_maker_fee_rate?: string | null;
	suggested_taker_fee_rate?: string | null;
	suggestion_source?: 'coinbase_fee_schedule' | 'unavailable';
	suggestion_unavailable_reason?: 'demo_or_missing_credentials' | null;
	suggestion_fee_tier?: string | null;
	suggestion_schedule_tier_id?: string | null;
	suggestion_schedule_version?: string | null;
	suggestion_schedule_as_of?: string | null;
	suggestion_fetched_at?: string | null;
}

export interface ResearchFeeSuggestion {
	makerFeeRate: string;
	takerFeeRate: string;
	feeTier: string;
	scheduleTierId: string;
	scheduleVersion: string;
	scheduleAsOf: string;
	fetchedAt: string;
}

export type ResearchFeeFieldSource =
	'loading' | 'unavailable' | 'suggested' | 'stale-suggestion' | 'custom';

export interface FeeErrorResponse {
	detail?: {
		code: string;
		message: string;
	};
}

/**
 * Format `FeeProfile.as_of` for the fees panel, or null when it is absent or invalid.
 */
export function formatFeeProfileAsOf(asOf: string): string | null {
	if (asOf.trim() === '') {
		return null;
	}
	const milliseconds = Date.parse(asOf);
	if (!Number.isFinite(milliseconds)) {
		return null;
	}
	return new Date(milliseconds).toLocaleString();
}

/**
 * Read a schedule-backed research suggestion, or null when the payload is unavailable.
 */
export function readResearchFeeSuggestion(profile: FeeProfile): ResearchFeeSuggestion | null {
	if (profile.suggestion_source !== 'coinbase_fee_schedule') {
		return null;
	}
	const maker = profile.suggested_maker_fee_rate?.trim() ?? '';
	const taker = profile.suggested_taker_fee_rate?.trim() ?? '';
	const feeTier = profile.suggestion_fee_tier?.trim() ?? '';
	const scheduleVersion = profile.suggestion_schedule_version?.trim() ?? '';
	const fetchedAt = profile.suggestion_fetched_at?.trim() ?? '';
	if (
		maker === '' ||
		taker === '' ||
		feeTier === '' ||
		scheduleVersion === '' ||
		fetchedAt === ''
	) {
		return null;
	}
	return {
		makerFeeRate: maker,
		takerFeeRate: taker,
		feeTier,
		scheduleTierId: profile.suggestion_schedule_tier_id?.trim() ?? '',
		scheduleVersion,
		scheduleAsOf: profile.suggestion_schedule_as_of?.trim() ?? '',
		fetchedAt
	};
}

/**
 * Compare two decimal fee-rate strings without binary floating point.
 */
export function feeRatesMatch(left: string, right: string): boolean {
	const trimmedLeft = left.trim();
	const trimmedRight = right.trim();
	if (trimmedLeft === '' || trimmedRight === '') {
		return false;
	}
	try {
		return compareDecimalStrings(trimmedLeft, trimmedRight) === 0;
	} catch {
		return trimmedLeft === trimmedRight;
	}
}

/**
 * Classify research maker/taker fields as suggested, stale, custom, or unavailable.
 */
export function researchFeeFieldSource(input: {
	makerFeeRate: string;
	takerFeeRate: string;
	applied: ResearchFeeSuggestion | null;
	latest: ResearchFeeSuggestion | null;
	loading: boolean;
}): ResearchFeeFieldSource {
	const empty = input.makerFeeRate.trim() === '' && input.takerFeeRate.trim() === '';
	if (empty && input.loading) {
		return 'loading';
	}
	if (empty) {
		return 'unavailable';
	}
	if (
		input.latest !== null &&
		feeRatesMatch(input.makerFeeRate, input.latest.makerFeeRate) &&
		feeRatesMatch(input.takerFeeRate, input.latest.takerFeeRate)
	) {
		return 'suggested';
	}
	if (
		input.applied !== null &&
		feeRatesMatch(input.makerFeeRate, input.applied.makerFeeRate) &&
		feeRatesMatch(input.takerFeeRate, input.applied.takerFeeRate)
	) {
		return 'stale-suggestion';
	}
	return 'custom';
}

/**
 * Honest source chip copy for research maker/taker fields.
 */
export function formatResearchFeeSourceChip(
	source: ResearchFeeFieldSource,
	asOfLabel: string | null
): string {
	if (source === 'loading') {
		return 'Loading fee-tier suggestion…';
	}
	if (source === 'unavailable') {
		return 'Coinbase fee-tier suggestion unavailable. Enter modeled rates.';
	}
	if (source === 'custom') {
		return 'Custom';
	}
	const asOf = asOfLabel !== null ? ` (as of ${asOfLabel})` : '';
	if (source === 'stale-suggestion') {
		return `Suggested from Coinbase fee tier${asOf} — stale`;
	}
	return `Suggested from Coinbase fee tier${asOf}`;
}

/**
 * Prefill empty untouched fields only; never overwrite in-progress edits.
 */
export function shouldPrefillResearchFeeRates(input: {
	makerFeeRate: string;
	takerFeeRate: string;
	touched: boolean;
	suggestion: ResearchFeeSuggestion | null;
}): boolean {
	return (
		input.suggestion !== null &&
		!input.touched &&
		input.makerFeeRate.trim() === '' &&
		input.takerFeeRate.trim() === ''
	);
}

export async function fetchFeeProfile(): Promise<FeeProfile> {
	const response = await fetch('/api/v1/fees', {
		headers: { Accept: 'application/json' }
	});

	if (!response.ok) {
		let message = 'Fee profile is temporarily unavailable.';
		try {
			const errorBody = (await response.json()) as FeeErrorResponse;
			if (errorBody.detail?.message) {
				message = errorBody.detail.message;
			}
		} catch {
			// fallback
		}
		throw new Error(message);
	}

	return (await response.json()) as FeeProfile;
}
