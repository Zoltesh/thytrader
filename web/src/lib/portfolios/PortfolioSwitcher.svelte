<script lang="ts">
	/** The portfolio switcher: one button per portfolio with its mode chip; the selected one is pressed. */
	import { modeLabel, type Portfolio } from '$lib/portfolios';

	let {
		portfolios,
		selectedId,
		onselect
	}: {
		portfolios: Portfolio[];
		/** The shown portfolio's id, or undefined when none is shown. */
		selectedId: string | undefined;
		onselect: (portfolioId: string) => void;
	} = $props();
</script>

<div class="switcher" role="group" aria-label="Choose a portfolio" data-testid="portfolio-switcher">
	{#each portfolios as item (item.portfolio_id)}
		<button
			type="button"
			class="btn switch"
			class:on={item.portfolio_id === selectedId}
			aria-pressed={item.portfolio_id === selectedId}
			title={item.name}
			data-testid="portfolio-switch"
			onclick={() => onselect(item.portfolio_id)}
		>
			<span class="chip" class:live={item.mode === 'live'} class:paper={item.mode === 'paper'}
				>{modeLabel(item.mode)}</span
			>
			<span class="switch-name">{item.name}</span>
		</button>
	{/each}
</div>

<style>
	.switcher {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin: calc(-1 * var(--space-3)) 0 var(--space-4);
		max-width: 100%;
	}
	.switch {
		gap: 8px;
		max-width: min(320px, 100%);
		min-width: 0;
	}
	.switch-name {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>
