<script lang="ts">
	/**
	 * Buy-and-hold comparison of a backtest result: strategy versus buy-and-hold
	 * return and drawdown over the same run window, dataset, and modeled costs.
	 */
	import {
		compareDecimalStrings,
		formatPercent,
		type BacktestBenchmark,
		type BacktestResult
	} from '$lib/backtests';
	import { formatUsd } from '$lib/portfolio';

	let {
		result,
		benchmark,
		benchmarkLoading,
		benchmarkError
	}: {
		result: BacktestResult;
		benchmark: BacktestBenchmark | null;
		benchmarkLoading: boolean;
		benchmarkError: string | null;
	} = $props();
</script>

<div
	class="benchmark-panel"
	data-testid="benchmark-comparison"
	aria-label="Buy-and-hold benchmark comparison"
>
	<div class="panel-heading">
		<div>
			<h3>Buy-and-hold comparison</h3>
			<p>Derived from the same verified run window, dataset, and modeled costs</p>
		</div>
		<span>read-only benchmark</span>
	</div>
	{#if benchmarkLoading}<div class="empty"><p>Loading benchmark comparison…</p></div>
	{:else if benchmarkError}<div class="empty" data-testid="benchmark-unavailable">
			<p>Benchmark comparison is unavailable.</p>
			<small>{benchmarkError}</small>
		</div>
	{:else if benchmark}<div class="benchmark-grid">
			<article>
				<small>Strategy net return</small>
				<strong
					class:gain={compareDecimalStrings(result.summary.total_return_fraction, '0') >= 0}
					class:loss={compareDecimalStrings(result.summary.total_return_fraction, '0') < 0}
					>{formatPercent(result.summary.total_return_fraction)}</strong
				>
				<span>{formatUsd(result.summary.total_net_pnl)} net PnL</span>
			</article>
			<article>
				<small>Buy-and-hold return</small>
				<strong
					class:gain={compareDecimalStrings(benchmark.total_return_fraction, '0') >= 0}
					class:loss={compareDecimalStrings(benchmark.total_return_fraction, '0') < 0}
					>{formatPercent(benchmark.total_return_fraction)}</strong
				>
				<span>{formatUsd(benchmark.total_net_pnl)} net PnL</span>
			</article>
			<article>
				<small>Strategy max drawdown</small>
				<strong class="loss">{formatPercent(result.summary.maximum_drawdown_fraction)}</strong>
				<span>{formatUsd(result.summary.maximum_drawdown)}</span>
			</article>
			<article>
				<small>Buy-and-hold max drawdown</small>
				<strong class="loss">{formatPercent(benchmark.maximum_drawdown_fraction)}</strong>
				<span>{formatUsd(benchmark.maximum_drawdown)}</span>
			</article>
		</div>
		<div class="benchmark-evidence">
			<span
				>Buy at {benchmark.entry_price} · liquidate at {benchmark.exit_price} · {benchmark.evaluation_bars}
				evaluated bars</span
			>
			<span
				>Modeled fees: {formatUsd(benchmark.total_fees)}{benchmark.total_spread_cost
					? ` · spread: ${formatUsd(benchmark.total_spread_cost)}`
					: ''}</span
			>
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
	small {
		color: var(--faint);
		font-size: 11px;
	}
	.gain {
		color: var(--accent);
	}
	.loss {
		color: var(--neg);
	}
	.benchmark-panel {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
		overflow: hidden;
	}
	.benchmark-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 1px;
		background: var(--hover);
	}
	.benchmark-grid article {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding: 18px;
		background: var(--surface);
	}
	.benchmark-grid strong {
		font:
			500 22px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		letter-spacing: -0.04em;
	}
	.benchmark-grid span,
	.benchmark-evidence {
		color: var(--faint);
		font-size: 11px;
	}
	.benchmark-evidence {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 18px;
		padding: 14px 18px;
		border-top: 1px solid var(--line);
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
		.benchmark-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 520px) {
		.benchmark-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
