import { describe, expect, it } from 'vitest';
import { isFuturesProductId, isFuturesStrategy, productIdQuote } from './product-id';

describe('isFuturesProductId', () => {
	it('recognises Coinbase CDE futures ids', () => {
		expect(isFuturesProductId('BIP-20DEC30-CDE')).toBe(true);
		expect(isFuturesProductId('ETP-20DEC30-CDE')).toBe(true);
		expect(isFuturesProductId(' bip-20dec30-cde ')).toBe(true);
	});

	it('rejects spot and malformed ids', () => {
		expect(isFuturesProductId('BTC-USDC')).toBe(false);
		expect(isFuturesProductId('BTC-USD')).toBe(false);
		expect(isFuturesProductId('BIP-20DEC30')).toBe(false);
		expect(isFuturesProductId('B-20DEC30-CDE')).toBe(false);
		expect(isFuturesProductId('BIP-2DEC30-CDE')).toBe(false);
		expect(isFuturesProductId('')).toBe(false);
	});

	it('recognises a futures strategy by instrument kind or product id', () => {
		expect(
			isFuturesStrategy({ instrument: { product_id: 'BIP-20DEC30-CDE', kind: 'future' } })
		).toBe(true);
		expect(isFuturesStrategy({ instrument: { product_id: 'BIP-20DEC30-CDE' } })).toBe(true);
		expect(isFuturesStrategy({ instrument: { product_id: 'BTC-USDC', kind: 'spot' } })).toBe(false);
		expect(isFuturesStrategy({ instrument: { product_id: 'BTC-USDC' } })).toBe(false);
		expect(isFuturesStrategy({})).toBe(false);
		expect(isFuturesStrategy(null)).toBe(false);
	});

	it('leaves the spot quote parser unchanged', () => {
		expect(productIdQuote('BTC-USDC')).toBe('USDC');
	});
});
