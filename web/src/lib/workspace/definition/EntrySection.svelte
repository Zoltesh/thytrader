<script lang="ts">
	/**
	 * Entry conditions: the ALL / ANY / NOT entry tree, the optional
	 * higher-timeframe filter (its clock, warmup, indicators, and own tree, AND-ed
	 * with entry on the last completed HTF bar), position side, and the declared
	 * re-entry cooldown.
	 */
	import { defaultHtfFilter, validHtfTimeframes, type BuilderModel } from '$lib/strategies';
	import IndicatorFields from './IndicatorFields.svelte';
	import RuleTree from './RuleTree.svelte';

	let {
		model = $bindable(),
		readonly,
		onchange
	}: {
		model: BuilderModel;
		readonly: boolean;
		/** Called after every edit. */
		onchange: () => void;
	} = $props();

	function markDirty(): void {
		onchange();
	}

	function addHtfIndicator(): void {
		if (!model?.htf_filter) return;
		model.htf_filter.indicators.push({
			id: `htf_indicator_${model.htf_filter.indicators.length + 1}`,
			kind: 'sma',
			input: 'close',
			parameters: { period: 50 }
		});
		markDirty();
	}

	function removeHtfIndicator(index: number): void {
		if (!model?.htf_filter) return;
		model.htf_filter.indicators.splice(index, 1);
		markDirty();
	}

	function toggleHtfFilter(enabled: boolean): void {
		if (!model) return;
		model.htf_filter = enabled ? defaultHtfFilter(model.timeframe) : null;
		markDirty();
	}
</script>

<section class="panel">
	<h2>Entry conditions</h2>
	<div class="rule-tree">
		{#if model.entry.when}
			<RuleTree
				bind:model
				root={model.entry.when}
				indicators={model.indicators}
				kind="entry"
				{onchange}
			/>
		{/if}
	</div>
	<label class="cooldown-row"
		><input
			type="checkbox"
			checked={model.htf_filter !== null}
			onchange={(event) => toggleHtfFilter((event.currentTarget as HTMLInputElement).checked)}
		/>
		Enable higher-timeframe filter (optional)
	</label>
	{#if model.htf_filter === null}
		<div class="hint">
			Off by default. A higher-timeframe filter AND-s entry with a coarser clock and needs a
			verified dataset for that clock too.
		</div>
	{/if}
	{#if model.htf_filter}
		<button
			class="secondary"
			type="button"
			data-testid="remove-htf-filter"
			onclick={() => toggleHtfFilter(false)}
			>Remove higher-timeframe filter ({model.htf_filter.timeframe})</button
		>
		<div class="grid-two">
			<label
				>HTF timeframe
				<select bind:value={model.htf_filter.timeframe} onchange={markDirty}>
					{#each validHtfTimeframes(model.timeframe) as timeframe (timeframe)}
						<option value={timeframe}>{timeframe}</option>
					{/each}
				</select></label
			>
			<label
				>HTF warmup bars
				<input
					type="number"
					min="1"
					bind:value={model.htf_filter.warmup_bars}
					oninput={markDirty}
				/></label
			>
		</div>
		{#each model.htf_filter.indicators, index (index)}
			<div class="indicator-row" data-testid="htf-indicator-row">
				<IndicatorFields
					bind:indicator={model.htf_filter.indicators[index]}
					{model}
					allowTimeframe={false}
					idPrefix={`htf-${index}`}
					{readonly}
					{onchange}
				/>
				<button class="secondary" type="button" onclick={() => removeHtfIndicator(index)}
					>Remove</button
				>
			</div>
		{/each}
		<button class="secondary" type="button" onclick={addHtfIndicator}>Add HTF indicator</button>
		<div class="rule-tree">
			<RuleTree
				bind:model
				root={model.htf_filter.when}
				indicators={model.htf_filter.indicators}
				kind="htf"
				{onchange}
			/>
		</div>
		<div class="hint">
			The HTF <code>when</code> tree is AND-ed with LTF entry using the last completed HTF bar. Paper
			and live evaluate this block on live complete-only HTF candles.
		</div>
	{/if}
	<label class="cooldown-row"
		>Position side
		<select bind:value={model.side} onchange={markDirty}>
			<option value="long">Long</option>
			<option value="short">Short (spot; live needs available base)</option>
		</select></label
	>
	<label class="cooldown-row"
		>Re-entry cooldown (bars, declared — not yet modeled by the backtester)
		<input type="number" min="0" bind:value={model.cooldown_bars} oninput={markDirty} /></label
	>
</section>

<style>
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
	select {
		width: 100%;
		min-height: 34px;
		padding: 7px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	/* The form's read-only fieldset (`DefinitionForm` `readonly`). */
	:global(fieldset:disabled) input:not([type='checkbox']),
	:global(fieldset:disabled) select {
		opacity: 1;
		color: var(--text);
		cursor: default;
	}
	:global(fieldset:disabled) .secondary {
		display: none;
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
	.indicator-row {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 10px;
		align-items: start;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.indicator-row > .secondary {
		align-self: end;
		justify-self: start;
	}
	.secondary {
		min-height: 30px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--muted);
		font: inherit;
		font-size: var(--fs-sm);
		cursor: pointer;
	}
	.secondary:hover {
		background: var(--hover);
		color: var(--text);
	}
	.rule-tree {
		display: grid;
		gap: 8px;
	}
	.cooldown-row {
		margin-top: 6px;
	}
	.cooldown-row:has(> input[type='checkbox']) {
		display: flex;
		align-items: center;
		gap: 8px;
		color: var(--text);
	}
	@media (max-width: 640px) {
		.grid-two {
			grid-template-columns: 1fr;
		}
	}
</style>
