import { compareDecimalStrings, type PortfolioAsset } from './portfolio';

/** Page sizes the assets table offers, in display order. */
export const ASSET_PAGE_SIZES = [10, 20, 50] as const;

export type AssetPageSize = (typeof ASSET_PAGE_SIZES)[number];

/** Columns of the assets table that can be sorted. */
export type AssetSortKey = 'currency' | 'name' | 'available' | 'hold' | 'total' | 'value';

export type SortDirection = 'asc' | 'desc';

export type AssetSort = {
	key: AssetSortKey;
	direction: SortDirection;
};

export type AssetPage = {
	items: PortfolioAsset[];
	page: number;
	pageCount: number;
	total: number;
	start: number;
	end: number;
};

export function nextAssetSort(current: AssetSort | null, key: AssetSortKey): AssetSort | null {
	/** Cycle a column header through unsorted -> ascending -> descending -> unsorted. */
	if (current === null || current.key !== key) {
		return { key, direction: 'asc' };
	}
	if (current.direction === 'asc') {
		return { key, direction: 'desc' };
	}
	return null;
}

function sortValue(asset: PortfolioAsset, key: AssetSortKey): string | null {
	switch (key) {
		case 'currency':
			return asset.currency;
		case 'name':
			return asset.name;
		case 'available':
			return asset.available;
		case 'hold':
			return asset.hold;
		case 'total':
			return asset.total;
		case 'value':
			return asset.value === null ? null : asset.value.amount;
	}
}

function compareAssets(
	left: PortfolioAsset,
	right: PortfolioAsset,
	key: AssetSortKey,
	direction: SortDirection
): number {
	/** Order two assets directionally; unvalued assets stay last regardless of direction. */
	const leftValue = sortValue(left, key);
	const rightValue = sortValue(right, key);
	if (key === 'currency' || key === 'name') {
		const primary = (leftValue ?? '').localeCompare(rightValue ?? '');
		return direction === 'asc' ? primary : -primary;
	}
	if (leftValue === null || rightValue === null) {
		if (leftValue === null && rightValue === null) return 0;
		return leftValue === null ? 1 : -1;
	}
	const primary = compareDecimalStrings(leftValue, rightValue);
	return direction === 'asc' ? primary : -primary;
}

export function sortAssets(
	assets: readonly PortfolioAsset[],
	sort: AssetSort | null
): PortfolioAsset[] {
	/** Return a new array ordered by the selected column; null keeps the API order. */
	if (sort === null) {
		return [...assets];
	}
	return [...assets].sort((left, right) => {
		const primary = compareAssets(left, right, sort.key, sort.direction);
		if (primary !== 0) {
			return primary;
		}
		return left.currency.localeCompare(right.currency);
	});
}

export function assetPageCount(total: number, pageSize: AssetPageSize): number {
	/** Count pages so an empty list still reports exactly one (empty) page. */
	if (total <= 0) return 1;
	return Math.ceil(total / pageSize);
}

export function isAssetPageSize(size: number): size is AssetPageSize {
	/** Validate an untrusted page-size input against the supported set. */
	return (ASSET_PAGE_SIZES as readonly number[]).includes(size);
}

export function clampAssetPage(page: number, pageCount: number): number {
	/** Fold arbitrary page inputs into the valid 1-based page range. */
	if (!Number.isFinite(page)) return 1;
	return Math.min(Math.max(Math.trunc(page), 1), pageCount);
}

export function paginateAssets(
	assets: readonly PortfolioAsset[],
	page: number,
	pageSize: AssetPageSize
): AssetPage {
	/** Slice a pre-sorted asset list into the requested 1-based page, clamping out-of-range pages. */
	const total = assets.length;
	const pageCount = assetPageCount(total, pageSize);
	const currentPage = clampAssetPage(page, pageCount);
	const start = (currentPage - 1) * pageSize;
	const end = Math.min(start + pageSize, total);
	return {
		items: assets.slice(start, end),
		page: currentPage,
		pageCount,
		total,
		start: total === 0 ? 0 : start + 1,
		end
	};
}
