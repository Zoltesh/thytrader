/**
 * Inventory adoption (ADR 0124): coins already held at Coinbase that no bot manages can be
 * protected by a discretionary book, sold to the product's quote currency, or handed to a
 * live strategy bot. Live only. The read-only preview reports what live books already
 * claim of a coin and how much is adoptable; unknown figures are null, never zero.
 */
import { request } from './deployments-http';
import type { Deployment } from './deployments';
import type { StrategyLibraryEntry } from './strategies-types';

export type AdoptionClaims = {
	managed_long: string;
	working_buys: string;
	working_short_entry_sells: string;
	claimed: string;
};

export type AdoptionPreview = {
	product_id: string;
	base_currency: string;
	timeframe: string;
	mark: string | null;
	mark_bar_starts_at: string | null;
	base_increment: string | null;
	balance_total: string | null;
	balance_available: string | null;
	claims: AdoptionClaims | null;
	unmanaged: string | null;
	adoptable: string | null;
	unresolved_reasons: string[];
	protect_blocking_reasons: string[];
	sell_blocking_reasons: string[];
};

export type AdoptionAction = 'protect' | 'sell';

/** Read-only: balances, claims, adoptable quantity, mark, and what blocks each action. */
export function fetchAdoptionPreview(
	productId: string,
	timeframe = '5m'
): Promise<AdoptionPreview> {
	const params = new URLSearchParams({ product_id: productId, timeframe });
	return request<AdoptionPreview>(`/api/v1/inventory-adoptions/preview?${params.toString()}`);
}

/**
 * Protect or sell held coins. Callers pass `i_understand_live: true` only after the
 * operator ticked the live acknowledgement in the confirmation dialog.
 */
export function adoptHoldings(input: {
	action: AdoptionAction;
	product_id: string;
	quantity: string;
	idempotency_key: string;
	timeframe?: string;
	stop_price?: string;
	take_profit_price?: string;
	note?: string;
	i_understand_live: true;
}): Promise<Deployment> {
	return request<Deployment>('/api/v1/inventory-adoptions', {
		method: 'POST',
		body: JSON.stringify({ mode: 'live', origin: 'human', ...input })
	});
}

/** Quote currencies hold cash, not coins a bot could adopt. */
export const QUOTE_CURRENCIES: readonly string[] = ['USD', 'USDC', 'USDT'];

export function isAdoptableAsset(currency: string): boolean {
	return !QUOTE_CURRENCIES.includes(currency.toUpperCase());
}

const DECIMAL = /^\d+(\.\d+)?$/;

/** Why a quantity cannot be sent, or null; "all" needs no number. */
export function adoptionQuantityError(all: boolean, text: string): string | null {
	if (all) return null;
	const trimmed = text.trim();
	if (trimmed === '') return 'Enter a quantity or choose all unmanaged.';
	if (!DECIMAL.test(trimmed) || /^0+(\.0+)?$/.test(trimmed)) {
		return 'Quantity must be a positive decimal.';
	}
	return null;
}

/** The request's quantity field: "all" or the trimmed decimal. */
export function adoptionQuantity(all: boolean, text: string): string {
	return all ? 'all' : text.trim();
}

/** Blocking reasons for one action, as "CODE: detail" lines. */
export function blockingReasons(preview: AdoptionPreview | null, action: AdoptionAction): string[] {
	if (preview === null) return [];
	return action === 'protect' ? preview.protect_blocking_reasons : preview.sell_blocking_reasons;
}

/** Valid single-product strategies trading this coin (the server checks the rest). */
export function strategiesForBase(
	entries: readonly StrategyLibraryEntry[],
	base: string
): StrategyLibraryEntry[] {
	const prefix = `${base.toUpperCase()}-`;
	return entries.filter((entry) => entry.valid && (entry.product_id ?? '').startsWith(prefix));
}
