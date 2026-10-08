<script lang="ts">
	/**
	 * Headline row of a backtest result: net return, buy & hold, max drawdown,
	 * trades, win rate, and profit factor.
	 */
	import {
		compareDecimalStrings,
		SAME_BAR_POLICY_LABEL,
		formatPercent,
		type BacktestResult
	} from '$lib/backtests';
	import { formatUsd } from '$lib/portfolio';

	let {
		result,
		buyAndHold,
		benchmarkLoading
	}: {
		result: BacktestResult;
		/** Buy-and-hold return from the benchmark or the metrics, or null. */
		buyAndHold: string | null;
		benchmarkLoading: boolean;
	} = $props();
</script>

<div class="metrics-row" data-testid="result-metrics">
	<div class="metric">
		<div class="l">Net return</div>
		<div
			class="v"
			class:gain={compareDecimalStrings(result.summary.total_return_fraction, '0') >= 0}
			class:loss={compareDecimalStrings(result.summary.total_return_fraction, '0') < 0}
		>
			{formatPercent(result.summary.total_return_fraction)}
		</div>
		<div class="s">
			{formatUsd(result.summary.total_net_pnl)} net · {formatUsd(result.summary.initial_equity)} →
			{formatUsd(result.summary.final_equity)}
		</div>
	</div>
	<div class="metric">
		<div class="l">Buy &amp; hold</div>
		<div class="v">
			{buyAndHold !== null ? formatPercent(buyAndHold) : benchmarkLoading ? '…' : '—'}
		</div>
		<div class="s">Same window and modeled costs</div>
	</div>
	<div class="metric">
		<div class="l">Max drawdown</div>
		<div class="v loss">{formatPercent(result.summary.maximum_drawdown_fraction)}</div>
		<div class="s">{formatUsd(result.summary.maximum_drawdown)}</div>
	</div>
	<div class="metric">
		<div class="l">Trades</div>
		<div class="v">{result.summary.trade_count}</div>
		<div class="s">{result.summary.winning_trade_count} winning</div>
	</div>
	<div class="metric">
		<div class="l">Win rate</div>
		<div class="v">{formatPercent(result.summary.win_rate)}</div>
		<div class="s">Closed trades</div>
	</div>
	<div class="metric">
		<div class="l">Profit factor</div>
		<div class="v">{result.summary.profit_factor ?? 'N/A'}</div>
		<div class="s">{SAME_BAR_POLICY_LABEL}</div>
	</div>
</div>

<style>
	.metrics-row {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: 12px;
	}
	.metric .l {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.metric .v {
		margin-top: 2px;
		font-size: var(--fs-xl);
		font-weight: 600;
		letter-spacing: -0.01em;
	}
	.metric .s {
		margin-top: 2px;
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.gain {
		color: var(--accent);
	}
	.loss {
		color: var(--neg);
	}
	@media (max-width: 1100px) {
		.metrics-row {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}
	@media (max-width: 520px) {
		.metrics-row {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
