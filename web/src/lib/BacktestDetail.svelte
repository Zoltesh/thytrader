<script lang="ts">
	import {
		formatBrokerAssumptions,
		formatPercent,
		shortFingerprint,
		type BacktestDetail
	} from '$lib/backtests';
	import { formatUsd } from '$lib/portfolio';

	let {
		detail,
		loading = false,
		error = null,
		onBack
	}: {
		detail: BacktestDetail | null;
		loading?: boolean;
		error?: string | null;
		onBack: () => void;
	} = $props();
	const result = $derived(detail?.result ?? null);
	const equityValues = $derived(result?.equity_curve.map((point) => Number(point.equity)) ?? []);
	const equityPath = $derived.by(() => {
		if (equityValues.length < 2) return '';
		const width = 760;
		const height = 180;
		const padding = 26;
		const low = Math.min(...equityValues);
		const high = Math.max(...equityValues);
		const range = high - low || 1;
		return equityValues
			.map((value, index) => {
				const x = padding + ((width - padding * 2) * index) / (equityValues.length - 1);
				const y = height - padding - ((value - low) / range) * (height - padding * 2);
				return `${x.toFixed(1)},${y.toFixed(1)}`;
			})
			.join(' ');
	});
</script>

<section class="detail" aria-label="Backtest result detail">
	<div class="heading">
		<div>
			<button type="button" onclick={onBack}>← All backtests</button>
			<h2>Simulation result</h2>
			<p>Historical evidence only · this page cannot submit orders.</p>
		</div>
	</div>
	{#if loading}<div class="empty"><div class="skeleton"></div></div>
	{:else if error}<div class="empty">
			<p>Backtest result could not be loaded.</p>
			<small>{error}</small>
		</div>
	{:else if result && detail}
		<div class="provenance">
			<span>Result <code>{shortFingerprint(detail.result_fingerprint)}</code></span><span
				>Strategy <code>{shortFingerprint(result.strategy_fingerprint)}</code></span
			><span>Dataset <code>{shortFingerprint(result.dataset_fingerprint)}</code></span><span
				>{result.engine_contract_version}</span
			>
		</div>
		<div class="metrics">
			<article>
				<small>Final equity</small><strong>{formatUsd(result.summary.final_equity)}</strong><span
					>from {formatUsd(result.summary.initial_equity)}</span
				>
			</article>
			<article>
				<small>Net return</small><strong
					class:gain={Number(result.summary.total_return_fraction) >= 0}
					class:loss={Number(result.summary.total_return_fraction) < 0}
					>{formatPercent(result.summary.total_return_fraction)}</strong
				><span>{formatUsd(result.summary.total_net_pnl)} net PnL</span>
			</article>
			<article>
				<small>Maximum drawdown</small><strong class="loss"
					>{formatPercent(result.summary.maximum_drawdown_fraction)}</strong
				><span
					>{result.summary.trade_count} trades · {formatPercent(result.summary.win_rate)} wins</span
				>
			</article>
			<article>
				<small>Profit factor</small><strong>{result.summary.profit_factor ?? 'N/A'}</strong><span
					>Stop-first same-bar policy</span
				>
			</article>
		</div>
		<div class="assumptions">
			<strong>Modeled assumptions</strong>
			<span>{formatBrokerAssumptions(result.broker)}</span>
			<small
				>Long-only, one position · completed close → next-open taker fill · adverse fixed slippage ·
				time exit before intrabar exits · stop first if stop and target collide · terminal force
				close.</small
			>
			{#if result.summary.total_spread_cost !== undefined && result.summary.total_spread_cost !== null}
				<small
					>Total modeled spread cost: {formatUsd(result.summary.total_spread_cost)}. This is a
					disclosed stress assumption, not observed bid/ask data.</small
				>
			{/if}
		</div>
		<div class="equity-panel">
			<div class="panel-heading">
				<div>
					<h3>Equity curve</h3>
					<p>Mark-to-market equity at each evaluation boundary</p>
				</div>
				<span>{result.equity_curve.length} points</span>
			</div>
			{#if equityPath}<svg viewBox="0 0 760 180" role="img" aria-label="Backtest equity curve"
					><polyline points={equityPath} fill="none" stroke="#5ce1b5" stroke-width="3" /></svg
				>{:else}<div class="empty"><p>One equity observation is available.</p></div>{/if}
		</div>
		<div class="ledger">
			<div class="panel-heading">
				<div>
					<h3>Trade ledger</h3>
					<p>Exact modeled entry and exit fills</p>
				</div>
				<span>{result.trades.length} closed {result.trades.length === 1 ? 'trade' : 'trades'}</span>
			</div>
			{#if result.trades.length === 0}<div class="empty">
					<p>No qualifying trades were modeled.</p>
				</div>{:else}<div class="table-wrap">
					<table>
						<thead
							><tr
								><th>Entry</th><th>Exit</th><th>Reason</th><th>Quantity</th><th>Fees</th><th
									>Spread</th
								><th>Net PnL</th><th>Bars</th></tr
							></thead
						><tbody
							>{#each result.trades as trade, index (index)}<tr
									><td
										>{new Date(trade.entry.candle_starts_at).toLocaleString()}<small
											>{trade.entry.price}</small
										></td
									><td
										>{new Date(trade.exit.candle_starts_at).toLocaleString()}<small
											>{trade.exit.price}</small
										></td
									><td>{trade.exit.reason.replace('_', ' ')}</td><td>{trade.entry.quantity}</td><td
										>{formatUsd((Number(trade.entry.fee) + Number(trade.exit.fee)).toString())}</td
									><td
										>{trade.entry.spread_cost && trade.exit.spread_cost
											? formatUsd(
													(
														Number(trade.entry.spread_cost) * Number(trade.entry.quantity) +
														Number(trade.exit.spread_cost) * Number(trade.exit.quantity)
													).toString()
												)
											: '—'}</td
									><td
										class:gain={Number(trade.net_pnl) >= 0}
										class:loss={Number(trade.net_pnl) < 0}>{formatUsd(trade.net_pnl)}</td
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
	.heading {
		display: flex;
		justify-content: space-between;
	}
	.heading button {
		padding: 0;
		border: 0;
		background: transparent;
		color: #5ce1b5;
		cursor: pointer;
		font-size: 13px;
	}
	h2 {
		margin: 12px 0 6px;
		font-size: 28px;
	}
	h3 {
		margin: 0;
		font-size: 18px;
	}
	p,
	.heading p {
		margin: 0;
		color: #778386;
		font-size: 12px;
	}
	.provenance,
	.assumptions,
	.equity-panel,
	.ledger,
	.metrics article {
		border: 1px solid #232b2d;
		border-radius: 13px;
		background: linear-gradient(145deg, rgba(20, 26, 28, 0.95), rgba(12, 16, 18, 0.95));
	}
	.provenance {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
		padding: 14px 18px;
		color: #849093;
		font-size: 11px;
	}
	code {
		color: #b8e8d8;
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
		color: #718083;
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
		color: #849093;
		font-size: 11px;
	}
	.gain {
		color: #5ce1b5;
	}
	.loss {
		color: #ed8b8b;
	}
	.assumptions {
		padding: 15px 18px;
		display: grid;
		gap: 7px;
		color: #8f9d9f;
		font-size: 12px;
		line-height: 1.5;
	}
	.assumptions strong {
		color: #e9edf1;
	}
	.panel-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 20px 22px;
		border-bottom: 1px solid #232b2d;
	}
	.panel-heading > span {
		color: #778386;
		font-size: 12px;
	}
	.equity-panel svg {
		display: block;
		width: 100%;
		height: 190px;
		padding: 14px 20px 6px;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
	}
	th {
		color: #657174;
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
		border-top: 1px solid #1d2426;
		color: #aeb9bb;
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
		color: #aeb9bb;
		font-size: 14px;
	}
	.skeleton {
		height: 60px;
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
