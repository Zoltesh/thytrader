<script lang="ts">
	import LightweightLineChart from '$lib/LightweightLineChart.svelte';
	import {
		formatConfiguredSamplingInterval,
		formatUsd,
		isHistoryStale,
		portfolioChange,
		portfolioHistoryChartModel,
		type HistoryEntry,
		type HistoryRange
	} from '$lib/portfolio';

	type HistoryAvailability = 'ready' | 'unavailable' | 'failed';

	let {
		entries = [] as HistoryEntry[],
		loading = false,
		availability = 'ready' as HistoryAvailability,
		selectedRange = '24h' as HistoryRange,
		samplingIntervalSeconds = 300,
		onRangeChange
	}: {
		entries?: HistoryEntry[];
		loading?: boolean;
		availability?: HistoryAvailability;
		selectedRange?: HistoryRange;
		samplingIntervalSeconds?: number;
		onRangeChange: (range: HistoryRange) => void;
	} = $props();

	const ranges: { value: HistoryRange; label: string }[] = [
		{ value: '24h', label: '24H' },
		{ value: '7d', label: '7D' },
		{ value: '30d', label: '30D' },
		{ value: 'all', label: 'All' }
	];
	const chartEntries = $derived([...entries].reverse());
	const model = $derived(portfolioHistoryChartModel(chartEntries, samplingIntervalSeconds));
	const current = $derived(entries.length > 0 ? entries[0].total_value.amount : '0');
	const high = $derived(model.maxAmount);
	const low = $derived(model.minAmount);
	const change = $derived(portfolioChange(entries));
	const workerStale = $derived(isHistoryStale(entries, samplingIntervalSeconds));
	const latestSnapshot = $derived(
		entries.length > 0 ? new Date(entries[0].as_of).toLocaleString() : ''
	);

	function changeDirectionLabel(direction: 'gain' | 'loss' | 'flat'): string {
		if (direction === 'gain') return 'up';
		if (direction === 'loss') return 'down';
		return 'unchanged';
	}
</script>

<section class="history-panel" aria-label="Portfolio value history">
	<div class="panel-heading">
		<div>
			<h2>Portfolio history</h2>
			<p>
				{entries.length} sampled {entries.length === 1 ? 'snapshot' : 'snapshots'} · target interval {formatConfiguredSamplingInterval(
					samplingIntervalSeconds
				)}
			</p>
		</div>
		<div class="range-controls" aria-label="History range">
			{#each ranges as range (range.value)}
				<button
					type="button"
					class:active={selectedRange === range.value}
					aria-pressed={selectedRange === range.value}
					onclick={() => onRangeChange(range.value)}
				>
					{range.label}
				</button>
			{/each}
		</div>
	</div>

	{#if entries.length > 0 && availability === 'ready'}
		<div class:stale={workerStale} class="worker-status">
			<span class="worker-dot"></span>
			{workerStale ? 'Snapshot cadence may be behind' : 'Snapshot cadence is current'} · last snapshot
			{latestSnapshot}
		</div>
	{/if}

	{#if entries.length >= 2 && availability === 'ready'}
		<div class="stats">
			<span class="stat"><small>Current</small><strong>{formatUsd(current)}</strong></span>
			<span class="stat"><small>High</small><strong>{formatUsd(high)}</strong></span>
			<span class="stat"><small>Low</small><strong>{formatUsd(low)}</strong></span>
			{#if change}
				<span
					class:gain={change.direction === 'gain'}
					class:loss={change.direction === 'loss'}
					class="stat change"
				>
					<small>Range change ({changeDirectionLabel(change.direction)})</small>
					<strong
						>{change.direction === 'gain' ? '+' : change.direction === 'loss' ? '−' : ''}{formatUsd(
							change.amount.startsWith('-') ? change.amount.slice(1) : change.amount
						)}{#if change.percent !== null}
							({change.direction === 'gain' ? '+' : ''}{change.percent}%){/if}</strong
					>
				</span>
			{/if}
		</div>
	{/if}

	{#if loading}
		<div class="chart-area"><div class="skeleton chart-skeleton"></div></div>
	{:else if availability === 'unavailable'}
		<div class="chart-empty">
			<p>Portfolio history is unavailable on this installation.</p>
			<small>Start the full local stack to enable durable scheduled snapshots.</small>
		</div>
	{:else if availability === 'failed'}
		<div class="chart-empty">
			<p>Portfolio history could not be loaded.</p>
			<small>Try again after the API and worker report healthy.</small>
		</div>
	{:else if entries.length === 0}
		<div class="chart-empty">
			<p>No snapshots exist in this range yet.</p>
			<small
				>The worker records live portfolios automatically; Refresh never creates chart points.</small
			>
		</div>
	{:else if entries.length === 1}
		<div class="chart-empty">
			<p>One snapshot is available.</p>
			<small>A line appears after the next successful scheduled observation.</small>
		</div>
	{:else}
		<div class="chart-area">
			<LightweightLineChart
				series={model.series}
				samples={model.samples}
				height={220}
				pointMarkers={true}
				hasGaps={model.hasGaps}
				ariaLabel="Portfolio value over time"
				testId="portfolio-history-chart"
			/>
			{#if model.hasGaps}<p class="gap-note">
					Gaps indicate missed worker observations; the line is intentionally not interpolated.
				</p>{/if}
		</div>
	{/if}
</section>

<style>
	.history-panel {
		border: 1px solid #232b2d;
		background: linear-gradient(145deg, rgba(20, 26, 28, 0.95), rgba(12, 16, 18, 0.95));
		border-radius: 13px;
		overflow: hidden;
		margin-top: 16px;
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
	.gap-note {
		margin: 5px 0 0;
		color: #778386;
		font-size: 12px;
	}
	.range-controls {
		display: flex;
		gap: 4px;
		padding: 3px;
		background: #101617;
		border-radius: 7px;
	}
	.range-controls button {
		border: 0;
		border-radius: 5px;
		background: transparent;
		color: #849093;
		cursor: pointer;
		font:
			600 11px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		padding: 6px 8px;
	}
	.range-controls button.active {
		background: #263436;
		color: #b8e8d8;
	}
	.range-controls button:focus-visible {
		outline: 2px solid #b8e8d8;
		outline-offset: 2px;
	}
	.worker-status {
		display: flex;
		align-items: center;
		gap: 7px;
		padding: 10px 24px;
		border-bottom: 1px solid #232b2d;
		color: #8f9d9f;
		font-size: 12px;
	}
	.worker-status.stale {
		color: #edbb70;
		background: rgba(139, 94, 30, 0.1);
	}
	.worker-dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: #5ce1b5;
	}
	.worker-status.stale .worker-dot {
		background: #edbb70;
	}
	.stats {
		display: flex;
		gap: 24px;
		padding: 16px 24px;
		border-bottom: 1px solid #232b2d;
	}
	.stat {
		display: flex;
		flex-direction: column;
	}
	.stat small {
		color: #657174;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		margin-bottom: 4px;
	}
	.stat strong {
		color: #edf3f3;
		font:
			500 15px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
	}
	.stat.gain strong {
		color: #5ce1b5;
	}
	.stat.loss strong {
		color: #f18f8f;
	}
	.chart-area {
		padding: 20px 24px;
	}
	.chart-empty {
		padding: 40px 24px;
		text-align: center;
	}
	.chart-empty p {
		margin: 0 0 6px;
		color: #849093;
		font-size: 14px;
	}
	.chart-empty small {
		color: #697578;
		font-size: 12px;
	}
	.chart-skeleton {
		height: 200px;
		border-radius: 8px;
	}
	@media (max-width: 800px) {
		.panel-heading {
			flex-direction: column;
			align-items: flex-start;
			gap: 12px;
		}
		.stats {
			overflow-x: auto;
			gap: 16px;
		}
	}
</style>
