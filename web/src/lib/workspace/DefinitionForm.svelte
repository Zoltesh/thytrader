<script lang="ts">
	/**
	 * Strategy definition form (the Build stage's left column).
	 *
	 * Section navigation plus the rule rows: indicators, the ALL / ANY / NOT
	 * entry tree, the optional higher-timeframe filter, exits (with the optional
	 * "Exit when" rule tree, ADR 0093), sizing, limits, and execution preferences. `readonly` renders a strategy
	 * snapshot's definition in the same layout with every control disabled. Market and
	 * data also declares read-only reference instruments (ADR 0096) that indicators may
	 * pick as their instrument, so a rule can gate on another market (BTC · EMA(100)).
	 *
	 * The larger sections live in `./definition/` and edit the same bound model;
	 * the short ones (overview, sizing, limits, execution) stay inline here.
	 */
	import { untrack } from 'svelte';
	import { quoteLabelFor, type BuilderModel } from '$lib/strategies';
	import type { BuildSection } from '$lib/strategy-workspace';
	import EntrySection from './definition/EntrySection.svelte';
	import ExitsSection from './definition/ExitsSection.svelte';
	import IndicatorsSection from './definition/IndicatorsSection.svelte';
	import MarketSection from './definition/MarketSection.svelte';

	let {
		model = $bindable(),
		readonly = false,
		initialSection = null,
		onchange
	}: {
		model: BuilderModel;
		readonly?: boolean;
		/** Section to open first (for example from a Test-stage "change this clock" link). */
		initialSection?: BuildSection | null;
		/** Called after every edit (marks dirty and re-validates in the parent). */
		onchange: () => void;
	} = $props();

	let activeSection = $state<string>(untrack(() => initialSection) ?? 'overview');

	const sections = [
		{ id: 'overview', label: 'Overview' },
		{ id: 'market', label: 'Market and data' },
		{ id: 'indicators', label: 'Indicators' },
		{ id: 'entry', label: 'Entry conditions' },
		{ id: 'exits', label: 'Exit conditions and protective stops' },
		{ id: 'sizing', label: 'Position sizing' },
		{ id: 'limits', label: 'Portfolio limits' },
		{ id: 'execution', label: 'Execution preferences' }
	];

	function markDirty(): void {
		onchange();
	}

	/** Last reward/risk multiple, restored when the operator switches back from `none`. */
	let lastTakeProfitMultiple = $state('2');

	const quote = $derived(quoteLabelFor(model.product_id));
</script>

<div class="definition-wrap">
	<nav class="section-nav" aria-label="Builder sections">
		{#each sections as section (section.id)}
			<button
				class="section-tab"
				class:active={activeSection === section.id}
				type="button"
				aria-pressed={activeSection === section.id}
				onclick={() => (activeSection = section.id)}>{section.label}</button
			>
		{/each}
	</nav>
	<fieldset class="definition" disabled={readonly}>
		<legend class="sr-only"
			>{readonly ? 'Strategy definition (read-only)' : 'Strategy definition'}</legend
		>
		{#if activeSection === 'overview'}
			<section class="panel">
				<h2>Overview</h2>
				<label>Strategy name<input bind:value={model.name} oninput={markDirty} /></label>
				<label
					>Thesis / description
					<textarea
						bind:value={model.description}
						rows={3}
						oninput={markDirty}
						placeholder="What market behavior does this capture, and when should it not trade?"
					></textarea></label
				>
				<div class="hint">
					Saving updates this strategy in place. Backtests and bots keep the exact rules they
					started with.
				</div>
			</section>
		{:else if activeSection === 'market'}
			<MarketSection bind:model {onchange} />
		{:else if activeSection === 'indicators'}
			<IndicatorsSection bind:model {readonly} {onchange} />
		{:else if activeSection === 'entry'}
			<EntrySection bind:model {readonly} {onchange} />
		{:else if activeSection === 'exits'}
			<ExitsSection bind:model bind:lastTakeProfitMultiple {readonly} {onchange} />
		{:else if activeSection === 'sizing'}
			<section class="panel">
				<h2>Position sizing</h2>
				<label
					>Risk fraction of equity per trade
					<input
						inputmode="decimal"
						bind:value={model.sizing.risk_fraction}
						oninput={markDirty}
					/></label
				>
				<div class="grid-two">
					<label
						>Minimum {quote} notional
						<input
							inputmode="decimal"
							bind:value={model.sizing.min_quote_notional}
							oninput={markDirty}
						/></label
					>
					<label
						>Maximum {quote} notional
						<input
							inputmode="decimal"
							bind:value={model.sizing.max_quote_notional}
							oninput={markDirty}
						/></label
					>
				</div>
				<div class="hint">
					Order notional bounds in the product's quote currency ({quote}), shared by every covered
					product.
				</div>
			</section>
		{:else if activeSection === 'limits'}
			<section class="panel">
				<h2>Portfolio limits</h2>
				<label
					>Max strategy exposure (fraction of equity)
					<input
						inputmode="decimal"
						bind:value={model.portfolio_limits.max_strategy_exposure_fraction}
						oninput={markDirty}
					/></label
				>
				<div class="hint">One concurrent position per strategy.</div>
			</section>
		{:else if activeSection === 'execution'}
			<section class="panel">
				<h2>Execution preferences</h2>
				<label
					>Entry preference
					<select bind:value={model.execution.entry_preference} onchange={markDirty}>
						<option value="maker_only">Maker only</option>
					</select></label
				>
				<div class="hint">Entries are always post-only maker limit orders.</div>
				<div class="grid-two">
					<label
						>Max entry wait (bars)
						<input
							type="number"
							min="1"
							bind:value={model.execution.max_entry_wait_bars}
							oninput={markDirty}
						/></label
					>
					<label
						>On unfilled entry
						<select bind:value={model.execution.on_unfilled_entry} onchange={markDirty}>
							<option value="cancel">Cancel</option>
							<option value="reprice">Reprice</option>
						</select></label
					>
				</div>
				<div class="warn">
					The current backtester fills every entry at the next bar open. These preferences are
					declared for future runtimes and are shown as unsupported in the inspector.
				</div>
			</section>
		{/if}
	</fieldset>
</div>

<style>
	.definition-wrap {
		display: grid;
		gap: 12px;
		min-width: 0;
	}
	.section-nav {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.section-tab {
		min-height: 32px;
		padding: 0 12px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--muted);
		font-size: var(--fs-sm);
		cursor: pointer;
	}
	.section-tab:hover {
		color: var(--text);
	}
	.section-tab.active {
		border-color: var(--accent-line);
		background: var(--accent-soft);
		color: var(--accent);
	}
	.definition {
		min-width: 0;
		margin: 0;
		padding: 0;
		border: 0;
	}
	.panel {
		display: grid;
		gap: 14px;
		padding: 18px 20px;
		border: 1px solid var(--line);
		border-radius: var(--radius-lg);
		background: var(--surface);
	}
	.panel h2 {
		color: var(--faint);
		font-size: var(--fs-sm);
		font-weight: 500;
		letter-spacing: 0.05em;
		text-transform: uppercase;
	}
	label {
		display: grid;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	input:not([type='checkbox']):not([type='radio']),
	select,
	textarea {
		width: 100%;
		min-height: 34px;
		padding: 7px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	fieldset:disabled input:not([type='checkbox']),
	fieldset:disabled select,
	fieldset:disabled textarea {
		opacity: 1;
		color: var(--text);
		cursor: default;
	}
	textarea {
		resize: vertical;
	}
	.grid-two {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 14px;
	}
	.hint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.warn {
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	@media (max-width: 640px) {
		.grid-two {
			grid-template-columns: 1fr;
		}
	}
</style>
