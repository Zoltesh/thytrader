<script lang="ts">
	/**
	 * Modeled assumptions of a backtest result: the published costs, the spread
	 * stress note on spread-stressed runs, modeling limits, and the model disclosure.
	 */
	import { formatValidityLimit } from '$lib/backtest-model';
	import { formatPublishedCosts, type BacktestDetail } from '$lib/backtests';
	import BacktestModelDisclosure from '$lib/BacktestModelDisclosure.svelte';

	let {
		costs,
		spreadCostNote,
		validityLimits
	}: {
		costs: BacktestDetail['costs'];
		/** Spread-stress copy; null on unstressed runs. */
		spreadCostNote: string | null;
		validityLimits: NonNullable<BacktestDetail['result']['summary']['validity_limits']>;
	} = $props();
</script>

<div class="assumptions" data-testid="modeled-assumptions">
	<strong>Modeled assumptions</strong>
	<span data-testid="published-costs">{formatPublishedCosts(costs)}</span>
	{#if spreadCostNote}<small data-testid="spread-stress">{spreadCostNote}</small>{/if}
	{#if validityLimits.length > 0}
		<ul class="limits" data-testid="validity-limits" aria-label="Modeling limits">
			{#each validityLimits as code (code)}
				<li>{formatValidityLimit(code)}</li>
			{/each}
		</ul>
	{/if}
	<BacktestModelDisclosure />
</div>

<style>
	small {
		color: var(--faint);
		font-size: 11px;
	}
	.assumptions {
		border: 1px solid var(--line);
		border-radius: 13px;
		background: var(--surface);
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
	.limits {
		margin: 0;
		padding-left: 16px;
		color: var(--faint);
		font-size: 11px;
	}
</style>
