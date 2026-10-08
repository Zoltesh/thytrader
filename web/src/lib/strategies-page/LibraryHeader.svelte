<script lang="ts">
	/**
	 * Strategy library heading and actions: Import JSON…, and New strategy from
	 * a template on a market and clock. The draft choices are bound to the page,
	 * which creates the strategy and remembers the market.
	 */
	import { EXECUTION_TIMEFRAMES, STRATEGY_TEMPLATE_OPTIONS } from '$lib/strategies';

	let {
		draftTemplate = $bindable(),
		draftProduct = $bindable(),
		draftTimeframe = $bindable(),
		pendingAction,
		onimport,
		oncreate
	}: {
		draftTemplate: string;
		draftProduct: string;
		draftTimeframe: string;
		/** A create, clone, or import in flight disables the draft controls. */
		pendingAction: string | null;
		onimport: () => void;
		oncreate: () => void;
	} = $props();
</script>

<div class="library-head">
	<div>
		<h1>Strategies</h1>
		<p class="lede">
			Every strategy takes one path: build, test, run on paper, run live, then review why each trade
			happened. Open a strategy to work on it.
		</p>
	</div>
	<section id="library-actions" class="top-actions" aria-label="Library actions">
		<button class="btn" type="button" onclick={() => onimport()}>Import JSON…</button>
		<label class="template-picker"
			>Template
			<select bind:value={draftTemplate} disabled={pendingAction !== null}>
				{#each STRATEGY_TEMPLATE_OPTIONS as template (template.id)}
					<option value={template.id} title={template.description}>{template.name}</option>
				{/each}
			</select></label
		>
		<label class="template-picker"
			>Market
			<input
				class="product-input"
				bind:value={draftProduct}
				spellcheck="false"
				autocomplete="off"
				placeholder="BTC-USDC"
				disabled={pendingAction !== null}
			/></label
		>
		<label class="template-picker"
			>Clock
			<select bind:value={draftTimeframe} disabled={pendingAction !== null}>
				{#each EXECUTION_TIMEFRAMES as clock (clock)}
					<option value={clock}>{clock}</option>
				{/each}
			</select></label
		>
		<button
			class="btn primary"
			type="button"
			onclick={() => oncreate()}
			disabled={pendingAction !== null}
			>{pendingAction === 'create' ? 'Creating…' : 'New strategy'}</button
		>
	</section>
</div>

<style>
	.library-head {
		display: flex;
		align-items: flex-end;
		flex-wrap: wrap;
		gap: 12px;
		margin-bottom: var(--space-5);
	}
	.library-head h1 {
		margin: 0;
	}
	.top-actions {
		display: flex;
		align-items: flex-end;
		flex-wrap: wrap;
		gap: 8px;
		margin-left: auto;
	}
	.template-picker {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.template-picker select {
		min-height: 34px;
		padding: 0 8px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
	}
	.product-input {
		width: 9rem;
		text-transform: uppercase;
	}
</style>
