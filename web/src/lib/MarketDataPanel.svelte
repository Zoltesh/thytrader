<script lang="ts">
	import type { MarketDataPreview } from '$lib/portfolio';

	let {
		preview = null as MarketDataPreview | null,
		loading = false,
		availability = 'ready' as 'ready' | 'failed'
	}: {
		preview?: MarketDataPreview | null;
		loading?: boolean;
		availability?: 'ready' | 'failed';
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
</script>

<section class="market-data-panel" aria-label="Market data preview">
	<div class="panel-heading">
		<div>
			<h2>Market data preview</h2>
			<p>Validated closed candles only · data is not persisted yet</p>
		</div>
		{#if preview && availability === 'ready'}
			<span class:warning={status !== 'Complete'} class="quality-status">{status}</span>
		{/if}
	</div>

	{#if loading}
		<div class="market-loading" aria-label="Loading market data"><div class="skeleton"></div></div>
	{:else if availability === 'failed'}
		<div class="market-empty">
			<p>Market data could not be loaded.</p>
			<small>Portfolio data remains separate; refresh to try the read-only preview again.</small>
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
	.market-detail.warning {
		color: #edbb70;
	}
	.quality-status.warning {
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
		.market-summary {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
