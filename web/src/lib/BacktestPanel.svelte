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
		onSelect,
		onOlder,
		onNewer
	}: {
		entries?: BacktestSummaryEntry[];
		bound?: BacktestList | null;
		loading?: boolean;
		availability?: 'ready' | 'unavailable' | 'failed';
		onSelect: (fingerprint: string) => void;
		onOlder: () => void;
		onNewer: () => void;
	} = $props();

	const boundLabel = $derived(bound === null ? null : formatBacktestListBound(bound));
	const pageFull = $derived(bound !== null && backtestListPageIsFull(bound));
	const canNewer = $derived((bound?.offset ?? 0) > 0);
	const canOlder = $derived(pageFull);
</script>

<section class="backtest-panel" aria-label="Published backtests">
	<div class="panel-heading">
		<div>
			<h2>Published backtests</h2>
			<p>Immutable historical simulations · not evidence of future profit</p>
		</div>
		{#if boundLabel !== null}
			<span data-testid="backtest-list-bound">{boundLabel}</span>
		{/if}
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
				<small>Newer immutable results remain on the previous page.</small>
			{:else}
				<p>No backtest results are published yet.</p>
				<small>Run a backtest from the CLI to inspect its immutable result here.</small>
			{/if}
		</div>
	{:else}
		<div class="table-wrap">
			<table>
				<thead
					><tr
						><th>Strategy</th><th>Engine</th><th>Return</th><th>Final equity</th><th>Trades</th><th
							>Win rate</th
						><th>Max drawdown</th><th>Published</th></tr
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
							><td class="engine"
								><span data-testid="backtest-list-engine">{entry.engine_contract_version}</span
								>{#if spreadCue}<small data-testid="backtest-list-spread">{spreadCue}</small
									>{/if}</td
							><td
								class:gain={compareDecimalStrings(entry.summary.total_return_fraction, '0') > 0}
								class:loss={compareDecimalStrings(entry.summary.total_return_fraction, '0') < 0}
								>{formatPercent(entry.summary.total_return_fraction)}</td
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
		{#if pageFull}
			<p class="bound-note" data-testid="backtest-list-truncated">
				This page is full ({bound?.limit} newest-first). Older immutable results may exist.
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
		border: 1px solid #232b2d;
		border-radius: 13px;
		overflow: hidden;
		background: linear-gradient(145deg, rgba(20, 26, 28, 0.95), rgba(12, 16, 18, 0.95));
	}
	.panel-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 22px 24px;
		border-bottom: 1px solid #232b2d;
	}
	h2 {
		margin: 0;
		font-size: 18px;
	}
	.panel-heading p,
	.panel-heading > span,
	.empty small {
		margin: 5px 0 0;
		color: #778386;
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
	td:first-child,
	th:nth-child(2),
	td.engine {
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
	td.engine {
		white-space: normal;
		max-width: 220px;
	}
	td button {
		border: 0;
		padding: 0;
		background: transparent;
		color: #b8e8d8;
		text-align: left;
		cursor: pointer;
		font: inherit;
	}
	td button:hover {
		color: #fff;
	}
	td button small,
	td.engine small {
		display: block;
		margin-top: 4px;
		color: #657174;
	}
	.gain {
		color: #5ce1b5;
	}
	.loss {
		color: #ed8b8b;
	}
	.empty {
		padding: 35px 24px;
		text-align: center;
	}
	.empty p {
		margin: 0 0 6px;
		color: #aeb9bb;
		font-size: 14px;
	}
	.bound-note,
	.pager {
		padding: 12px 18px;
		color: #778386;
		font-size: 12px;
	}
	.bound-note {
		margin: 0;
		border-top: 1px solid #1d2426;
	}
	.pager {
		display: flex;
		justify-content: flex-end;
		gap: 10px;
		border-top: 1px solid #1d2426;
	}
	.pager button {
		color: #dce4e5;
		background: #151b1d;
		border: 1px solid #303a3c;
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
		background: linear-gradient(90deg, #151c1e, #20292b, #151c1e);
		background-size: 200%;
		animation: shimmer 1.4s infinite;
	}
	@keyframes shimmer {
		to {
			background-position: -200% 0;
		}
	}
</style>
