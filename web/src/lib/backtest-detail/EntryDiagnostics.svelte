<script lang="ts">
	/**
	 * "Why so few trades?": the entry funnel of a backtest result (ADR 0090) —
	 * unfilled entries, matched signals that rested no entry, warmup bars, and how
	 * positions closed. Open by default when the result has no trades.
	 */
	import {
		exitReasonLines,
		formatDiagnosticsFunnel,
		formatSkipReason,
		unfilledEntryLines,
		type BacktestDiagnostics
	} from '$lib/backtests';

	let {
		diagnostics,
		tradeCount
	}: {
		/** Null on results published before diagnostics were recorded. */
		diagnostics: BacktestDiagnostics | null;
		tradeCount: number;
	} = $props();
</script>

<details class="evidence diagnostics" data-testid="result-diagnostics" open={tradeCount === 0}>
	<summary>Why so few trades? <span class="faint">entry funnel</span></summary>
	<div class="diagnostics-body">
		{#if diagnostics === null}
			<p>
				Entry diagnostics were not recorded for this result (published before ADR 0090). Run the
				backtest again to record them.
			</p>
		{:else}
			<p data-testid="diagnostics-funnel">
				<strong>{formatDiagnosticsFunnel(diagnostics)}</strong>
			</p>
			{#if unfilledEntryLines(diagnostics).length > 0}
				<ul>
					{#each unfilledEntryLines(diagnostics) as line, index (index)}<li>{line}</li>{/each}
				</ul>
			{/if}
			{#if diagnostics.skipped.length > 0}
				<p class="faint">Matched signals that rested no entry:</p>
				<ul data-testid="diagnostics-skipped">
					{#each diagnostics.skipped as item (item.reason)}
						<li>
							<strong>{item.count}</strong> · {formatSkipReason(item.reason)}
							<code>{item.reason}</code>
						</li>
					{/each}
				</ul>
			{:else if diagnostics.signals_matched > 0}
				<p class="faint">Every matched signal rested an entry.</p>
			{/if}
			{#if diagnostics.warmup_bars > 0}
				<p class="faint">
					{diagnostics.warmup_bars} evaluation bars could not evaluate the rule yet (indicator warmup).
				</p>
			{/if}
			{#if exitReasonLines(diagnostics).length > 0}
				<p class="faint">How positions closed:</p>
				<ul data-testid="diagnostics-exits">
					{#each exitReasonLines(diagnostics) as line, index (index)}<li>{line}</li>{/each}
				</ul>
			{/if}
		{/if}
	</div>
</details>

<style>
	.faint {
		color: var(--faint);
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
	.diagnostics-body {
		padding: 4px 18px 14px;
		border-top: 1px solid var(--line);
	}
	.diagnostics-body ul {
		margin: 6px 0 10px;
		padding-left: 18px;
	}
	.diagnostics-body code {
		margin-left: 6px;
		font-size: var(--fs-sm);
		color: var(--faint);
	}
	p {
		margin: 0;
		color: var(--faint);
		font-size: 12px;
	}
	code {
		color: var(--code);
	}
</style>
