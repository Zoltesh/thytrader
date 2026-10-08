<script lang="ts">
	/**
	 * Mark-to-model equity curve of a backtest result, with distinct copy when the
	 * result recorded no observation or only one.
	 */
	import { backtestEquityChartModel, type BacktestResult } from '$lib/backtests';
	import LightweightLineChart from '$lib/LightweightLineChart.svelte';

	let { result }: { result: BacktestResult } = $props();

	const equityModel = $derived(backtestEquityChartModel(result.equity_curve));
	const equityPointCount = $derived(result.equity_curve.length);
</script>

<div class="equity-panel">
	<div class="panel-heading">
		<div>
			<h3>Equity curve</h3>
			<p>
				Mark-to-model research equity at each evaluation boundary — not live prices or profit
				theater.
			</p>
		</div>
		<span>{equityPointCount} {equityPointCount === 1 ? 'point' : 'points'}</span>
	</div>
	{#if equityPointCount === 0}
		<div class="empty">
			<p>No equity observations were recorded for this result.</p>
			<small>The trade ledger remains the fill audit; this chart does not invent a path.</small>
		</div>
	{:else if equityPointCount === 1}
		<div class="empty">
			<p>One equity observation is available.</p>
			<small>A curve appears when the result includes at least two evaluation boundaries.</small>
		</div>
	{:else}
		<div class="equity-chart">
			<LightweightLineChart
				series={equityModel.series}
				samples={equityModel.samples}
				height={180}
				pointMarkers={false}
				ariaLabel="Backtest mark-to-model equity curve"
				testId="backtest-equity-chart"
			/>
		</div>
	{/if}
</div>

<style>
	h3 {
		margin: 0;
		font-size: var(--fs-md);
	}
	p {
		margin: 0;
		color: var(--faint);
		font-size: 12px;
	}
	.equity-panel {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
	}
	small {
		color: var(--faint);
		font-size: 11px;
	}
	.panel-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 14px 18px;
		border-bottom: 1px solid var(--line);
	}
	.panel-heading > span {
		color: var(--faint);
		font-size: 12px;
	}
	.equity-chart {
		padding: 14px 20px 16px;
	}
	.empty {
		padding: 32px;
		text-align: center;
	}
	.empty p {
		margin: 0 0 6px;
		color: var(--muted);
		font-size: 14px;
	}
	.empty small {
		color: var(--faint);
		font-size: 12px;
	}
</style>
