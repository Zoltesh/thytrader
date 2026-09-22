<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import MarketDataPanel, {
		type FreshnessState,
		type MarketFeedState
	} from '$lib/MarketDataPanel.svelte';
	import PortfolioChart from '$lib/PortfolioChart.svelte';
	import PageHead from '$lib/PageHead.svelte';
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
	import { portfolioWindowSummary } from '$lib/portfolio-summary';
	import {
		analyzeDust,
		formatQuantityDisplay,
		snapshotAgeMinutes,
		type DustAnalysis,
		type QuantityDisplay
	} from '$lib/money';
	import { listDeployments, canonicalPositions, type Deployment } from '$lib/deployments';
	import { lifecycleControlsAvailable } from '$lib/lifecycle-contract';

	const ASSET_COLUMNS: Array<{ key: AssetSortKey; label: string }> = [
		{ key: 'currency', label: 'Asset' },
		{ key: 'available', label: 'Available' },
		{ key: 'hold', label: 'On hold' },
		{ key: 'total', label: 'Total' },
		{ key: 'value', label: 'Est. value' }
	];

	let portfolio: Portfolio | null = $state(null);
	let assetSort: AssetSort | null = $state({ key: 'value', direction: 'desc' });
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
	// Deployment strip: independent of the portfolio load so a slow Coinbase
	// refresh never hides local runtime state.
	let deployments = $state<Deployment[] | null>(null);
	let deploymentsLoading = $state(true);

	const activeDeployments = $derived((deployments ?? []).filter((d) => d.status !== 'stopped'));
	const windowSummary = $derived(portfolioWindowSummary(history));
	/**
	 * Display model for the assets panel: dust rows collapse into a summary so
	 * the table carries holdings worth reading. Sorting and pagination keep
	 * exact decimal strings; only presentation is condensed.
	 */
	let dustOpen = $state(false);
	/**
	 * Display-model helpers take the narrowed portfolio as a parameter:
	 * top-level `$derived` loses `{#if portfolio}` narrowing (svelte-check
	 * reads the portfolio as `never`).
	 */
	function dustSummary(current: Portfolio): DustAnalysis {
		return analyzeDust(current.assets);
	}

	function stalenessOf(current: Portfolio): { ageMinutes: number | null; stale: boolean } {
		const ageMinutes = snapshotAgeMinutes(current.as_of);
		return { ageMinutes, stale: ageMinutes !== null && ageMinutes > 10 };
	}

	function isFreshInstallState(current: Portfolio): boolean {
		return current.demo && current.assets.length === 0;
	}

	function quantityCell(quantity: string): QuantityDisplay {
		return formatQuantityDisplay(quantity);
	}

	/**
	 * Performance figures across non-stopped deployments, from the capital and
	 * ledger contract. Counts disclose what is measured; open books make PnL
	 * provisional and are shown that way rather than silently mixed.
	 */
	const performance = $derived.by(() => {
		const active = (deployments ?? []).filter((d) => d.status !== 'stopped');
		const withLedger = active.filter((d) => d.ledger !== null);
		const pnlStrings = withLedger
			.map((d) => d.ledger?.total_net_pnl ?? null)
			.filter((v): v is string => v !== null);
		const totalPnl = pnlStrings.reduce((acc, pnl) => addDecimalStrings(acc, pnl), '0');
		const openBooks = active.reduce((count, d) => count + canonicalPositions(d).length, 0);
		const paperCount = active.filter((d) => d.mode === 'paper').length;
		const liveCount = active.length - paperCount;
		return {
			tradeCount: withLedger.reduce((count, d) => count + (d.ledger?.trade_count ?? 0), 0),
			totalPnl,
			provisional: openBooks > 0,
			paperCount,
			liveCount
		};
	});

	function addDecimalStrings(left: string, right: string): string {
		const leftParts = parseDecimalForSum(left);
		const rightParts = parseDecimalForSum(right);
		const scale = Math.max(leftParts.scale, rightParts.scale);
		const leftUnits = leftParts.units * 10n ** BigInt(scale - leftParts.scale);
		const rightUnits = rightParts.units * 10n ** BigInt(scale - rightParts.scale);
		const units = leftUnits + rightUnits;
		const sign = units < 0n ? '-' : '';
		const digits = (units < 0n ? -units : units).toString().padStart(scale + 1, '0');
		const splitAt = digits.length - scale;
		return `${sign}${digits.slice(0, splitAt)}.${digits.slice(splitAt)}`;
	}

	function parseDecimalForSum(amount: string): { units: bigint; scale: number } {
		const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
		if (!match) {
			throw new Error(`Invalid decimal string: ${amount}`);
		}
		const fraction = match[3] ?? '';
		return {
			units: BigInt(`${match[1] === '-' ? '-' : ''}${match[2]}${fraction}`),
			scale: fraction.length
		};
	}

	function comparePnlSign(amount: string): number {
		const whole = amount.startsWith('-') ? amount.slice(1) : amount;
		const [units, fraction = ''] = whole.split('.');
		const normalized = units.replace(/^0+(?=\d)/, '') + fraction;
		return normalized.replace(/0+$/, '') === '' ? 0 : amount.startsWith('-') ? -1 : 1;
	}

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

	async function loadDeployments(): Promise<void> {
		deploymentsLoading = true;
		try {
			deployments = await listDeployments();
		} catch {
			deployments = null;
		} finally {
			deploymentsLoading = false;
		}
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
		void loadDeployments();
	});
</script>

<svelte:head><title>Your portfolio · ThyTrader</title></svelte:head>

<main>
	<PageHead
		eyebrow="Coinbase overview"
		title="Your portfolio"
		lede="Balances and estimated value from your connected Coinbase account."
	>
		<button class="refresh" type="button" onclick={loadPortfolio} disabled={loading}>
			<span class:spinning={loading}>↻</span>
			{loading ? 'Refreshing…' : 'Refresh portfolio'}
		</button>
	</PageHead>

	{#if deploymentsLoading}
		<p class="strip-loading" role="status">Checking deployments…</p>
	{:else if deployments !== null && activeDeployments.length > 0}
		<section class="deploy-strip" aria-label="Active deployments">
			<div class="strip-head">
				<h2>Your deployments</h2>
				<a href={resolve('/deployments')}>Manage all deployments →</a>
			</div>
			{#if performance.tradeCount > 0 || performance.totalPnl !== '0'}
				<p class="perf-line" data-testid="strategy-performance">
					Strategy performance (paper {performance.paperCount}
					{performance.paperCount === 1 ? 'bot' : 'bots'}{performance.liveCount > 0
						? `, live ${performance.liveCount}`
						: ''}):
					<strong
						class:value-loss={comparePnlSign(performance.totalPnl) < 0}
						class:value-gain={comparePnlSign(performance.totalPnl) > 0}
						>{formatUsd(performance.totalPnl)}</strong
					>
					· {performance.tradeCount}
					{performance.tradeCount === 1 ? 'trade' : 'trades'} closed
					{#if performance.provisional}
						· <em>open position(s) — PnL provisional until books close</em>
					{/if}
				</p>
			{/if}
			<div class="strip-cards">
				{#each activeDeployments as deployment (deployment.id)}
					<a
						class="strip-card"
						href={resolve('/deployments')}
						class:strip-live={deployment.mode === 'live'}
					>
						<div class="strip-top">
							<span class="mode mode-{deployment.mode}">{deployment.mode}</span>
							<strong>{deployment.product_id}</strong>
							<span class="strip-status">{deployment.status}</span>
						</div>
						<p class="strip-facts">
							{deployment.timeframe ?? '—'} · cash {deployment.cash}
							{#if deployment.position}
								· in position{/if}
							{#if !lifecycleControlsAvailable(deployment)}
								· read-only{/if}
						</p>
					</a>
				{/each}
			</div>
		</section>
	{/if}

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
		{@const assetPageView = assetTableState(dustSummary(portfolio).visible)}
		{@const staleness = stalenessOf(portfolio)}
		{@const dust = dustSummary(portfolio)}
		{@const freshInstall = isFreshInstallState(portfolio)}
		{#if portfolio.demo}
			{#if freshInstall}
				<section class="empty-state" aria-label="Getting started">
					<h2>Connect Coinbase to see your portfolio</h2>
					<p>
						ThyTrader is showing an empty demo until Coinbase Advanced Trade credentials are
						configured on the server. Then this page shows your real spot balances, and Strategies
						is where your first strategy starts.
					</p>
					<a class="link-button" href={resolve('/settings')}>Add credentials in Settings</a>
					<a class="link-button" href={resolve('/strategies')}>Explore Strategies</a>
				</section>
			{:else}
				<div class="demo-banner">
					<div><span class="demo-dot"></span><strong>Demo data</strong></div>
					<p>Add Coinbase credentials to <code>.env</code> to display your live balances.</p>
				</div>
			{/if}
		{/if}

		<section class="summary-grid">
			<article class="value-card">
				<p>Estimated portfolio value</p>
				<strong>{formatUsd(portfolio.total_value.amount)}</strong>
				{#if windowSummary.sampleCount >= 2 && windowSummary.changePercent !== null}
					<span
						class="value-change value-{windowSummary.direction}"
						data-testid="portfolio-window-change"
					>
						{windowSummary.direction === 'gain'
							? '▲'
							: windowSummary.direction === 'loss'
								? '▼'
								: '■'}
						{windowSummary.changePercent} · {formatUsd(windowSummary.changeAmount)} · {historyRange} ·
						{windowSummary.sampleCount} samples
					</span>
				{:else}
					<span class="value-change value-flat" data-testid="portfolio-window-change">
						Not enough history yet · {historyRange}
					</span>
				{/if}
				<span class="value-source">USD estimate · Coinbase spot balances</span>
				{#if staleness.stale}
					<span class="value-stale" role="status">
						Snapshot {staleness.ageMinutes} min old · refresh for current balances
					</span>
				{/if}
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
							{@const available = quantityCell(asset.available)}
							{@const hold = quantityCell(asset.hold)}
							{@const total = quantityCell(asset.total)}
							<tr
								class:dust-row={asset.value !== null && formatUsd(asset.value.amount) === '$0.00'}
							>
								<td
									><div class="asset-name">
										<span class="coin">{asset.currency.slice(0, 1)}</span>
										<div><strong>{asset.name}</strong><small>{asset.currency}</small></div>
									</div></td
								>
								<td class="num" title={available.title}>{available.text}</td>
								<td class="num" title={hold.title}>{hold.text}</td>
								<td class="num" title={total.title}>{total.text}</td>
								<td class="asset-value num"
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
			{#if dust.dust.length > 0 && dust.dustTotal !== null}
				<div class="dust-summary">
					<button
						type="button"
						class="dust-toggle"
						aria-expanded={dustOpen}
						onclick={() => (dustOpen = !dustOpen)}
					>
						{dustOpen ? '▾' : '▸'}
						{dust.dust.length}
						{dust.dust.length === 1 ? 'balance' : 'balances'} under
						{formatUsd('0.10')} totaling {formatUsd(dust.dustTotal)}
					</button>
					{#if dustOpen}
						<ul class="dust-list">
							{#each dust.dust as asset (asset.currency)}
								<li>
									{asset.name} ({asset.currency}) ·
									{asset.value ? formatUsd(asset.value.amount) : 'Unavailable'}
								</li>
							{/each}
						</ul>
					{/if}
				</div>
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

<style>
	.strip-loading {
		color: #657174;
		font-size: 12px;
		margin: -12px 0 18px;
	}
	.deploy-strip {
		margin-bottom: 26px;
	}
	.strip-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-bottom: 10px;
	}
	.strip-head h2 {
		margin: 0;
		font-size: 14px;
		color: #aeb9bb;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.strip-head a {
		color: #5ce1b5;
		font-size: 13px;
		text-decoration: none;
	}
	.strip-head a:hover {
		text-decoration: underline;
	}
	.strip-cards {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
		gap: 12px;
	}
	.strip-card {
		display: grid;
		gap: 8px;
		border: 1px solid #232b2d;
		border-radius: 11px;
		background: #101617;
		padding: 13px 15px;
		text-decoration: none;
	}
	.strip-card:hover {
		border-color: #5ce1b5;
	}
	.strip-top {
		display: flex;
		align-items: center;
		gap: 9px;
	}
	.strip-top strong {
		color: #e9edf1;
		font-size: 14px;
	}
	.strip-status {
		margin-left: auto;
		color: #778386;
		font-size: 12px;
	}
	.strip-facts {
		margin: 0;
		color: #8d999c;
		font:
			400 12px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.perf-line {
		margin: 0 0 10px;
		color: #8d999c;
		font-size: 13px;
	}
	.perf-line strong {
		font:
			500 13px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.perf-line em {
		color: #b39b72;
		font-style: normal;
	}
	.value-change {
		display: block;
		margin-top: 8px;
		font:
			500 12px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.value-gain {
		color: #5ce1b5;
	}
	.value-loss {
		color: #f0a3a3;
	}
	.value-flat {
		color: #718083;
	}
	.value-source {
		display: block;
		margin-top: 6px;
		color: #657174;
		font-size: 11px;
	}
	.value-stale {
		display: block;
		margin-top: 6px;
		color: #b39b72;
		font-size: 11px;
	}
	.dust-summary {
		padding: 10px 24px 16px;
	}
	.dust-toggle {
		color: #778386;
		background: transparent;
		border: none;
		padding: 4px 0;
		cursor: pointer;
		font: inherit;
		font-size: 12px;
	}
	.dust-toggle:hover {
		color: #aeb9bb;
	}
	.dust-list {
		margin: 6px 0 0;
		padding-left: 18px;
		color: #718083;
		font:
			400 12px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.link-button {
		display: inline-block;
		color: #dce4e5;
		background: #151b1d;
		border: 1px solid #303a3c;
		border-radius: 9px;
		padding: 11px 15px;
		text-decoration: none;
		font: inherit;
		font-size: 14px;
	}
	.link-button:hover {
		border-color: #5ce1b5;
	}
	.mode {
		font:
			600 10px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		border-radius: 6px;
		padding: 3px 7px;
	}
	.mode-paper {
		color: #9fd9ff;
		border: 1px solid #2c4a5c;
		background: #10222c;
	}
	.mode-live {
		color: #ffb3b3;
		border: 1px solid #733d3d;
		background: #2c1212;
	}
	.strip-live {
		border-color: #4c2a2a;
	}
	.dust-row td {
		color: #5f6d70;
	}
	.dust-row .asset-value {
		color: #5f6d70;
	}
	.num {
		font-variant-numeric: tabular-nums;
	}
</style>
