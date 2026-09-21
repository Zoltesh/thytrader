import { describe, expect, it } from 'vitest';

import type { PortfolioAsset } from './portfolio';

import {
	ASSET_PAGE_SIZES,
	assetPageCount,
	isAssetPageSize,
	nextAssetSort,
	paginateAssets,
	sortAssets,
	type AssetPageSize
} from './asset-table';

function asset(overrides: Partial<PortfolioAsset>): PortfolioAsset {
	return {
		currency: 'TEST',
		name: 'Test',
		available: '0',
		hold: '0',
		total: '0',
		value: null,
		...overrides
	};
}

function fixture(): PortfolioAsset[] {
	return [
		asset({
			currency: 'BTC',
			name: 'Bitcoin',
			available: '0.75000000',
			hold: '0.01000000',
			total: '0.76000000',
			value: { amount: '91200.00', currency: 'USD' }
		}),
		asset({
			currency: 'ETH',
			name: 'Ethereum',
			available: '2.25',
			hold: '0',
			total: '2.25',
			value: { amount: '7342.17', currency: 'USD' }
		}),
		asset({
			currency: 'SOL',
			name: 'Solana',
			available: '10',
			hold: '0.5',
			total: '10.5',
			value: { amount: '1500.00', currency: 'USD' }
		}),
		asset({
			currency: 'DOGE',
			name: 'Dogecoin',
			available: '1000',
			hold: '0',
			total: '1000',
			value: null
		})
	];
}

describe('nextAssetSort', () => {
	it('starts ascending from an unsorted table', () => {
		expect(nextAssetSort(null, 'value')).toEqual({ key: 'value', direction: 'asc' });
	});

	it('toggles to descending on a second click of the same column', () => {
		expect(nextAssetSort({ key: 'value', direction: 'asc' }, 'value')).toEqual({
			key: 'value',
			direction: 'desc'
		});
	});

	it('clears the sort on a third click of the same column', () => {
		expect(nextAssetSort({ key: 'value', direction: 'desc' }, 'value')).toBeNull();
	});

	it('starts ascending when switching to a different column', () => {
		expect(nextAssetSort({ key: 'total', direction: 'desc' }, 'name')).toEqual({
			key: 'name',
			direction: 'asc'
		});
	});
});

describe('sortAssets', () => {
	it('preserves the API order when no sort is selected', () => {
		const assets = fixture();
		expect(sortAssets(assets, null)).toEqual(assets);
	});

	it('sorts by ticker ascending and descending', () => {
		const assets = fixture();
		expect(
			sortAssets(assets, { key: 'currency', direction: 'asc' }).map((a) => a.currency)
		).toEqual(['BTC', 'DOGE', 'ETH', 'SOL']);
		expect(
			sortAssets(assets, { key: 'currency', direction: 'desc' }).map((a) => a.currency)
		).toEqual(['SOL', 'ETH', 'DOGE', 'BTC']);
	});

	it('sorts by name ascending', () => {
		const assets = fixture();
		expect(sortAssets(assets, { key: 'name', direction: 'asc' }).map((a) => a.name)).toEqual([
			'Bitcoin',
			'Dogecoin',
			'Ethereum',
			'Solana'
		]);
	});

	it('compares decimal magnitudes exactly, not lexicographically', () => {
		const assets = [
			asset({ currency: 'A', total: '10' }),
			asset({ currency: 'B', total: '9' }),
			asset({ currency: 'C', total: '0.00000001' }),
			asset({ currency: 'D', total: '0.1' })
		];
		expect(sortAssets(assets, { key: 'total', direction: 'asc' }).map((a) => a.currency)).toEqual([
			'C',
			'D',
			'B',
			'A'
		]);
	});

	it('sorts by value descending', () => {
		const assets = fixture();
		expect(sortAssets(assets, { key: 'value', direction: 'desc' }).map((a) => a.currency)).toEqual([
			'BTC',
			'ETH',
			'SOL',
			'DOGE'
		]);
	});

	it('places unvalued assets last in both directions when sorting by value', () => {
		const assets = [
			asset({ currency: 'A', value: { amount: '5', currency: 'USD' } }),
			asset({ currency: 'B', value: null }),
			asset({ currency: 'C', value: { amount: '100', currency: 'USD' } }),
			asset({ currency: 'D', value: null })
		];
		const ascending = sortAssets(assets, { key: 'value', direction: 'asc' });
		const descending = sortAssets(assets, { key: 'value', direction: 'desc' });
		expect(ascending.map((a) => a.currency)).toEqual(['A', 'C', 'B', 'D']);
		expect(descending.map((a) => a.currency)).toEqual(['C', 'A', 'B', 'D']);
	});

	it('sorts available and hold balances by exact decimal magnitude', () => {
		const assets = [
			asset({ currency: 'A', available: '2.25000000', hold: '9' }),
			asset({ currency: 'B', available: '10', hold: '0.00000001' })
		];
		expect(
			sortAssets(assets, { key: 'available', direction: 'desc' }).map((a) => a.currency)
		).toEqual(['B', 'A']);
		expect(sortAssets(assets, { key: 'hold', direction: 'asc' }).map((a) => a.currency)).toEqual([
			'B',
			'A'
		]);
	});

	it('breaks ties by ticker so ordering is deterministic in both directions', () => {
		const assets = [asset({ currency: 'ZED', total: '5' }), asset({ currency: 'ABC', total: '5' })];
		expect(sortAssets(assets, { key: 'total', direction: 'asc' }).map((a) => a.currency)).toEqual([
			'ABC',
			'ZED'
		]);
		expect(sortAssets(assets, { key: 'total', direction: 'desc' }).map((a) => a.currency)).toEqual([
			'ABC',
			'ZED'
		]);
	});

	it('does not mutate the input array', () => {
		const assets = fixture();
		const copy = [...assets];
		sortAssets(assets, { key: 'value', direction: 'desc' });
		expect(assets).toEqual(copy);
	});
});

describe('paginateAssets', () => {
	it('defaults to the first page of ten with range labels', () => {
		const assets = Array.from({ length: 12 }, (_, index) => asset({ currency: `C${index}` }));
		const page = paginateAssets(assets, 1, 10);
		expect(page.items).toHaveLength(10);
		expect(page.page).toBe(1);
		expect(page.pageCount).toBe(2);
		expect(page.total).toBe(12);
		expect(page.start).toBe(1);
		expect(page.end).toBe(10);
	});

	it('returns the short final page', () => {
		const assets = Array.from({ length: 12 }, (_, index) => asset({ currency: `C${index}` }));
		const page = paginateAssets(assets, 2, 10);
		expect(page.items.map((a) => a.currency)).toEqual(['C10', 'C11']);
		expect(page.start).toBe(11);
		expect(page.end).toBe(12);
	});

	it('clamps an out-of-range page back into the valid range', () => {
		const assets = Array.from({ length: 12 }, (_, index) => asset({ currency: `C${index}` }));
		expect(paginateAssets(assets, 99, 10).page).toBe(2);
		expect(paginateAssets(assets, 0, 10).page).toBe(1);
		expect(paginateAssets(assets, -3, 10).page).toBe(1);
	});

	it('folds non-finite pages back to page one', () => {
		const assets = fixture();
		expect(paginateAssets(assets, Number.NaN, 10).page).toBe(1);
		expect(paginateAssets(assets, Number.POSITIVE_INFINITY, 10).page).toBe(1);
	});

	it('handles a page size larger than the asset count as a single page', () => {
		const assets = fixture();
		const page = paginateAssets(assets, 1, 50);
		expect(page.items).toHaveLength(4);
		expect(page.pageCount).toBe(1);
		expect(page.start).toBe(1);
		expect(page.end).toBe(4);
	});

	it('handles an empty asset list without inventing rows', () => {
		const page = paginateAssets([], 1, 10);
		expect(page.items).toEqual([]);
		expect(page.pageCount).toBe(1);
		expect(page.total).toBe(0);
		expect(page.start).toBe(0);
		expect(page.end).toBe(0);
	});

	it('applies a page size of twenty after sorting deterministically', () => {
		const assets = fixture();
		const sorted = sortAssets(assets, { key: 'value', direction: 'desc' });
		const page = paginateAssets(sorted, 1, 20 satisfies AssetPageSize);
		expect(page.items.map((a) => a.currency)).toEqual(['BTC', 'ETH', 'SOL', 'DOGE']);
	});
});

describe('assetPageCount', () => {
	it('rounds up partial pages', () => {
		expect(assetPageCount(0, 10)).toBe(1);
		expect(assetPageCount(1, 10)).toBe(1);
		expect(assetPageCount(10, 10)).toBe(1);
		expect(assetPageCount(11, 10)).toBe(2);
		expect(assetPageCount(50, 50)).toBe(1);
		expect(assetPageCount(51, 50)).toBe(2);
	});

	it('exposes the supported page sizes in order', () => {
		expect([...ASSET_PAGE_SIZES]).toEqual([10, 20, 50]);
	});
});

describe('isAssetPageSize', () => {
	it('accepts only the supported sizes', () => {
		expect(isAssetPageSize(10)).toBe(true);
		expect(isAssetPageSize(20)).toBe(true);
		expect(isAssetPageSize(50)).toBe(true);
		expect(isAssetPageSize(1)).toBe(false);
		expect(isAssetPageSize(0)).toBe(false);
		expect(isAssetPageSize(-10)).toBe(false);
		expect(isAssetPageSize(Number.NaN)).toBe(false);
	});
});

describe('sort then paginate integration', () => {
	it('sorts the full set before slicing so page one holds the global top rows', () => {
		const assets: PortfolioAsset[] = Array.from({ length: 12 }, (_, index) =>
			asset({ currency: `C${index}`, value: { amount: String(index), currency: 'USD' } })
		);
		const page = paginateAssets(sortAssets(assets, { key: 'value', direction: 'desc' }), 1, 10);
		expect(page.items.map((a) => a.currency)).toEqual([
			'C11',
			'C10',
			'C9',
			'C8',
			'C7',
			'C6',
			'C5',
			'C4',
			'C3',
			'C2'
		]);
		expect(page.pageCount).toBe(2);
	});
});
