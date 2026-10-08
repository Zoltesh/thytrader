/**
 * Coinbase product-id parsing shared by the deployment detail and strategy
 * workspace view models. It imports nothing, so neither view model has to import
 * the other; `deployment-detail.ts` re-exports `productIdQuote`.
 */

/** Quote currency of a `BASE-QUOTE` product id, or null when there is no dash. */
export function productIdQuote(productId: string): string | null {
	const separator = productId.indexOf('-');
	if (separator === -1) return null;
	return productId.slice(separator + 1);
}
