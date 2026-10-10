<script lang="ts">
	/**
	 * "Futures · USD · paper" badge for a paper futures book in bot lists (ADR 0129).
	 * Renders nothing for a spot product, so spot rows are unchanged.
	 */
	import { isFuturesProductId } from '$lib/product-id';

	let { productId }: { productId: string } = $props();
</script>

{#if isFuturesProductId(productId)}
	<span class="chip futures" data-testid="futures-badge" title="Paper futures book, amounts in USD"
		>Futures · USD · paper</span
	>
{/if}

<style>
	.futures {
		margin-top: 4px;
		border-color: var(--warn-line);
		color: var(--warn);
	}
</style>
