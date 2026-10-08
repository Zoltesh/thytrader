<script lang="ts">
	/**
	 * Library view filters: Mine / Research / All with server counts (ADR 0098),
	 * the active tag filter chip (ADR 0094), and the match total of the view.
	 */
	import Segmented from '$lib/Segmented.svelte';
	import type { StrategyOrigin } from '$lib/strategies';

	let {
		originOptions,
		origin,
		onorigin,
		tagFilter,
		onclearTag,
		total,
		loading
	}: {
		originOptions: readonly { id: StrategyOrigin; label: string; count?: number }[];
		origin: StrategyOrigin;
		onorigin: (next: StrategyOrigin) => void;
		tagFilter: string | null;
		onclearTag: () => void;
		/** Matches in the current view, from the server's `total`. */
		total: number | null;
		loading: boolean;
	} = $props();
</script>

<div class="library-filters">
	<Segmented
		label="Whose strategies"
		options={originOptions}
		value={origin}
		onchange={onorigin}
		testId="library-origin"
	/>
	{#if tagFilter !== null}
		<div class="tag-filter" role="status" data-testid="library-tag-filter">
			<span>Tagged</span>
			<button
				class="tag-chip active"
				type="button"
				aria-label="Clear the tag filter {tagFilter}"
				onclick={() => onclearTag()}>{tagFilter} ✕</button
			>
		</div>
	{/if}
	<span class="spacer"></span>
	{#if total !== null && !loading}
		<span class="faint view-count" data-testid="library-total"
			>{total} strateg{total === 1 ? 'y' : 'ies'}</span
		>
	{/if}
</div>

<style>
	.tag-chip {
		border: 1px solid var(--line-2);
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--muted);
		font-size: var(--fs-xs);
		padding: 1px 8px;
		cursor: pointer;
	}
	.tag-chip:hover,
	.tag-chip.active {
		border-color: var(--accent);
		color: var(--text);
		background: var(--accent-soft);
	}
	.library-filters {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3);
		margin-bottom: var(--space-3);
	}
	.library-filters .spacer {
		flex: 1;
	}
	.view-count {
		font-size: var(--fs-sm);
		font-variant-numeric: tabular-nums;
	}
	.tag-filter {
		display: flex;
		align-items: center;
		gap: 8px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.faint {
		color: var(--faint);
	}
</style>
