<script lang="ts">
	/**
	 * Indicators: one row per decision-clock indicator plus add/remove.
	 * Removing the ATR the initial stop references re-points the stop at
	 * another ATR (or none), because the stop must always name a live ATR.
	 */
	import type { BuilderModel, IndicatorDraft } from '$lib/strategies';
	import IndicatorFields from './IndicatorFields.svelte';

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

	function addIndicator(): void {
		if (!model) return;
		const next: IndicatorDraft = {
			id: `indicator_${model.indicators.length + 1}`,
			kind: 'sma',
			input: 'close',
			timeframe: '',
			parameters: { period: 50 }
		};
		model.indicators.push(next);
		markDirty();
	}

	function removeIndicator(index: number): void {
		if (!model) return;
		const removedId = model.indicators[index]?.id;
		model.indicators.splice(index, 1);
		// The initial stop must always reference a live ATR indicator.
		if (removedId !== undefined && model.exits.initial_stop.atr_indicator === removedId) {
			const replacement = model.indicators.find((candidate) => candidate.kind === 'atr');
			model.exits.initial_stop.atr_indicator = replacement?.id ?? '';
		}
		markDirty();
	}
</script>

<section class="panel">
	<h2>Indicators</h2>
	{#each model.indicators, index (index)}
		<div class="indicator-row" data-testid="indicator-row">
			<IndicatorFields
				bind:indicator={model.indicators[index]}
				{model}
				allowTimeframe={true}
				idPrefix={`ltf-${index}`}
				{readonly}
				{onchange}
			/>
			<button class="secondary" type="button" onclick={() => removeIndicator(index)}>Remove</button>
		</div>
	{/each}
	<button class="secondary" type="button" onclick={addIndicator}>Add indicator</button>
	<div class="hint">
		Pick a kind to see its parameters, defaults, and one-line help. Multi-series kinds (MACD,
		Bollinger, Supertrend, Ichimoku, …) expose each output as its own operand in conditions. Offset
		reads the value from that many completed bars earlier on the indicator's own clock (offset 1 on
		a 20-bar Donchian is the prior 20-bar high) and adds to the warmup. An optional timeframe uses a
		coarser integer-multiple venue clock; constants take neither. Stop ATRs stay on the decision
		clock.
	</div>
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
	/* The form's read-only fieldset (`DefinitionForm` `readonly`). */
	:global(fieldset:disabled) .secondary {
		display: none;
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
</style>
