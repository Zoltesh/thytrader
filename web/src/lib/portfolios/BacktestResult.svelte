<script lang="ts">
	/**
	 * One combined portfolio backtest result: headline metrics, equity against the
	 * equal-weight basket, costs, what each sleeve contributed, correlation and
	 * overlap, and the disclosures verbatim. Results are simulated from candles.
	 */
	import { formatPercent } from '$lib/portfolio';
	import {
		drawdownPercent,
		quoteText,
		ratioText,
		signedPercent,
		windowText,
		type PortfolioBacktestDetail
	} from '$lib/portfolios';
	import { costsText, tone } from './backtest';
	import BacktestContribution from './BacktestContribution.svelte';
	import BacktestCorrelation from './BacktestCorrelation.svelte';
	import PortfolioEquityChart from './PortfolioEquityChart.svelte';

	let { detail }: { detail: PortfolioBacktestDetail } = $props();

	const result = $derived(detail.result);
	const summary = $derived(result.summary);
</script>

<section class="card result" aria-label="Portfolio backtest result">
	<div class="card-head">
		<h2>
			All sleeves together · {windowText(result.evaluation_start, result.evaluation_end)}
		</h2>
		<span class="chip">Simulated result (candle-based fills)</span>
	</div>
	<div class="metrics" data-testid="backtest-metrics">
		<div class="metric">
			<div class="l">Net return</div>
			<div class="v {tone(summary.total_return_fraction)}">
				{signedPercent(summary.total_return_fraction)}
			</div>
			<div class="n">{quoteText(summary.final_equity, result.quote_currency)} final</div>
		</div>
		<div class="metric">
			<div class="l">Max drawdown</div>
			<div class="v neg">{drawdownPercent(summary.maximum_drawdown_fraction)}</div>
		</div>
		<div class="metric">
			<div class="l">Best sleeve alone</div>
			<div class="v">{signedPercent(summary.best_sleeve_return_fraction)}</div>
			<div class="n">
				its drawdown {drawdownPercent(summary.best_sleeve_maximum_drawdown_fraction)}
			</div>
		</div>
		<div class="metric">
			<div class="l">Capital idle</div>
			<div class="v">{formatPercent(summary.idle_capital_fraction)}</div>
		</div>
		<div class="metric">
			<div class="l">Equal-weight basket</div>
			<div class="v {tone(result.basket.total_return_fraction)}">
				{signedPercent(result.basket.total_return_fraction)}
			</div>
			<div class="n">
				buy &amp; hold, drawdown {drawdownPercent(result.basket.maximum_drawdown_fraction)}
			</div>
		</div>
		<div class="metric">
			<div class="l">Sharpe</div>
			<div class="v">{ratioText(result.metrics.sharpe)}</div>
			<div class="n">rf 0 · {summary.trade_count} trades</div>
		</div>
	</div>
	<PortfolioEquityChart
		points={result.equity_curve}
		currency={result.quote_currency}
		ariaLabel="Combined portfolio equity {signedPercent(
			summary.total_return_fraction
		)} against the equal-weight basket {signedPercent(result.basket.total_return_fraction)}"
	/>
	{#if detail.equity_curve_downsampled}
		<p class="faint small">
			Chart thinned to {result.equity_curve.length} of {detail.equity_curve_points} points for display;
			the stored result keeps every point.
		</p>
	{/if}
	<p class="faint small">
		Costs: {costsText(result.costs)} · revision {result.portfolio_revision}
	</p>
</section>

<BacktestContribution {result} />

<BacktestCorrelation {result} />

<section class="card body" aria-label="How this was simulated">
	<h2>How this was simulated</h2>
	<ul class="disclosures" data-testid="backtest-disclosures">
		{#each result.disclosures as text, index (index)}
			<li>{text}</li>
		{/each}
	</ul>
</section>

<style>
	.card {
		margin-top: 16px;
	}
	.card-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head .chip {
		margin-left: auto;
	}
	.result .metrics,
	.result :global(.chart),
	.result > p {
		margin: 14px 16px;
	}
	.metrics {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: 14px;
	}
	.metric .l {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.metric .v {
		margin-top: 2px;
		font-size: var(--fs-xl);
		font-weight: 600;
	}
	.metric .n {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.body {
		padding: 16px 18px;
	}
	.disclosures {
		margin: 10px 0 0;
		padding-left: 18px;
		color: var(--muted);
		display: grid;
		gap: 6px;
	}
	.pos {
		color: var(--pos);
	}
	.neg {
		color: var(--neg);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
	@media (max-width: 1100px) {
		.metrics {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}
</style>
