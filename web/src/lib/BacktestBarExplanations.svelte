<script lang="ts">
	import {
		fetchBarExplanations,
		indicatorText,
		isExitFill,
		outcomeLabel
	} from '$lib/barExplanations';
	import type { BacktestBarExplanationPage } from '$lib/barExplanations';
	import { formatUtcTimestamp } from '$lib/time';

	let { resultFingerprint }: { resultFingerprint: string } = $props();
	let pageData = $state<BacktestBarExplanationPage | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);
	let cursor = $state<string | null>(null);

	async function load(nextCursor: string | null): Promise<void> {
		loading = true;
		error = null;
		try {
			pageData = await fetchBarExplanations(resultFingerprint, { limit: 50, cursor: nextCursor });
			cursor = nextCursor;
		} catch (caught) {
			pageData = null;
			error = caught instanceof Error ? caught.message : 'Bar explanations are unavailable.';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const fingerprint = resultFingerprint;
		cursor = null;
		void load(null);
		return () => {
			void fingerprint;
		};
	});
</script>

<section class="bars" data-testid="bar-explanations" aria-label="Per-bar explanations">
	<div class="head">
		<h3>Bar explanations</h3>
		<p>
			Verified signal trace joined to this result's own fills. The evaluation-end liquidation bar is
			listed separately because the strategy never saw it.
		</p>
	</div>
	{#if loading}<p>Loading explanations…</p>
	{:else if error}<p data-testid="bar-explanations-error">{error}</p>
	{:else if pageData}
		<p class="provenance" data-testid="bar-explanation-provenance">
			{pageData.product_id} · {pageData.timeframe} · {pageData.returned} of {pageData.total_bars} bars
			· result {pageData.result_fingerprint.slice(0, 18)}…
		</p>
		{#if pageData.outside_trace.length > 0}
			<ul data-testid="outside-trace">
				{#each pageData.outside_trace as item (`${item.candle_starts_at}-${item.kind}`)}
					<li>
						{item.kind} outside the signal trace at {formatUtcTimestamp(item.candle_starts_at)}
						{#if isExitFill(item.fill)}
							· {item.fill.reason} · net {item.fill.net_pnl} · fee {item.fill.fee}
						{:else}
							· fee {item.fill.fee}
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
		<table>
			<thead>
				<tr>
					<th>Bar</th>
					<th>Entry</th>
					<th>Exit rule</th>
					<th>Values</th>
					<th>Fills</th>
				</tr>
			</thead>
			<tbody>
				{#each pageData.records as record (record.candle_starts_at)}
					<tr data-testid="bar-explanation">
						<td>{formatUtcTimestamp(record.candle_starts_at)}</td>
						<td>{outcomeLabel(record.entry_condition)}</td>
						<td>{outcomeLabel(record.exit_condition)}</td>
						<td>{indicatorText(record.indicator_values)}</td>
						<td>
							{#if record.entries.length === 0 && record.exits.length === 0}—{:else}
								{record.entries.length} entry / {record.exits.length} exit
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
		{#if pageData.next_cursor}
			<button type="button" onclick={() => void load(pageData?.next_cursor ?? null)}>
				Older bars
			</button>
		{/if}
		{#if cursor}
			<button type="button" onclick={() => void load(null)}>Newest bars</button>
		{/if}
	{/if}
</section>

<style>
	.bars {
		display: grid;
		gap: 10px;
		padding: 16px 18px;
		border: 1px solid var(--line, #ddd);
	}
	.head h3 {
		margin: 0;
	}
	.head p,
	.provenance {
		color: var(--muted, #667);
		margin: 0;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}
	th,
	td {
		padding: 6px 8px;
		border-bottom: 1px solid var(--line, #ddd);
		text-align: left;
		vertical-align: top;
	}
	button {
		justify-self: start;
	}
</style>
