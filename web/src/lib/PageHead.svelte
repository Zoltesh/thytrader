<script lang="ts">
	/**
	 * Compact workstation page header (ADR 0079 shell).
	 *
	 * Operate-tool posture: the page title orients, it does not perform. The
	 * huge marketing hero stays on this design's discard pile; safety copy
	 * lives next to the actions it describes. Use `lede` for plain copy or the
	 * `intro` snippet when the lede carries links or code.
	 */
	import type { Snippet } from 'svelte';

	let {
		eyebrow,
		title,
		lede = '',
		intro,
		children
	}: {
		eyebrow?: string;
		title: string;
		lede?: string;
		/** Rich lede content (links, code); rendered after `lede`. */
		intro?: Snippet;
		/** Trailing slot for status chips and primary actions. */
		children?: Snippet;
	} = $props();
</script>

<section class="page-head">
	<div class="page-head-text">
		{#if eyebrow}<p class="eyebrow">{eyebrow}</p>{/if}
		<h1>{title}</h1>
		{#if lede}<p class="lede">{lede}</p>{/if}
		{#if intro}{@render intro()}{/if}
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
		align-items: flex-end;
		gap: var(--space-3);
		margin-bottom: var(--space-5);
	}
	.page-head-text {
		min-width: 0;
	}
	.page-head-side {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		flex-shrink: 0;
	}
	@media (max-width: 720px) {
		.page-head {
			flex-direction: column;
			align-items: flex-start;
		}
	}
</style>
