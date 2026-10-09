<script lang="ts">
	/**
	 * Read-only ratio metrics derived from a backtest result's equity curve and
	 * trades: Sharpe and Sortino, Calmar and CAGR, SQN and volatility, exposure.
	 */
	import { formatPercent, type BacktestPerformanceMetrics } from '$lib/backtests';

	let {
		metrics,
		metricsLoading,
		metricsError
	}: {
		metrics: BacktestPerformanceMetrics | null;
		metricsLoading: boolean;
		metricsError: string | null;
	} = $props();
</script>

<div
	class="metrics-panel"
	data-testid="performance-metrics"
	aria-label="Derived performance metrics"
>
	<div class="panel-heading">
		<div>
			<h3>Ratio metrics</h3>
			<p>Derived from this result’s equity curve and trades · rf=0 · 365-day bar clock</p>
		</div>
		<span>read-only metrics</span>
	</div>
	{#if metricsLoading}<div class="empty"><p>Loading performance metrics…</p></div>
	{:else if metricsError}<div class="empty" data-testid="metrics-unavailable">
			<p>Performance metrics are unavailable.</p>
			<small>{metricsError}</small>
		</div>
	{:else if metrics}<div class="metrics">
			<article>
				<small>Sharpe</small><strong>{metrics.sharpe ?? 'N/A'}</strong><span
					>Sortino {metrics.sortino ?? 'N/A'}</span
				>
			</article>
			<article>
				<small>Calmar</small><strong>{metrics.calmar ?? 'N/A'}</strong><span
					>CAGR {metrics.cagr ?? 'N/A'}</span
				>
			</article>
			<article>
				<small>SQN</small><strong>{metrics.sqn ?? 'N/A'}</strong><span
					>vol {metrics.annualized_volatility ?? 'N/A'}</span
				>
			</article>
			<article>
				<small>Exposure</small><strong>{formatPercent(metrics.exposure_fraction)}</strong><span
					>max consecutive losses {metrics.max_consecutive_losses} · BH {metrics.buy_and_hold_return_fraction
						? formatPercent(metrics.buy_and_hold_return_fraction)
						: 'N/A'}</span
				>
			</article>
		</div>{/if}
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
	.metrics {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 14px;
	}
	.metrics article {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	small {
		color: var(--faint);
		font-size: 11px;
	}
	.metrics strong {
		font:
			500 22px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		letter-spacing: -0.04em;
	}
	.metrics span {
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
	@media (max-width: 800px) {
		.metrics {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 520px) {
		.metrics {
			grid-template-columns: 1fr;
		}
	}
</style>
