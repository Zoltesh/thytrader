<script lang="ts">
	/** Per-strategy results of the last confirmed deletion, including partial failures. */
	import { bulkOutcomeText, type BulkDeleteItem } from '$lib/strategies';
	import { deleteResultsHeadline } from './library';

	let {
		results,
		nameOf,
		ondismiss
	}: {
		results: BulkDeleteItem[];
		/** Display name for a result the server did not name. */
		nameOf: (strategyId: string) => string;
		ondismiss: () => void;
	} = $props();
</script>

<section
	class="card results"
	role="status"
	aria-label="Deletion results"
	data-testid="delete-results"
>
	<div class="results-head">
		<strong>{deleteResultsHeadline(results)}</strong>
		<button class="btn ghost small" type="button" onclick={() => ondismiss()}>Dismiss</button>
	</div>
	<ul>
		{#each results as item (item.strategy_id)}
			<li data-outcome={item.outcome}>
				<span class="result-name">{item.name ?? nameOf(item.strategy_id)}</span>
				<span class:neg={item.outcome !== 'deleted'}>{bulkOutcomeText(item)}</span>
			</li>
		{/each}
	</ul>
</section>

<style>
	.small {
		display: block;
		font-size: var(--fs-xs);
	}
	.btn.small {
		min-height: 28px;
		padding: 0 8px;
		font-size: var(--fs-sm);
	}
	.results {
		margin-bottom: var(--space-3);
		padding: 12px 16px;
	}
	.results-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	.results ul {
		display: grid;
		gap: 6px;
		margin: 8px 0 0;
		padding: 0;
		list-style: none;
	}
	.results li {
		display: grid;
		gap: 2px;
	}
	.result-name {
		font-weight: 500;
	}
	.neg {
		color: var(--neg);
	}
</style>
