<script lang="ts">
	import { onMount } from 'svelte';
	import MarketDataPanel, {
		type FreshnessState,
		type MarketFeedState
	} from '$lib/MarketDataPanel.svelte';
	import PortfolioChart from '$lib/PortfolioChart.svelte';
	import {
		formatPercent,
		formatUsd,
		permissionLabel,
		type ApiError,
		type HistoryEntry,
		type HistoryRange,
		type MarketDataIngestionState,
		type MarketDataPreview,
		type MarketDataRange,
		type MarketProduct,
		type Portfolio,
		type PortfolioHistory
	} from '$lib/portfolio';
	import {
		ASSET_PAGE_SIZES,
		isAssetPageSize,
		nextAssetSort,
		paginateAssets,
		sortAssets,
		type AssetPage,
		type AssetPageSize,
		type AssetSort,
		type AssetSortKey
	} from '$lib/asset-table';
	import { fetchFeeProfile, formatFeeProfileAsOf, type FeeProfile } from '$lib/fees';
	import type { PortfolioAsset } from '$lib/portfolio';

	const ASSET_COLUMNS: Array<{ key: AssetSortKey; label: string }> = [
		{ key: 'currency', label: 'Asset' },
		{ key: 'available', label: 'Available' },
		{ key: 'hold', label: 'On hold' },
		{ key: 'total', label: 'Total' },
		{ key: 'value', label: 'Est. value' }
	];

	let portfolio: Portfolio | null = $state(null);
	let assetSort: AssetSort | null = $state(null);
	let assetPageSize = $state<AssetPageSize>(10);
	// assetPage is deliberately kept across portfolio refreshes: paginateAssets clamps it
	// back into range when a refresh shrinks the asset list, so no reset is needed here.
	let assetPage = $state(1);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let history: HistoryEntry[] = $state([]);
	let historyLoading = $state(true);
	let historyAvailability: 'ready' | 'unavailable' | 'failed' = $state('ready');
	let historyRange: HistoryRange = $state('24h');
	let samplingIntervalSeconds = $state(300);
	let marketDataPreview: MarketDataPreview | null = $state(null);
	let marketDataRange: MarketDataRange | null = $state(null);
	let marketDataIngestion: MarketDataIngestionState | null = $state(null);
	let marketDataIngestionAvailability: 'ready' | 'unavailable' | 'failed' = $state('ready');
	let marketDataRangeAvailability: 'ready' | 'failed' = $state('ready');
	let marketProducts: MarketProduct[] = $state([]);
	let selectedMarketProductId = $state('BTC-USD');
	let marketDataLoading = $state(true);
	let marketDataAvailability: 'ready' | 'failed' = $state('ready');
	let marketFreshness: FreshnessState | null = $state(null);
	let marketFreshnessAvailability: 'ready' | 'unavailable' | 'failed' = $state('ready');
	let marketFeed: MarketFeedState | null = $state(null);
	let marketFeedAvailability: 'ready' | 'unavailable' | 'failed' = $state('ready');
	let feeProfile: FeeProfile | null = $state(null);
	let feesLoading = $state(true);
	let feesAvailability: 'ready' | 'unavailable' = $state('ready');

	function assetTableState(assets: PortfolioAsset[]): AssetPage {
		/** Sort then paginate for the template; the parameter carries the {#if portfolio} narrowing. */
		return paginateAssets(sortAssets(assets, assetSort), assetPage, assetPageSize);
	}

	function sortOn(key: AssetSortKey): void {
		assetSort = nextAssetSort(assetSort, key);
		assetPage = 1;
	}

	function setAssetPageSize(event: Event): void {
		const size = Number((event.currentTarget as HTMLSelectElement).value);
		assetPageSize = isAssetPageSize(size) ? size : 10;
		assetPage = 1;
	}

	function goToAssetPage(page: number): void {
		assetPage = page;
	}

	async function loadHistory(range = historyRange): Promise<void> {
		historyLoading = true;
		historyAvailability = 'ready';
		try {
			const response = await fetch(`/api/v1/portfolio/history?range=${range}`, {
				headers: { Accept: 'application/json' }
			});
			if (response.ok) {
				const body = (await response.json()) as PortfolioHistory;
				history = body.entries;
				historyRange = body.range;
				samplingIntervalSeconds = body.sampling_interval_seconds;
			} else {
				history = [];
				historyAvailability = response.status === 503 ? 'unavailable' : 'failed';
			}
		} catch {
			history = [];
			historyAvailability = 'failed';
		} finally {
			historyLoading = false;
		}
	}

	async function loadMarketData(productId = selectedMarketProductId): Promise<void> {
		marketDataLoading = true;
		marketDataAvailability = 'ready';
		marketDataRangeAvailability = 'ready';
		marketDataIngestionAvailability = 'ready';
		marketFreshnessAvailability = 'ready';
		marketFeedAvailability = 'ready';
		try {
			if (!marketProducts.length) {
				const catalogResponse = await fetch('/api/v1/market-data/products', {
					headers: { Accept: 'application/json' }
				});
				if (!catalogResponse.ok) throw new Error('Market catalog unavailable.');
				marketProducts = ((await catalogResponse.json()) as { products: MarketProduct[] }).products;
				if (!marketProducts.some((product) => product.product_id === productId)) {
					productId = marketProducts[0]?.product_id ?? productId;
				}
			}
			selectedMarketProductId = productId;
			const [previewResult, rangeResult, ingestionResult, freshnessResult, feedResult] =
				await Promise.allSettled([
					fetch(`/api/v1/market-data/preview?product_id=${encodeURIComponent(productId)}`, {
						headers: { Accept: 'application/json' }
					}),
					fetch(`/api/v1/market-data/range?product_id=${encodeURIComponent(productId)}`, {
						headers: { Accept: 'application/json' }
					}),
					fetch(`/api/v1/market-data/ingestion?product_id=${encodeURIComponent(productId)}`, {
						headers: { Accept: 'application/json' }
					}),
					fetch(`/api/v1/market-data/freshness?product_id=${encodeURIComponent(productId)}`, {
						headers: { Accept: 'application/json' }
					}),
					fetch(`/api/v1/market-data/feed?product_id=${encodeURIComponent(productId)}`, {
						headers: { Accept: 'application/json' }
					})
				]);
			if (freshnessResult.status === 'fulfilled' && freshnessResult.value.ok) {
				marketFreshness = (await freshnessResult.value.json()) as FreshnessState;
			} else {
				marketFreshness = null;
				marketFreshnessAvailability =
					freshnessResult.status === 'fulfilled' && freshnessResult.value.status === 503
						? 'unavailable'
						: 'failed';
			}
			if (feedResult.status === 'fulfilled' && feedResult.value.ok) {
				marketFeed = (await feedResult.value.json()) as MarketFeedState;
			} else {
				marketFeed = null;
				marketFeedAvailability =
					feedResult.status === 'fulfilled' && feedResult.value.status === 503
						? 'unavailable'
						: 'failed';
			}
			if (previewResult.status === 'fulfilled' && previewResult.value.ok) {
				marketDataPreview = (await previewResult.value.json()) as MarketDataPreview;
			} else {
				marketDataPreview = null;
				marketDataAvailability = 'failed';
			}
			if (rangeResult.status === 'fulfilled' && rangeResult.value.ok) {
				marketDataRange = (await rangeResult.value.json()) as MarketDataRange;
			} else {
				marketDataRange = null;
				marketDataRangeAvailability = 'failed';
			}
			if (ingestionResult.status === 'fulfilled' && ingestionResult.value.ok) {
				marketDataIngestion = (await ingestionResult.value.json()) as MarketDataIngestionState;
			} else {
				marketDataIngestion = null;
				marketDataIngestionAvailability =
					ingestionResult.status === 'fulfilled' && ingestionResult.value.status === 503
						? 'unavailable'
						: 'failed';
			}
		} catch {
			marketDataPreview = null;
			marketDataRange = null;
			marketDataRangeAvailability = 'failed';
			marketDataIngestion = null;
			marketDataIngestionAvailability = 'failed';
			marketDataAvailability = 'failed';
			marketFreshness = null;
			marketFreshnessAvailability = 'failed';
			marketFeed = null;
			marketFeedAvailability = 'failed';
		} finally {
			marketDataLoading = false;
		}
	}

	async function loadFees(): Promise<void> {
		feesLoading = true;
		feesAvailability = 'ready';
		try {
			feeProfile = await fetchFeeProfile();
		} catch {
			feeProfile = null;
			feesAvailability = 'unavailable';
		} finally {
			feesLoading = false;
		}
	}

	async function loadPortfolio(): Promise<void> {
		loading = true;
		error = null;
		try {
			const response = await fetch('/api/v1/portfolio', {
				headers: { Accept: 'application/json' }
			});
			if (!response.ok) {
				const body = (await response.json()) as ApiError;
				throw new Error(body.detail?.message ?? 'Portfolio data is unavailable.');
			}
			portfolio = (await response.json()) as Portfolio;
			await Promise.all([loadHistory(), loadMarketData(), loadFees()]);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Portfolio data is unavailable.';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void loadPortfolio();
	});
</script>

<svelte:head><title>Your portfolio · ThyTrader</title></svelte:head>

<main>
	<section class="hero">
		<div>
			<p class="eyebrow">Coinbase overview</p>
			<h1>Your portfolio</h1>
			<p class="lede">Balances and estimated value from your connected Coinbase account.</p>
		</div>
		<button class="refresh" type="button" onclick={loadPortfolio} disabled={loading}>
			<span class:spinning={loading}>↻</span>
			{loading ? 'Refreshing…' : 'Refresh portfolio'}
		</button>
	</section>

	{#if error}
		<div class="error-banner" role="alert">
			<div>
				<strong>Couldn't refresh Coinbase</strong>
				<p>{error}</p>
			</div>
			<button type="button" onclick={loadPortfolio}>Try again</button>
		</div>
	{/if}

	{#if loading && !portfolio}
		<section class="loading-card" aria-label="Loading portfolio">
			<div class="skeleton wide"></div>
			<div class="skeleton"></div>
			<div class="skeleton"></div>
		</section>
	{:else if portfolio}
		{@const assetPageView = assetTableState(portfolio.assets)}
		{#if portfolio.demo}
			<div class="demo-banner">
				<div><span class="demo-dot"></span><strong>Demo data</strong></div>
				<p>Add Coinbase credentials to <code>.env</code> to display your live balances.</p>
			</div>
		{/if}

		<section class="summary-grid">
			<article class="value-card">
				<p>Estimated portfolio value</p>
				<strong>{formatUsd(portfolio.total_value.amount)}</strong>
				<span>USD estimate</span>
			</article>
			<article class="connection-card">
				<div class="card-heading">
					<p>Coinbase connection</p>
					<span class="status {portfolio.connection.status}">{portfolio.connection.status}</span>
				</div>
				<strong
					>{portfolio.connection.status === 'connected' ? 'Connected' : 'Ready to preview'}</strong
				>
				<small>Updated {new Date(portfolio.as_of).toLocaleString()}</small>
			</article>
			<article class="permissions-card">
				<p>Detected permissions</p>
				<div class="permissions">
					{#each portfolio.connection.permissions as permission (permission)}
						<span>{permissionLabel(permission)}</span>
					{/each}
				</div>
				<small>Additional permissions do not block connection.</small>
			</article>
		</section>

		<section class="asset-panel">
			<div class="panel-heading">
				<div>
					<h2>Assets</h2>
					<p>{portfolio.assets.length} balances with value</p>
				</div>
				<div class="asset-controls">
					<span>Estimated in USD</span>
					<label class="page-size">
						<span>Rows per page</span>
						<select data-testid="asset-page-size" value={assetPageSize} onchange={setAssetPageSize}>
							{#each ASSET_PAGE_SIZES as size (size)}
								<option value={size}>{size}</option>
							{/each}
						</select>
					</label>
				</div>
			</div>
			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							{#each ASSET_COLUMNS as column (column.key)}
								<th
									aria-sort={assetSort?.key === column.key
										? assetSort.direction === 'asc'
											? 'ascending'
											: 'descending'
										: 'none'}
								>
									<button
										type="button"
										class="sort-header"
										class:active={assetSort?.key === column.key}
										onclick={() => sortOn(column.key)}
									>
										{column.label}
										<span class="sort-arrow" aria-hidden="true">
											{assetSort?.key === column.key
												? assetSort.direction === 'asc'
													? '▲'
													: '▼'
												: '↕'}
										</span>
									</button>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each assetPageView.items as asset (asset.currency)}
							<tr>
								<td
									><div class="asset-name">
										<span class="coin">{asset.currency.slice(0, 1)}</span>
										<div><strong>{asset.name}</strong><small>{asset.currency}</small></div>
									</div></td
								>
								<td>{asset.available}</td><td>{asset.hold}</td><td>{asset.total}</td>
								<td class="asset-value"
									>{asset.value ? formatUsd(asset.value.amount) : 'Unavailable'}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if assetPageView.total === 0}
				<p class="table-empty">No balances with value to display.</p>
			{/if}
			<div class="table-foot" data-testid="asset-table-foot">
				<p data-testid="asset-range">
					Showing {assetPageView.start}–{assetPageView.end} of {assetPageView.total}
				</p>
				<div class="pager">
					<button type="button" onclick={() => goToAssetPage(1)} disabled={assetPageView.page <= 1}>
						« First
					</button>
					<button
						type="button"
						onclick={() => goToAssetPage(assetPageView.page - 1)}
						disabled={assetPageView.page <= 1}
					>
						‹ Prev
					</button>
					<span>Page {assetPageView.page} of {assetPageView.pageCount}</span>
					<button
						type="button"
						onclick={() => goToAssetPage(assetPageView.page + 1)}
						disabled={assetPageView.page >= assetPageView.pageCount}
					>
						Next ›
					</button>
					<button
						type="button"
						onclick={() => goToAssetPage(assetPageView.pageCount)}
						disabled={assetPageView.page >= assetPageView.pageCount}
					>
						Last »
					</button>
				</div>
			</div>
			{#if portfolio.unvalued_assets.length}
				<p class="unvalued">No direct USD valuation: {portfolio.unvalued_assets.join(', ')}</p>
			{/if}
		</section>

		<section class="fees-panel">
			<div class="panel-heading">
				<div>
					<h2>Fee Tier & Costs</h2>
					<p>Coinbase Advanced Trade 30-day volume and execution rates</p>
					{#if feeProfile}
						{@const asOfLabel = formatFeeProfileAsOf(feeProfile.as_of)}
						{#if asOfLabel !== null}
							<p>As of <time datetime={feeProfile.as_of}>{asOfLabel}</time></p>
						{/if}
					{/if}
				</div>
				{#if feeProfile}
					<span class="badge tier-badge">{feeProfile.fee_tier}</span>
				{/if}
			</div>
			{#if feesLoading}
				<div class="loading-state"><p>Loading fee profile…</p></div>
			{:else if feeProfile}
				<div class="fees-grid">
					<article class="fee-card">
						<p>Taker fee rate</p>
						<strong>{formatPercent(feeProfile.taker_fee_rate)}</strong>
						<span>Market orders / taker</span>
					</article>
					<article class="fee-card">
						<p>Maker fee rate</p>
						<strong>{formatPercent(feeProfile.maker_fee_rate)}</strong>
						<span>Limit orders / maker</span>
					</article>
					<article class="fee-card">
						<p>30-day trailing volume</p>
						<strong>{formatUsd(feeProfile.usd_volume_30d)}</strong>
						<span>USD spot volume</span>
					</article>
				</div>
			{:else if feesAvailability === 'unavailable'}
				<div class="unavailable-state">
					<p>Fee profile is temporarily unavailable.</p>
				</div>
			{/if}
		</section>

		<PortfolioChart
			entries={history}
			loading={historyLoading}
			availability={historyAvailability}
			selectedRange={historyRange}
			{samplingIntervalSeconds}
			onRangeChange={(range) => void loadHistory(range)}
		/>

		<MarketDataPanel
			preview={marketDataPreview}
			range={marketDataRange}
			ingestion={marketDataIngestion}
			ingestionAvailability={marketDataIngestionAvailability}
			rangeAvailability={marketDataRangeAvailability}
			freshness={marketFreshness}
			freshnessAvailability={marketFreshnessAvailability}
			feed={marketFeed}
			feedAvailability={marketFeedAvailability}
			products={marketProducts}
			selectedProductId={selectedMarketProductId}
			loading={marketDataLoading}
			availability={marketDataAvailability}
			onProductChange={(productId) => void loadMarketData(productId)}
		/>
	{/if}
</main>
