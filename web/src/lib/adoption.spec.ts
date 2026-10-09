import { describe, expect, it } from 'vitest';
import {
	adoptionQuantity,
	adoptionQuantityError,
	blockingReasons,
	isAdoptableAsset,
	strategiesForBase,
	type AdoptionPreview
} from './adoption';
import type { StrategyLibraryEntry } from './strategies-types';
import { holdingActionTitle, holdingActionsAvailable, sellProduct } from './home/holding-actions';

const preview: AdoptionPreview = {
	product_id: 'DOGE-USDC',
	base_currency: 'DOGE',
	timeframe: '5m',
	mark: '0.2',
	mark_bar_starts_at: '2026-10-09T14:00:00+00:00',
	base_increment: '1',
	balance_total: '150',
	balance_available: '150',
	claims: {
		managed_long: '100',
		working_buys: '0',
		working_short_entry_sells: '0',
		claimed: '100'
	},
	unmanaged: '50',
	adoptable: '50',
	unresolved_reasons: [],
	protect_blocking_reasons: ['ADOPTION_BOOK_OCCUPIED: book 1 holds DOGE-USDC.'],
	sell_blocking_reasons: []
};

function entry(overrides: Partial<StrategyLibraryEntry>): StrategyLibraryEntry {
	return {
		strategy_id: 's',
		name: 'n',
		product_id: 'DOGE-USDC',
		timeframe: '1h',
		revision: 1,
		valid: true,
		current_fingerprint: null,
		summary: null,
		created_at: '2026-10-09T00:00:00Z',
		updated_at: '2026-10-09T00:00:00Z',
		backtest: null,
		paper_live: { paper: 'none', live: 'none' },
		active_deployment_count: 0,
		...overrides
	};
}

describe('inventory adoption helpers', () => {
	it('never offers cash currencies for adoption', () => {
		expect(isAdoptableAsset('DOGE')).toBe(true);
		for (const cash of ['USD', 'usdc', 'USDT']) expect(isAdoptableAsset(cash)).toBe(false);
	});

	it('accepts all or a positive decimal quantity', () => {
		expect(adoptionQuantityError(true, '')).toBeNull();
		expect(adoptionQuantityError(false, ' 12.5 ')).toBeNull();
		expect(adoptionQuantityError(false, '')).toMatch(/quantity/);
		expect(adoptionQuantityError(false, '0')).toMatch(/positive/);
		expect(adoptionQuantityError(false, '1e3')).toMatch(/positive/);
		expect(adoptionQuantity(true, '7')).toBe('all');
		expect(adoptionQuantity(false, ' 7 ')).toBe('7');
	});

	it('lists the blocking reasons of the chosen action only', () => {
		expect(blockingReasons(preview, 'protect')).toHaveLength(1);
		expect(blockingReasons(preview, 'sell')).toEqual([]);
		expect(blockingReasons(null, 'sell')).toEqual([]);
	});

	it('offers only valid strategies trading the coin', () => {
		const rows = [
			entry({ strategy_id: 'a' }),
			entry({ strategy_id: 'b', valid: false }),
			entry({ strategy_id: 'c', product_id: 'DOGECOIN-USD' }),
			entry({ strategy_id: 'd', product_id: 'BTC-USD' }),
			entry({ strategy_id: 'e', product_id: null })
		];
		expect(strategiesForBase(rows, 'doge').map((row) => row.strategy_id)).toEqual(['a']);
	});

	it('names holdings actions and hides them for cash and demo balances', () => {
		expect(sellProduct('doge')).toBe('DOGE-USDC');
		expect(holdingActionsAvailable(false, 'DOGE')).toBe(true);
		expect(holdingActionsAvailable(true, 'DOGE')).toBe(false);
		expect(holdingActionsAvailable(false, 'USDC')).toBe(false);
		expect(holdingActionTitle('sell', 'DOGE')).toBe('Sell DOGE to USDC?');
		expect(holdingActionTitle('adopt', 'DOGE')).toBe('Adopt DOGE into a live bot?');
	});
});
