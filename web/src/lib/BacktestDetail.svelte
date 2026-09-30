<script lang="ts">
	import {
		backtestEquityChartModel,
		compareDecimalStrings,
		formatBrokerAssumptions,
		formatEngineFillAssumptions,
		formatFillFee,
		formatPercent,
		formatPublishedCosts,
		formatSameBarPolicy,
		formatSpreadCostNote,
		shortFingerprint,
		type BacktestBenchmark,
		type BacktestDetail,
		type BacktestPerformanceMetrics
	} from '$lib/backtests';
	import LightweightLineChart from '$lib/LightweightLineChart.svelte';
	import { formatUsd } from '$lib/portfolio';
	import { engineContractLabel } from '$lib/research-studies';
	import { formatUtcTimestamp } from '$lib/time';

	let {
		detail,
		benchmark = null,
		benchmarkLoading = false,
		benchmarkError = null,
		metrics = null,
		metricsLoading = false,
		metricsError = null,
		loading = false,
		error = null,
		backLabel = '← All backtests',
		versionLabel = null,
		publishedAt = null,
		onBack
	}: {
		detail: BacktestDetail | null;
		benchmark?: BacktestBenchmark | null;
		benchmarkLoading?: boolean;
		benchmarkError?: string | null;
		metrics?: BacktestPerformanceMetrics | null;
		metricsLoading?: boolean;
		metricsError?: string | null;
		loading?: boolean;
		error?: string | null;
		/** Label of the button that leaves this detail view. */
		backLabel?: string;
		/** `v3` when the owning strategy version is known (workspace Test stage). */
		versionLabel?: string | null;
		/** Publication time of this result when the caller's list knows it. */
		publishedAt?: string | null;
		onBack: () => void;
	} = $props();
	const result = $derived(detail?.result ?? null);
	const equityModel = $derived(backtestEquityChartModel(result?.equity_curve ?? []));
	const equityPointCount = $derived(result?.equity_curve.length ?? 0);
	/** `2026-08-01 → 2026-09-29 · 1,400 bars`, read from the result's own equity curve. */
	const periodText = $derived.by((): string => {
		if (result === null) return '';
		const bars = `${result.summary.evaluation_bars} bars`;
		const curve = result.equity_curve;
		if (curve.length < 2) return bars;
		return `${curve[0].candle_starts_at.slice(0, 10)} → ${curve[curve.length - 1].candle_starts_at.slice(0, 10)} · ${bars}`;
	});
	const buyAndHold = $derived(
		benchmark?.total_return_fraction ?? metrics?.buy_and_hold_return_fraction ?? null
	);
	const spreadCostNote = $derived(
		result
			? formatSpreadCostNote(result.engine_contract_version, result.summary.total_spread_cost)
			: null
	);
</script>

<section class="detail" aria-label="Backtest result detail">
	<div class="result-head" data-testid="backtest-result-head">
		<div class="result-id">
			<h2>
				{#if result}{versionLabel ? `${versionLabel} · ` : ''}{periodText} · {engineContractLabel(
						result.engine_contract_version
					)}{:else}Backtest result{/if}
			</h2>
			{#if publishedAt}<span class="faint">{formatUtcTimestamp(publishedAt)}</span>{/if}
			<span class="chip">Simulated result (candle-based fills)</span>
		</div>
		<button type="button" class="back" onclick={onBack}>{backLabel}</button>
	</div>
	<p class="evidence-only">
		Historical evidence only · immutable published result · this page cannot submit orders or
		regenerate the run.
	</p>
	{#if loading}<div class="empty"><div class="skeleton"></div></div>
	{:else if error}<div class="empty">
			<p>Backtest result could not be loaded.</p>
			<small>{error}</small>
		</div>
	{:else if result && detail}
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
				<div class="s">{formatSameBarPolicy(result.engine_contract_version)}</div>
			</div>
		</div>
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
					<small>A curve appears when the result includes at least two evaluation boundaries.</small
					>
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
		<div class="assumptions" data-testid="modeled-assumptions">
			<strong>Modeled assumptions</strong>
			<span>{formatBrokerAssumptions(result.broker, result.engine_contract_version)}</span>
			<span data-testid="published-costs"
				>{formatPublishedCosts(detail.costs, result.engine_contract_version)}</span
			>
			<small data-testid="fill-assumptions"
				>{formatEngineFillAssumptions(result.engine_contract_version)}</small
			>
			{#if spreadCostNote}<small>{spreadCostNote}</small>{/if}
		</div>
		<details class="evidence" data-testid="result-evidence">
			<summary>Evidence <span class="faint">fingerprints and engine contract</span></summary>
			<div class="provenance">
				<span title={detail.result_fingerprint}
					>Result <code>{shortFingerprint(detail.result_fingerprint)}</code></span
				><span title={result.strategy_fingerprint}
					>Strategy <code>{shortFingerprint(result.strategy_fingerprint)}</code></span
				><span title={result.dataset_fingerprint}
					>Dataset <code>{shortFingerprint(result.dataset_fingerprint)}</code></span
				><span title={result.run_fingerprint}
					>Run <code>{shortFingerprint(result.run_fingerprint)}</code></span
				><span>{result.engine_contract_version}</span>
			</div>
		</details>
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
		<div class="ledger">
			<div class="panel-heading">
				<div>
					<h3>Trade ledger</h3>
					<p>Modeled entry and exit fills (not venue fills)</p>
				</div>
				<span>{result.trades.length} closed {result.trades.length === 1 ? 'trade' : 'trades'}</span>
			</div>
			{#if result.trades.length === 0}<div class="empty">
					<p>No qualifying trades were modeled.</p>
				</div>{:else}<div class="table-wrap">
					<table>
						<thead
							><tr
								><th>Entry</th><th>Exit</th><th>Reason</th><th>Quantity</th><th
									>Fees (entry / exit)</th
								><th>Spread/unit (entry / exit)</th><th>Net PnL</th><th>Bars</th></tr
							></thead
						><tbody
							>{#each result.trades as trade, index (index)}<tr
									><td
										>{formatUtcTimestamp(trade.entry.candle_starts_at)}<small
											>{trade.entry.price}</small
										></td
									><td
										>{formatUtcTimestamp(trade.exit.candle_starts_at)}<small
											>{trade.exit.price}</small
										></td
									><td>{trade.exit.reason.replace('_', ' ')}</td><td>{trade.entry.quantity}</td><td
										>{formatFillFee(trade.entry)} / {formatFillFee(trade.exit)}</td
									><td
										>{trade.entry.executable_side === 'ask' &&
										trade.exit.executable_side === 'bid' &&
										trade.entry.spread_cost &&
										trade.exit.spread_cost
											? `${formatUsd(trade.entry.spread_cost)} / ${formatUsd(trade.exit.spread_cost)}`
											: '—'}</td
									><td
										class:gain={compareDecimalStrings(trade.net_pnl, '0') >= 0}
										class:loss={compareDecimalStrings(trade.net_pnl, '0') < 0}
										>{formatUsd(trade.net_pnl)}</td
									><td>{trade.holding_bars}</td></tr
								>{/each}</tbody
						>
					</table>
				</div>{/if}
		</div>
	{/if}
</section>

<style>
	.detail {
		display: grid;
		gap: 16px;
	}
	.result-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px 16px;
	}
	.result-id {
		display: flex;
		flex: 1;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px 12px;
		min-width: 0;
	}
	.result-id h2 {
		margin: 0;
		font-size: var(--fs-md);
	}
	.result-id .chip {
		margin-left: auto;
	}
	.back {
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--accent);
		cursor: pointer;
		font-size: 13px;
	}
	.evidence-only {
		margin-top: -8px;
	}
	.faint {
		color: var(--faint);
	}
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
	.evidence {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
	}
	.evidence summary {
		padding: 12px 18px;
		cursor: pointer;
		font-weight: 600;
	}
	.evidence summary .faint {
		margin-left: 6px;
		font-weight: 400;
		font-size: var(--fs-sm);
	}
	.evidence .provenance {
		border: 0;
		border-top: 1px solid var(--line);
		border-radius: 0;
	}
	h3 {
		margin: 0;
		font-size: var(--fs-md);
	}
	p {
		margin: 0;
		color: var(--faint);
		font-size: 12px;
	}
	.provenance,
	.assumptions,
	.benchmark-panel,
	.equity-panel,
	.ledger,
	.metrics article {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
	}
	.provenance {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
		padding: 14px 18px;
		color: var(--faint);
		font-size: 11px;
	}
	code {
		color: var(--code);
	}
	.metrics {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 14px;
	}
	.metrics article {
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
	.gain {
		color: var(--accent);
	}
	.loss {
		color: var(--neg);
	}
	.benchmark-panel {
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
	.assumptions {
		padding: 15px 18px;
		display: grid;
		gap: 7px;
		color: var(--muted);
		font-size: 12px;
		line-height: 1.5;
	}
	.assumptions strong {
		color: var(--text);
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
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
	}
	th {
		color: var(--faint);
		font:
			500 10px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		text-align: right;
		padding: 13px 18px;
	}
	th:first-child,
	td:first-child {
		text-align: left;
	}
	td {
		padding: 14px 18px;
		border-top: 1px solid var(--line);
		color: var(--muted);
		text-align: right;
		font:
			400 12px ui-monospace,
			SFMono-Regular,
			Consolas,
			monospace;
		white-space: nowrap;
	}
	td small {
		display: block;
		margin-top: 4px;
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
	.skeleton {
		height: 60px;
		border-radius: 8px;
		background: linear-gradient(90deg, var(--surface-2), var(--hover), var(--surface-2));
		background-size: 200%;
		animation: shimmer 1.4s infinite;
	}
	@keyframes shimmer {
		to {
			background-position: -200% 0;
		}
	}
	@media (max-width: 1100px) {
		.metrics-row {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}
	@media (max-width: 800px) {
		.metrics {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.benchmark-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 520px) {
		.metrics-row {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.metrics {
			grid-template-columns: 1fr;
		}
		.benchmark-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
