<script lang="ts">
	import {
		backtestListPageIsFull,
		compareDecimalStrings,
		formatBacktestListBound,
		formatListSpreadCue,
		formatPercent,
		shortFingerprint,
		type BacktestList,
		type BacktestSummaryEntry
	} from '$lib/backtests';
	import { formatUsd } from '$lib/portfolio';
	import { formatUtcTimestamp } from '$lib/time';

	let {
		entries = [] as BacktestSummaryEntry[],
		bound = null as BacktestList | null,
		loading = false,
		availability = 'ready' as 'ready' | 'unavailable' | 'failed',
		pageSize = 10 as 10 | 25 | 50 | 100,
		onPageSizeChange,
		onSelect,
		onOlder,
		onNewer
	}: {
		entries?: BacktestSummaryEntry[];
		bound?: BacktestList | null;
		loading?: boolean;
		availability?: 'ready' | 'unavailable' | 'failed';
		pageSize?: 10 | 25 | 50 | 100;
		onPageSizeChange: (event: Event) => void;
		onSelect: (fingerprint: string) => void;
		onOlder: () => void;
		onNewer: () => void;
	} = $props();

	const boundLabel = $derived(bound === null ? null : formatBacktestListBound(bound));
	const pageFull = $derived(bound !== null && backtestListPageIsFull(bound));
	const canNewer = $derived((bound?.offset ?? 0) > 0);
	const canOlder = $derived(pageFull && bound?.has_more !== false);
</script>

<section class="backtest-panel" aria-label="Saved backtests">
	<div class="panel-heading">
		<div>
			<h2>Saved backtests</h2>
			<p>Historical simulations of strategy snapshots · not evidence of future profit</p>
		</div>
		<div class="page-controls">
			<label
				>Rows per page
				<select data-testid="backtest-page-size" value={pageSize} onchange={onPageSizeChange}>
					{#each [10, 25, 50, 100] as size (size)}<option value={size}>{size}</option>{/each}
				</select>
			</label>
			{#if boundLabel !== null}<span data-testid="backtest-list-bound">{boundLabel}</span>{/if}
		</div>
	</div>
	{#if loading}
		<div class="empty"><div class="skeleton"></div></div>
	{:else if availability === 'unavailable'}
		<div class="empty">
			<p>Backtest results are unavailable on this installation.</p>
			<small>Start the full local stack to enable durable result inspection.</small>
		</div>
	{:else if availability === 'failed'}
		<div class="empty">
			<p>Backtest results could not be loaded.</p>
			<small>Refresh this page after the API reports healthy.</small>
		</div>
	{:else if entries.length === 0}
		<div class="empty">
			{#if (bound?.offset ?? 0) > 0}
				<p>No further results at offset {bound?.offset}.</p>
				<small>Newer results remain on the previous page.</small>
			{:else}
				<p>No backtest results are saved yet.</p>
				<small>Run a backtest from a strategy's Test stage to inspect its saved result here.</small>
			{/if}
		</div>
	{:else}
		<div class="table-wrap">
			<table>
				<thead
					><tr
						><th>Strategy</th><th>Return</th><th>Final equity</th><th>Trades</th><th>Win rate</th
						><th>Max drawdown</th><th>Saved</th></tr
					></thead
				>
				<tbody>
					{#each entries as entry (entry.result_fingerprint)}
						{@const spreadCue = formatListSpreadCue(entry.summary.total_spread_cost)}
						<tr
							><td
								><button
									type="button"
									onclick={() => onSelect(entry.result_fingerprint)}
									aria-label={`Inspect ${shortFingerprint(entry.result_fingerprint)}`}
									><strong>{shortFingerprint(entry.strategy_fingerprint)}</strong><small
										>{shortFingerprint(entry.result_fingerprint)}</small
									></button
								></td
							><td
								class:gain={compareDecimalStrings(entry.summary.total_return_fraction, '0') > 0}
								class:loss={compareDecimalStrings(entry.summary.total_return_fraction, '0') < 0}
								>{formatPercent(entry.summary.total_return_fraction)}{#if spreadCue}<small
										data-testid="backtest-list-spread">{spreadCue}</small
									>{/if}</td
							><td>{formatUsd(entry.summary.final_equity)}</td><td>{entry.summary.trade_count}</td
							><td>{formatPercent(entry.summary.win_rate)}</td><td class="loss"
								>{formatPercent(entry.summary.maximum_drawdown_fraction)}</td
							><td data-testid="backtest-list-published"
								>{formatUtcTimestamp(entry.published_at)}</td
							></tr
						>
					{/each}
				</tbody>
			</table>
		</div>
		{#if canOlder}
			<p class="bound-note" data-testid="backtest-list-truncated">
				This page is full ({bound?.limit} newest-first). Older results may exist.
			</p>
		{/if}
		{#if canOlder || canNewer}
			<div class="pager" data-testid="backtest-list-pager">
				<button type="button" onclick={onNewer} disabled={!canNewer}>Newer</button>
				<button type="button" onclick={onOlder} disabled={!canOlder}>Older</button>
			</div>
		{/if}
	{/if}
</section>

<style>
	.backtest-panel {
		border: 1px solid var(--line);
		border-radius: 13px;
		overflow: hidden;
		background: var(--surface);
	}
	.panel-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 22px 24px;
		border-bottom: 1px solid var(--line);
	}
	h2 {
		margin: 0;
		font-size: 18px;
	}
	.panel-heading p,
	.page-controls > span,
	.empty small {
		margin: 5px 0 0;
		color: var(--faint);
		font-size: 12px;
	}
	.page-controls {
		display: flex;
		align-items: center;
		gap: 18px;
		color: var(--muted);
		font-size: 12px;
	}
	.page-controls select {
		margin-left: 8px;
		padding: 6px;
		color: var(--text);
		background: var(--surface-2);
		border: 1px solid var(--line-2);
		border-radius: 6px;
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
	td button {
		border: 0;
		padding: 0;
		background: transparent;
		color: var(--code);
		text-align: left;
		cursor: pointer;
		font: inherit;
	}
	td button:hover {
		color: var(--text);
	}
	td small {
		display: block;
		margin-top: 4px;
		color: var(--faint);
	}
	.gain {
		color: var(--accent);
	}
	.loss {
		color: var(--neg);
	}
	.empty {
		padding: 35px 24px;
		text-align: center;
	}
	.empty p {
		margin: 0 0 6px;
		color: var(--muted);
		font-size: 14px;
	}
	.bound-note,
	.pager {
		padding: 12px 18px;
		color: var(--faint);
		font-size: 12px;
	}
	.bound-note {
		margin: 0;
		border-top: 1px solid var(--line);
	}
	.pager {
		display: flex;
		justify-content: flex-end;
		gap: 10px;
		border-top: 1px solid var(--line);
	}
	.pager button {
		color: var(--text);
		background: var(--surface-2);
		border: 1px solid var(--line-2);
		border-radius: 8px;
		padding: 8px 12px;
		cursor: pointer;
	}
	.pager button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.skeleton {
		height: 55px;
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
</style>
