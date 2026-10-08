<script lang="ts">
	/**
	 * The library card when the current view has no rows: a failed load, an
	 * empty tag filter, or an empty Mine / Research / All view (Mine points at
	 * Research so agent research never makes the library look empty).
	 */
	import type { StrategyOrigin } from '$lib/strategies';

	let {
		failed,
		origin,
		tagFilter,
		onresearch
	}: {
		/** The library load failed (the page shows the error banner). */
		failed: boolean;
		origin: StrategyOrigin;
		tagFilter: string | null;
		/** Switch the library to the Research view. */
		onresearch: () => void;
	} = $props();
</script>

{#if failed}
	<div class="library-empty"><p>Could not load strategies. Retry the library load.</p></div>
{:else if tagFilter !== null}
	<div class="library-empty">
		<p>
			No {origin === 'operator'
				? 'strategies of yours'
				: origin === 'research'
					? 'research strategies'
					: 'strategies'} are tagged {tagFilter}.
		</p>
	</div>
{:else if origin === 'operator'}
	<div class="library-empty" data-testid="library-empty-mine">
		<p>No strategies yet.</p>
		<p class="empty-hint">
			Strategies that agent research created (tagged <code>claude-research</code> or
			<code>research-*</code>) are under
			<button class="link-btn" type="button" onclick={() => onresearch()}>Research</button>. Use
			<strong>New strategy</strong> above to create your own, or import a strategy definition.
		</p>
	</div>
{:else if origin === 'research'}
	<div class="library-empty">
		<p>No research strategies.</p>
		<p class="empty-hint">
			Strategies tagged <code>claude-research</code> or <code>research-*</code> show here.
		</p>
	</div>
{:else}
	<div class="library-empty">
		<p>No strategies yet.</p>
		<p class="empty-hint">
			Use <strong>New strategy</strong> above to create a conservative reference strategy, or import a
			strategy definition you exported elsewhere.
		</p>
	</div>
{/if}

<style>
	.link-btn {
		padding: 0;
		border: 0;
		background: none;
		color: var(--accent);
		font: inherit;
		cursor: pointer;
	}
	.link-btn:hover {
		text-decoration: underline;
	}
	.library-empty {
		padding: 42px 24px;
		text-align: center;
	}
	.library-empty p {
		margin: 0 0 8px;
	}
	.empty-hint {
		color: var(--muted);
	}
</style>
