import type { FuturesBookPayload, FuturesFactGroup } from './futures-book';

/** Account totals stay separate from the managed book's modeled USD ledger. */
export type LiveFuturesBook = {
	observed_at: string | null;
	evidence_status: 'fresh' | 'stale' | 'unknown';
	venue_contracts: string | null;
	match_state: 'managed_matched' | 'managed_mismatch' | 'unknown';
	match_reason: string | null;
	margin_ratio: string | null;
	available_margin_usd: string | null;
	buying_power_usd: string | null;
	initial_margin_usd: string | null;
	reserve_currency: string | null;
	reserve_quote: string | null;
	reserve_required_usd: string | null;
	reserve_headroom_quote: string | null;
	reserve_covers_current_margin: boolean | null;
	funding_accrued_usd: string | null;
	venue_funding_pnl_usd: string | null;
	funding_drift_reason: string;
};

export const LIVE_VISIBILITY_NOTE =
	'Read-only live book evidence; this book trades real CFM contracts. Live futures books start and stop from the CLI. Account USD totals are not allocated to this book or added to USDC.';

/** Every unknown is explicit; no settlement is inferred from a mirror counter. */
export function liveFuturesGroup(book: FuturesBookPayload): FuturesFactGroup {
	const live = book.live;
	const amount = (value: string | null | undefined, currency = 'USD') =>
		value == null ? 'Unknown' : `${value} ${currency}`;
	const items: [string, string, string][] = [
		['live-orderable', 'Orderable', 'No — live starts remain disabled'],
		['venue-match', 'Venue/book match', live?.match_state ?? 'Unknown'],
		['venue-contracts', 'Venue signed contracts', live?.venue_contracts ?? 'Unknown'],
		['venue-observed', 'Account observed at', live?.observed_at ?? 'Unknown'],
		['venue-freshness', 'Account evidence', live?.evidence_status ?? 'Unknown'],
		['venue-ratio', 'Account margin ratio', live?.margin_ratio ?? 'Unknown'],
		['venue-buying-power', 'Account buying power (USD)', amount(live?.buying_power_usd)],
		['venue-available', 'Account available margin (USD)', amount(live?.available_margin_usd)],
		[
			'reserve',
			'Declared reserve',
			amount(live?.reserve_quote, live?.reserve_currency ?? 'unknown currency')
		],
		['reserve-required', 'Required reserve threshold (USD)', amount(live?.reserve_required_usd)],
		[
			'reserve-headroom',
			'Reserve headroom (declared peg)',
			amount(live?.reserve_headroom_quote, live?.reserve_currency ?? 'unknown currency')
		],
		['accrued-funding', 'Book modeled accrued funding', amount(live?.funding_accrued_usd)],
		[
			'venue-funding',
			'Account funding counter (not settlement)',
			amount(live?.venue_funding_pnl_usd)
		],
		[
			'funding-drift',
			'Settlement drift',
			`Unknown: ${live?.funding_drift_reason ?? 'SETTLEMENT_PROVENANCE_UNAVAILABLE'}`
		],
		['derisk-duty', 'De-risk duty', book.futures_derisk_pending ? 'Pending' : 'Not pending'],
		[
			'flat-duty',
			'Post-fill flat confirmation',
			book.futures_flat_confirmation_after ?? 'Not pending'
		]
	];
	return {
		id: 'live',
		title: 'Live CFM account evidence',
		facts: items.map(([id, label, value]) => ({ id, label, value, hint: null }))
	};
}
