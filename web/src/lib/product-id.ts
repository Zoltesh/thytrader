/**
 * Coinbase product-id parsing shared by the deployment detail and strategy
 * workspace view models. It imports nothing, so neither view model has to import
 * the other; `deployment-detail.ts` re-exports `productIdQuote`. Futures ids
 * (`BIP-20DEC30-CDE`) are recognised here so USD futures books stay apart from spot.
 */

/** Quote currency of a `BASE-QUOTE` product id, or null when there is no dash. */
export function productIdQuote(productId: string): string | null {
	const separator = productId.indexOf('-');
	if (separator === -1) return null;
	return productId.slice(separator + 1);
}

/** Coinbase CDE futures product ids (`BIP-20DEC30-CDE`), as the backend recognises them. */
const FUTURES_PRODUCT_ID = /^[A-Z0-9]{2,6}-\d{2}[A-Z]{3}\d{2}-CDE$/;

/** Whether `productId` names a Coinbase CFM futures contract (a USD paper futures book). */
export function isFuturesProductId(productId: string): boolean {
	return FUTURES_PRODUCT_ID.test(productId.trim().toUpperCase());
}

/**
 * Whether a strategy definition trades a futures contract: `instrument.kind` is
 * `future`, or its product id is a CDE futures id. A null definition is not futures.
 */
export function isFuturesStrategy(definition: { [key: string]: unknown } | null): boolean {
	const instrument = definition?.instrument as { kind?: unknown; product_id?: unknown } | undefined;
	if (instrument === undefined || instrument === null) return false;
	if (instrument.kind === 'future') return true;
	return typeof instrument.product_id === 'string' && isFuturesProductId(instrument.product_id);
}
