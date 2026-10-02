<script lang="ts">
	/**
	 * Home "Portfolio value" chart card (ADR 0084) over `GET /api/v1/portfolio/history`.
	 *
	 * 1D · 1W · 1M · 3M map onto the API's 24h · 7d · 30d ranges; 3M reads `all`
	 * and keeps the last 90 days. X follows wall-clock time and missed worker
	 * observations stay visible as gaps: the line is never interpolated. When the
	 * API thins a long range to representative snapshots, a gap means a hole
	 * longer than the spacing between those snapshots, not the thinning itself.
	 */
	import LightweightLineChart from '$lib/LightweightLineChart.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import {
		HOME_CHART_RANGES,
		expectedSampleSpacingSeconds,
		isThinnedHistory,
		type HomeChartRange
	} from '$lib/home/history-range';
	import type { ChartHistory } from '$lib/home/home-data';
	import { displayMinus, formatShortUtc } from '$lib/home/home-format';
	import type { Load } from '$lib/home/load';
	import {
		formatConfiguredSamplingInterval,
		formatUsd,
		isHistoryStale,
		portfolioChange,
		portfolioHistoryChartModel,
		type HistoryEntry
	} from '$lib/portfolio';

	let {
		history,
		range,
		onRangeChange,
		onRetry
	}: {
		history: Load<ChartHistory>;
		range: HomeChartRange;
		onRangeChange: (range: HomeChartRange) => void;
		onRetry: () => void;
	} = $props();

	const rangeOptions = HOME_CHART_RANGES.map((option) => ({ id: option.id, label: option.id }));
	const ready = $derived(
		history.status === 'ready' && history.data.kind === 'ready' ? history.data : null
	);
	const entries = $derived<HistoryEntry[]>(ready?.entries ?? []);
	const cadence = $derived(ready?.samplingIntervalSeconds ?? 300);
	const spacing = $derived(
		ready === null ? cadence : expectedSampleSpacingSeconds(entries, cadence, ready.responseCount)
	);
	const thinned = $derived(ready !== null && isThinnedHistory(ready.responseCount));
	const model = $derived(portfolioHistoryChartModel([...entries].reverse(), spacing));
	const change = $derived(portfolioChange(entries));
	const workerStale = $derived(isHistoryStale(entries, cadence));
	const latest = $derived(entries[0] ?? null);
	const option = $derived(HOME_CHART_RANGES.find((candidate) => candidate.id === range));

	function changeText(
		direction: 'gain' | 'loss' | 'flat',
		amount: string,
		percent: string | null
	): string {
		const magnitude = formatUsd(amount.startsWith('-') ? amount.slice(1) : amount);
		const sign = direction === 'gain' ? '+' : direction === 'loss' ? '−' : '';
		const pct =
			percent === null ? '' : ` (${direction === 'gain' ? '+' : ''}${displayMinus(percent)}%)`;
		return `${sign}${magnitude}${pct}`;
	}
</script>

<section
	class="card chart-card"
	aria-labelledby="portfolio-chart-title"
	data-testid="portfolio-chart"
>
	<div class="card-head">
		<h2 id="portfolio-chart-title">Portfolio value</h2>
		<Segmented
			label="Chart range"
			options={rangeOptions}
			value={range}
			onchange={onRangeChange}
			testId="chart-range"
		/>
	</div>
	<div class="card-body">
		{#if history.status === 'loading'}
			<div class="skeleton chart-skeleton" aria-hidden="true"></div>
			<p class="sr-only">Loading portfolio history…</p>
		{:else if history.status === 'error'}
			<div class="chart-empty" role="status">
				<p>Portfolio history could not be loaded.</p>
				<small>Try again after the API and worker report healthy. ({history.error})</small>
				<button type="button" class="btn retry" onclick={onRetry}>Retry</button>
			</div>
		{:else if history.data.kind === 'unavailable'}
			<div class="chart-empty">
				<p>Portfolio history is unavailable on this installation.</p>
				<small>Start the full local stack to enable durable scheduled snapshots.</small>
			</div>
		{:else}
			<p class="meta" data-testid="chart-meta">
				{entries.length} sampled {entries.length === 1 ? 'snapshot' : 'snapshots'} · target interval
				{formatConfiguredSamplingInterval(cadence)}{#if thinned}
					· representative sample{/if}{#if option?.clipDays}
					· {option.description} of the all-time history{/if}
			</p>
			{#if latest !== null}
				<p class="cadence" class:stale={workerStale}>
					<span class="dot" aria-hidden="true"></span>
					{workerStale ? 'Snapshot cadence may be behind' : 'Snapshot cadence is current'} · last snapshot
					{formatShortUtc(latest.as_of)}
				</p>
			{/if}
			{#if entries.length >= 2}
				<dl class="stats">
					<div>
						<dt>Latest</dt>
						<dd>{formatUsd(entries[0].total_value.amount)}</dd>
					</div>
					<div>
						<dt>High</dt>
						<dd>{formatUsd(model.maxAmount)}</dd>
					</div>
					<div>
						<dt>Low</dt>
						<dd>{formatUsd(model.minAmount)}</dd>
					</div>
					{#if change}
						<div class:gain={change.direction === 'gain'} class:loss={change.direction === 'loss'}>
							<dt>
								Range change ({change.direction === 'gain'
									? 'up'
									: change.direction === 'loss'
										? 'down'
										: 'unchanged'})
							</dt>
							<dd>{changeText(change.direction, change.amount, change.percent)}</dd>
						</div>
					{/if}
				</dl>
			{/if}
			{#if entries.length === 0}
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
				<LightweightLineChart
					series={model.series}
					samples={model.samples}
					height={220}
					pointMarkers={true}
					hasGaps={model.hasGaps}
					ariaLabel="Portfolio value over the {option?.description ?? 'selected range'}"
					testId="portfolio-history-chart"
				/>
				{#if model.hasGaps}<p class="gap-note">
						Gaps indicate missed worker observations; the line is intentionally not interpolated.
					</p>{/if}
			{/if}
		{/if}
	</div>
</section>

<style>
	.chart-card {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head :global(.seg) {
		margin-left: auto;
	}
	.card-body {
		padding: 10px 16px 14px;
	}
	.meta,
	.gap-note {
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.gap-note {
		margin-top: 6px;
	}
	.cadence {
		display: flex;
		align-items: center;
		gap: 7px;
		margin: 4px 0 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.cadence.stale {
		color: var(--warn);
	}
	.dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--accent);
	}
	.cadence.stale .dot {
		background: var(--warn);
	}
	.stats {
		display: flex;
		flex-wrap: wrap;
		gap: 6px 22px;
		margin: 10px 0 8px;
	}
	.stats div {
		display: flex;
		flex-direction: column;
	}
	.stats dt {
		color: var(--faint);
		font-size: 10px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}
	.stats dd {
		margin: 2px 0 0;
		color: var(--text);
		font: 500 var(--fs-base) var(--font-mono);
	}
	.stats .gain dd {
		color: var(--pos);
	}
	.stats .loss dd {
		color: var(--neg);
	}
	.chart-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 6px;
		padding: 36px 12px;
		text-align: center;
	}
	.chart-empty p {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-md);
	}
	.chart-empty small {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.retry {
		margin-top: 6px;
	}
	.chart-skeleton {
		height: 260px;
		margin: 4px 0;
	}
</style>
