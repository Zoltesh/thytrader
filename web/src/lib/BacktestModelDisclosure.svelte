<script lang="ts">
	/**
	 * "How backtests simulate": the one backtest model's fill and cost
	 * assumptions (ADR 0083) as a collapsible disclosure. Static copy; it does
	 * not fetch and offers no model choice because there is only one model.
	 */
	import { BACKTEST_MODEL_HONESTY, backtestModelAssumptions } from '$lib/backtest-model';

	let {
		maxEntryWaitBars = null,
		open = false
	}: {
		/** The strategy's entry wait, substituted into the unfilled-entry assumption. */
		maxEntryWaitBars?: number | null;
		open?: boolean;
	} = $props();

	const assumptions = $derived(backtestModelAssumptions(maxEntryWaitBars));
</script>

<details class="model" data-testid="backtest-model-disclosure" {open}>
	<summary>How backtests simulate</summary>
	<p class="honesty">{BACKTEST_MODEL_HONESTY}</p>
	<dl>
		{#each assumptions as assumption (assumption.key)}
			<div data-testid="backtest-model-assumption">
				<dt>{assumption.label}</dt>
				<dd>{assumption.detail}</dd>
			</div>
		{/each}
	</dl>
</details>

<style>
	.model {
		min-width: 0;
		font-size: var(--fs-sm);
	}
	summary {
		cursor: pointer;
		color: var(--muted);
	}
	.honesty {
		margin: 8px 0 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
		line-height: 1.45;
	}
	dl {
		display: grid;
		gap: 8px;
		margin: 0;
	}
	dt {
		color: var(--text);
		font-weight: 550;
		font-size: 12px;
	}
	dd {
		margin: 2px 0 0;
		color: var(--faint);
		font-size: 11px;
		line-height: 1.4;
	}
</style>
