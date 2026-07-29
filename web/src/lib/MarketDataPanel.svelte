<script lang="ts">
	import type {
		MarketDataIngestionState,
		MarketDataPreview,
		MarketDataRange,
		MarketProduct
	} from '$lib/portfolio';

	let {
		preview = null as MarketDataPreview | null,
		range = null as MarketDataRange | null,
		rangeAvailability = 'ready' as 'ready' | 'failed',
		ingestion = null as MarketDataIngestionState | null,
		ingestionAvailability = 'ready' as 'ready' | 'unavailable' | 'failed',
		products = [] as MarketProduct[],
		selectedProductId = 'BTC-USD',
		loading = false,
		availability = 'ready' as 'ready' | 'failed',
		onProductChange
	}: {
		preview?: MarketDataPreview | null;
		range?: MarketDataRange | null;
		rangeAvailability?: 'ready' | 'failed';
		ingestion?: MarketDataIngestionState | null;
		ingestionAvailability?: 'ready' | 'unavailable' | 'failed';
		products?: MarketProduct[];
		selectedProductId?: string;
		loading?: boolean;
		availability?: 'ready' | 'failed';
		onProductChange: (productId: string) => void;
	} = $props();

	const status = $derived(
		preview?.quality.stale
			? 'Stale'
			: preview && preview.quality.gap_count > 0
				? 'Gaps detected'
				: 'Complete'
	);
	const latestCompleted = $derived(
		preview?.quality.latest_completed_at
			? new Date(preview.quality.latest_completed_at).toLocaleString()
			: 'No completed candle'
	);
	const rangeStatus = $derived(
		range
			? range.complete
				? 'Complete'
				: range.missing_intervals > 0
					? 'Gaps'
					: 'Incomplete'
			: null
	);

	function selectProduct(event: Event): void {
		onProductChange((event.currentTarget as HTMLSelectElement).value);
	}
</script>

<section class="market-data-panel" aria-label="Data-source diagnostics">
	<div class="panel-heading">
		<div>
			<h2>Data-source diagnostics</h2>
			<p>Connection and candle-integrity check · not a chart, signal, or historical dataset</p>
		</div>
		<label class="product-select">
			<span>USD spot product</span>
			<select
				value={selectedProductId}
				onchange={selectProduct}
				disabled={loading || !products.length}
			>
				{#each products as product (product.product_id)}
					<option value={product.product_id}>{product.product_id}</option>
				{/each}
			</select>
		</label>
		{#if preview && availability === 'ready'}
			<span class:warning={status !== 'Complete'} class="quality-status">{status}</span>
		{/if}
	</div>

	{#if loading}
		<div class="market-loading" aria-label="Loading market data"><div class="skeleton"></div></div>
	{:else if availability === 'failed'}
		<div class="market-empty">
			<p>Data-source diagnostics could not be loaded.</p>
			<small
				>Portfolio data remains separate; refresh to retry this read-only connection check.</small
			>
		</div>
	{:else if preview}
		<div class="market-summary">
			<div>
				<small>Product</small>
				<strong>{preview.product.product_id} · {preview.timeframe.toUpperCase()}</strong>
			</div>
			<div>
				<small>Closed candles</small>
				<strong>{preview.quality.candle_count}</strong>
			</div>
			<div>
				<small>Missing intervals</small>
				<strong>{preview.quality.missing_intervals}</strong>
			</div>
			<div>
				<small>Latest complete</small>
				<strong>{latestCompleted}</strong>
			</div>
		</div>
		{#if range}
			<div class="range-summary">
				<div class="range-header">
					<span>7-day range coverage</span>
					<span class:warning={rangeStatus !== 'Complete'} class="range-badge">{rangeStatus}</span>
				</div>
				<div class="range-grid">
					<div>
						<small>Expected</small>
						<strong>{range.requested_candle_count}</strong>
					</div>
					<div>
						<small>Received</small>
						<strong>{range.received_candle_count}</strong>
					</div>
					<div>
						<small>Gaps</small>
						<strong>{range.gap_count}</strong>
					</div>
					<div>
						<small>Missing</small>
						<strong>{range.missing_intervals}</strong>
					</div>
				</div>
			</div>
		{:else if rangeAvailability === 'failed'}
			<div class="range-summary range-unavailable" role="status">
				<strong>7-day range coverage unavailable</strong>
				<span
					>The recent-candle diagnostic loaded, but the range request failed. Refresh to retry.</span
				>
			</div>
		{/if}
		<div class:warning={status !== 'Complete'} class="market-detail">
			<span class="quality-dot"></span>
			{#if preview.quality.stale}
				Recent completed candles are older than two expected hourly intervals.
			{:else if preview.quality.gap_count > 0}
				{preview.quality.gap_count} gap{preview.quality.gap_count === 1 ? '' : 's'} contain
				{preview.quality.missing_intervals} missing interval{preview.quality.missing_intervals === 1
					? ''
					: 's'}.
			{:else}
				Recent hourly candles are complete and contiguous in this preview window.
			{/if}
		</div>
	{:else}
		<div class="market-empty">
			<p>No market-data preview is available.</p>
		</div>
	{/if}
	{#if !loading}
		<div class="range-summary" role="status">
			<div class="range-header">
				<span>Durable ingestion worker</span>
				{#if ingestion?.provider === 'demo'}<span class="range-badge">Demo dataset</span>{/if}
			</div>
			{#if ingestionAvailability === 'unavailable'}
				<span>Worker state unavailable · configure PostgreSQL and start the separate worker.</span>
			{:else if ingestionAvailability === 'failed'}
				<span>Worker diagnostics could not be loaded.</span>
			{:else if !ingestion || ingestion.status === 'never_run'}
				<span>No ingestion attempt has been recorded for this product.</span>
			{:else if ingestion.status === 'running'}
				<span>Retrieving and validating a bounded hourly range.</span>
				{#if ingestion.failure}<small>Previous failure remains recorded until success.</small>{/if}
			{:else if ingestion.status === 'failed'}
				<strong>Last attempt failed</strong>
				<span>{ingestion.failure?.message ?? 'A redacted ingestion failure was recorded.'}</span>
			{:else if ingestion.coverage}
				<strong>{ingestion.fresh ? 'Fresh · complete' : 'Stale · complete'}</strong>
				<span
					>{ingestion.coverage.received_candle_count} /
					{ingestion.coverage.expected_candle_count} candles · {ingestion.coverage.gap_count} gaps</span
				>
				<small>Fingerprint {ingestion.coverage.content_fingerprint.slice(0, 22)}…</small>
			{/if}
		</div>
	{/if}
</section>

<style>
	.market-data-panel {
		margin-top: 16px;
		border: 1px solid #232b2d;
		border-radius: 13px;
		overflow: hidden;
		background: linear-gradient(145deg, rgba(20, 26, 28, 0.95), rgba(12, 16, 18, 0.95));
	}
	.panel-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 22px 24px;
		border-bottom: 1px solid #232b2d;
	}
	h2 {
		margin: 0;
		font-size: 18px;
	}
	.panel-heading p,
	.market-empty small {
		margin: 5px 0 0;
		color: #778386;
		font-size: 12px;
	}
	.quality-status {
		border: 1px solid #315849;
		border-radius: 999px;
		padding: 4px 8px;
		color: #5ce1b5;
		font-size: 11px;
	}
	.quality-status.warning,
	.market-detail.warning,
	.range-badge.warning {
		color: #edbb70;
	}
	.quality-status.warning,
	.range-badge.warning {
		border-color: #76552d;
	}
	.market-summary {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		padding: 18px 24px;
		gap: 20px;
	}
	.market-summary div {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.market-summary small {
		color: #657174;
		font-size: 10px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	.market-summary strong {
		color: #e8eeee;
		font:
			500 13px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.range-summary {
		padding: 14px 24px;
		border-top: 1px solid #232b2d;
	}
	.range-unavailable {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		color: #edbb70;
		font-size: 12px;
	}
	.range-unavailable span {
		color: #8f9d9f;
	}
	.range-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 12px;
	}
	.range-header span:first-child {
		color: #8f9d9f;
		font-size: 12px;
	}
	.range-badge {
		border: 1px solid #315849;
		border-radius: 999px;
		padding: 3px 8px;
		color: #5ce1b5;
		font-size: 11px;
	}
	.range-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 16px;
	}
	.range-grid div {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.range-grid small {
		color: #657174;
		font-size: 10px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	.range-grid strong {
		color: #e8eeee;
		font:
			500 13px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.market-detail {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 24px;
		border-top: 1px solid #232b2d;
		color: #8f9d9f;
		font-size: 12px;
	}
	.quality-dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: #5ce1b5;
	}
	.warning .quality-dot {
		background: #edbb70;
	}
	.market-empty,
	.market-loading {
		padding: 32px 24px;
		text-align: center;
	}
	.market-empty p {
		margin: 0 0 6px;
		color: #849093;
		font-size: 14px;
	}
	.skeleton {
		height: 55px;
		border-radius: 8px;
		background: linear-gradient(90deg, #151c1e, #20292b, #151c1e);
		background-size: 200%;
		animation: shimmer 1.4s infinite;
	}
	@keyframes shimmer {
		to {
			background-position: -200% 0;
		}
	}
	@media (max-width: 800px) {
		.panel-heading {
			align-items: flex-start;
			gap: 12px;
			flex-direction: column;
		}
		.market-summary,
		.range-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
