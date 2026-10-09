<script lang="ts">
	/**
	 * Trade ledger of a backtest result: modeled entry and exit fills (not venue
	 * fills) with reason, quantity, fees, spread cost on stressed runs, net PnL, bars.
	 */
	import {
		compareDecimalStrings,
		formatExitReason,
		formatFillFee,
		type BacktestResult
	} from '$lib/backtests';
	import { formatUsd } from '$lib/portfolio';
	import { formatUtcTimestamp } from '$lib/time';

	let {
		result,
		spreadStressed
	}: {
		result: BacktestResult;
		/** Spread-stressed runs show a spread-cost column. */
		spreadStressed: boolean;
	} = $props();
</script>

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
						><th>Entry</th><th>Exit</th><th>Reason</th><th>Quantity</th><th>Fees (entry / exit)</th
						>{#if spreadStressed}<th>Spread cost (entry / exit)</th>{/if}<th>Net PnL</th><th
							>Bars</th
						></tr
					></thead
				><tbody
					>{#each result.trades as trade, index (index)}<tr
							><td
								>{formatUtcTimestamp(trade.entry.candle_starts_at)}<small>{trade.entry.price}</small
								></td
							><td
								>{formatUtcTimestamp(trade.exit.candle_starts_at)}<small>{trade.exit.price}</small
								></td
							><td>{formatExitReason(trade.exit.reason)}</td><td>{trade.entry.quantity}</td><td
								>{formatFillFee(trade.entry)} / {formatFillFee(trade.exit)}</td
							>{#if spreadStressed}<td
									>{trade.entry.spread_cost ? formatUsd(trade.entry.spread_cost) : '—'} / {trade
										.exit.spread_cost
										? formatUsd(trade.exit.spread_cost)
										: '—'}</td
								>{/if}<td
								class:gain={compareDecimalStrings(trade.net_pnl, '0') >= 0}
								class:loss={compareDecimalStrings(trade.net_pnl, '0') < 0}
								>{formatUsd(trade.net_pnl)}</td
							><td>{trade.holding_bars}</td></tr
						>{/each}</tbody
				>
			</table>
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
	.ledger {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
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
</style>
