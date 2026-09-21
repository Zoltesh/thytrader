<script lang="ts">
	/**
	 * Compact workstation page header.
	 *
	 * Operate-tool posture: the page title orients, it does not perform. The
	 * huge marketing hero stays on this design's discard pile; safety copy
	 * lives next to the actions it describes.
	 */
	import type { Snippet } from 'svelte';

	let {
		eyebrow,
		title,
		lede = '',
		children
	}: {
		eyebrow?: string;
		title: string;
		lede?: string;
		/** Trailing slot for status chips and primary actions. */
		children?: Snippet;
	} = $props();
</script>

<section class="page-head">
	<div class="page-head-text">
		{#if eyebrow}<p class="eyebrow">{eyebrow}</p>{/if}
		<h1>{title}</h1>
		{#if lede}<p class="lede">{lede}</p>{/if}
	</div>
	{#if children}
		<div class="page-head-side">
			{@render children()}
		</div>
	{/if}
</section>

<style>
	.page-head {
		display: flex;
		justify-content: space-between;
		align-items: end;
		gap: 18px;
		margin-bottom: 26px;
	}
	.page-head-text {
		min-width: 0;
	}
	.page-head-side {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-shrink: 0;
	}
</style>
