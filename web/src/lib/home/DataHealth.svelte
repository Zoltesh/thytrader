<script lang="ts">
	/**
	 * Home "Data health" disclosure (ADR 0084), closed by default at the bottom of
	 * Home. It lists the watched datasets from the data catalog (already loaded
	 * for Needs attention) and, the first time it opens, loads the per-product
	 * market-data diagnostics (`MarketDataPanel`) that used to fill Home.
	 * Dataset attention items without a strategy link here (`#data-health`).
	 */
	import MarketDataPanel, {
		type FreshnessState,
		type MarketFeedState
	} from '$lib/MarketDataPanel.svelte';
	import type {
		MarketDataIngestionState,
		MarketDataPreview,
		MarketDataRange,
		MarketProduct
	} from '$lib/portfolio';
	import {
		datasetCoverageText,
		datasetKey,
		datasetWatchText,
		type DataCatalogReport,
		type DatasetCoverageRow
	} from './home-data';
	import { formatShortUtc } from './home-format';
	import WatchedTails from './WatchedTails.svelte';
	import type { Load } from './load';

	let {
		catalog,
		problems,
		open = $bindable(false),
		onretry
	}: {
		catalog: Load<DataCatalogReport>;
		/** Worst problem per dataset key, from the same rules as Needs attention. */
		problems: Readonly<Record<string, string>>;
		open?: boolean;
		onretry: () => void;
	} = $props();

	type Availability = 'ready' | 'unavailable' | 'failed';

	const watched = $derived(
		(catalog.data?.payload.datasets ?? [])
			.filter((row) => row.watched)
			.sort(
				(left, right) =>
					left.product_id.localeCompare(right.product_id) ||
					left.timeframe.localeCompare(right.timeframe)
			)
	);
	const warnings = $derived(catalog.data?.partial_result_warnings ?? []);
	const summary = $derived.by((): string => {
		if (catalog.data === null) {
			return catalog.status === 'error' ? 'catalog unavailable' : 'reading the data catalog…';
		}
		const withProblems = watched.filter((row) => rowProblem(row) !== null).length;
		const base = `${watched.length} watched ${watched.length === 1 ? 'dataset' : 'datasets'}`;
		return withProblems === 0
			? base
			: `${base} · ${withProblems} ${withProblems === 1 ? 'needs' : 'need'} attention`;
	});

	let marketLoaded = $state(false);
	let marketDataPreview: MarketDataPreview | null = $state(null);
	let marketDataRange: MarketDataRange | null = $state(null);
	let marketDataIngestion: MarketDataIngestionState | null = $state(null);
	let marketDataIngestionAvailability: Availability = $state('ready');
	let marketDataRangeAvailability: 'ready' | 'failed' = $state('ready');
	let marketProducts: MarketProduct[] = $state([]);
	let selectedMarketProductId = $state('BTC-USD');
	let marketDataLoading = $state(true);
	let marketDataAvailability: 'ready' | 'failed' = $state('ready');
	let marketFreshness: FreshnessState | null = $state(null);
	let marketFreshnessAvailability: Availability = $state('ready');
	let marketFeed: MarketFeedState | null = $state(null);
	let marketFeedAvailability: Availability = $state('ready');

	$effect(() => {
		if (open && !marketLoaded) {
			marketLoaded = true;
			void loadMarketData();
		}
	});

	/** The worst problem on a watched row, exactly as Needs attention reports it. */
	function rowProblem(row: DatasetCoverageRow): string | null {
		return problems[datasetKey(row.product_id, row.timeframe)] ?? null;
	}

	function availabilityOf(result: PromiseSettledResult<Response>): Availability {
		return result.status === 'fulfilled' && result.value.status === 503 ? 'unavailable' : 'failed';
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
			const query = `product_id=${encodeURIComponent(productId)}`;
			const read = (path: string): Promise<Response> =>
				fetch(`/api/v1/market-data/${path}?${query}`, { headers: { Accept: 'application/json' } });
			const [previewResult, rangeResult, ingestionResult, freshnessResult, feedResult] =
				await Promise.allSettled([
					read('preview'),
					read('range'),
					read('ingestion'),
					read('freshness'),
					read('feed')
				]);
			if (freshnessResult.status === 'fulfilled' && freshnessResult.value.ok) {
				marketFreshness = (await freshnessResult.value.json()) as FreshnessState;
			} else {
				marketFreshness = null;
				marketFreshnessAvailability = availabilityOf(freshnessResult);
			}
			if (feedResult.status === 'fulfilled' && feedResult.value.ok) {
				marketFeed = (await feedResult.value.json()) as MarketFeedState;
			} else {
				marketFeed = null;
				marketFeedAvailability = availabilityOf(feedResult);
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
				marketDataIngestionAvailability = availabilityOf(ingestionResult);
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
</script>

<details class="card data-health" id="data-health" bind:open data-testid="data-health">
	<summary>
		<span class="chevron" aria-hidden="true">▸</span>
		<h2>Data health</h2>
		<span class="summary-note" data-testid="data-health-summary">{summary}</span>
	</summary>
	<div class="body">
		<section class="watched" aria-labelledby="watched-title">
			<h3 id="watched-title">Watched datasets</h3>
			{#if catalog.data === null}
				{#if catalog.status === 'error'}
					<p class="note warn" role="status">
						The data catalog could not be loaded: {catalog.error}
						<button type="button" class="btn ghost retry" onclick={onretry}>Retry</button>
					</p>
				{:else}
					<p class="note">Reading the data catalog… this can take about 20 seconds.</p>
				{/if}
			{:else}
				{#if warnings.length > 0}
					<p class="note warn" role="status">Partial catalog: {warnings.join(' ')}</p>
				{/if}
				{#if watched.length === 0}
					<p class="note">
						No datasets are watched yet. A strategy's Test or Run stage can download what it needs.
					</p>
				{:else}
					<div class="table-wrap">
						<table>
							<thead>
								<tr>
									<th scope="col">Dataset</th>
									<th scope="col">Coverage</th>
									<th scope="col">Newest candle</th>
									<th scope="col">Watch</th>
									<th scope="col">Worker</th>
									<th scope="col">Status</th>
								</tr>
							</thead>
							<tbody>
								{#each watched as row (`${row.product_id}|${row.timeframe}`)}
									{@const problem = rowProblem(row)}
									<tr data-testid="watched-dataset">
										<td>{row.product_id} <span class="faint">· {row.timeframe}</span></td>
										<td class="num">{datasetCoverageText(row)}</td>
										<td>{row.covered_ends_at ? formatShortUtc(row.covered_ends_at) : '—'}</td>
										<td>{datasetWatchText(row)}</td>
										<td>{row.worker_status ?? 'never run'}</td>
										<td class:warn={problem !== null}>
											{#if problem !== null}<span class="mark" aria-hidden="true">!</span
												>{/if}{problem ?? 'OK'}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			{/if}
		</section>
		{#if marketLoaded}
			<WatchedTails />
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
	</div>
</details>

<style>
	.data-health {
		overflow: hidden;
		/* Keep the opened disclosure clear of the sticky top bar. */
		scroll-margin-top: calc(var(--topbar-height) + var(--space-3));
	}
	.mark {
		display: inline-block;
		width: 14px;
		margin-right: 6px;
		border: 1px solid currentColor;
		border-radius: 50%;
		font-size: 10px;
		font-weight: 700;
		line-height: 12px;
		text-align: center;
	}
	summary {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		cursor: pointer;
		list-style: none;
	}
	summary::-webkit-details-marker {
		display: none;
	}
	summary:focus-visible {
		outline-offset: -2px;
	}
	.chevron {
		color: var(--faint);
		font-size: 11px;
	}
	details[open] .chevron {
		transform: rotate(90deg);
	}
	.summary-note {
		margin-left: auto;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.body {
		padding: 0 16px 16px;
		border-top: 1px solid var(--line);
	}
	h3 {
		margin: 14px 0 8px;
		font-size: var(--fs-base);
		font-weight: 600;
	}
	.note {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin: 0 0 8px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.warn {
		color: var(--warn);
	}
	.retry {
		min-height: 26px;
		padding: 0 8px;
	}
	.watched table {
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
	}
	th,
	td {
		padding: 7px 12px;
		text-align: left;
		font-size: var(--fs-sm);
	}
	th {
		border-bottom: 1px solid var(--line);
	}
	.num {
		text-align: right;
	}
	.faint {
		color: var(--faint);
	}
</style>
