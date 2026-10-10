<script lang="ts">
	/**
	 * Market and data: the instrument kind (spot or a CFM futures contract, ADR 0128),
	 * the traded product and decision clock, warmup, and up to
	 * `MAX_REFERENCE_INSTRUMENTS` read-only reference instruments (ADR 0096)
	 * that indicators may read. Renaming a reference retargets every indicator
	 * that read it; removing one leaves those indicators for validation to name.
	 */
	import {
		builderQuoteLabel,
		defaultDerivatives,
		defaultReferenceInstrument,
		EXECUTION_TIMEFRAMES,
		MAX_REFERENCE_INSTRUMENTS,
		validReferenceTimeframes,
		type BuilderModel,
		type DerivativesDraft,
		type InstrumentKind
	} from '$lib/strategies';
	import FuturesFields from './FuturesFields.svelte';

	let {
		model = $bindable(),
		onchange
	}: {
		model: BuilderModel;
		/** Called after every edit. */
		onchange: () => void;
	} = $props();

	const quote = $derived(builderQuoteLabel(model));
	const futures = $derived(model.instrument_kind === 'future');

	/** The futures block set aside while the form shows spot, restored on switching back. */
	let setAsideDerivatives: DerivativesDraft | null = null;

	function markDirty(): void {
		onchange();
	}

	/**
	 * Edit the product id. On spot (`BASE-QUOTE`) the base currency follows the id; a
	 * futures contract's underlying is its own field (`BIP` settles on BTC, not BIP).
	 */
	function setProduct(raw: string): void {
		const productId = raw.trim().toUpperCase();
		model.product_id = productId;
		if (!futures) model.base_currency = productId.split('-')[0] ?? '';
		markDirty();
	}

	/**
	 * Switch between spot and a futures contract. Futures gain a derivatives block
	 * (unlevered by default); spot drops it, so the saved spot document carries no
	 * futures keys. Blocks a futures strategy cannot use stay for validation to name.
	 */
	function setKind(kind: InstrumentKind): void {
		if (kind === model.instrument_kind) return;
		model.instrument_kind = kind;
		if (kind === 'future') {
			model.derivatives = setAsideDerivatives ?? defaultDerivatives();
		} else {
			setAsideDerivatives = model.derivatives;
			model.derivatives = null;
			model.base_currency = model.product_id.split('-')[0] ?? '';
		}
		markDirty();
	}

	/** Declare one more read-only reference instrument (BTC in this quote on 1d by default). */
	function addReferenceInstrument(): void {
		if (!model || model.reference_instruments.length >= MAX_REFERENCE_INSTRUMENTS) return;
		model.reference_instruments.push(defaultReferenceInstrument(model));
		markDirty();
	}

	/**
	 * Drop one reference. Indicators that still read it keep their source, so validation
	 * names them instead of silently retargeting a rule at the traded instrument.
	 */
	function removeReferenceInstrument(index: number): void {
		if (!model) return;
		model.reference_instruments.splice(index, 1);
		markDirty();
	}

	/** Rename one reference id and every indicator source that pointed at the old id. */
	function renameReferenceInstrument(index: number, raw: string): void {
		const reference = model?.reference_instruments[index];
		if (!model || reference === undefined) return;
		const previous = reference.id;
		const next = raw.trim();
		reference.id = next;
		for (const indicator of model.indicators) {
			if (indicator.source === previous) indicator.source = next;
		}
		markDirty();
	}

	/** Upper-case one reference product id as it is typed. */
	function setReferenceProduct(index: number, raw: string): void {
		const reference = model?.reference_instruments[index];
		if (!model || reference === undefined) return;
		reference.product_id = raw.trim().toUpperCase();
		markDirty();
	}
</script>

<section class="panel">
	<h2>Market and data</h2>
	<label
		>Instrument kind
		<select
			aria-label="Instrument kind"
			value={model.instrument_kind}
			onchange={(event) => setKind(event.currentTarget.value as InstrumentKind)}
		>
			<option value="spot">Spot</option>
			<option value="future">Futures (CFM contract)</option>
		</select></label
	>
	<div class="grid-two">
		<label
			>{futures ? 'Contract' : 'Product'}
			<input
				value={model.product_id}
				spellcheck="false"
				autocomplete="off"
				placeholder={futures ? 'ETP-20DEC30-CDE' : 'BTC-USDC'}
				oninput={(event) => setProduct(event.currentTarget.value)}
			/></label
		>
		<label
			>Timeframe
			<select aria-label="Timeframe" bind:value={model.timeframe} onchange={markDirty}>
				{#each EXECUTION_TIMEFRAMES as clock (clock)}
					<option value={clock}>{clock}</option>
				{/each}
			</select></label
		>
	</div>
	{#if futures && model.derivatives !== null}
		<FuturesFields bind:model {onchange} />
	{/if}
	<label
		>Warmup bars (required history before signals)
		<input type="number" min="1" bind:value={model.warmup_bars} oninput={markDirty} /></label
	>
	<div class="reference-block" data-testid="reference-instruments">
		<h3>Reference instruments (optional)</h3>
		{#each model.reference_instruments as reference, index (index)}
			<div class="indicator-row" data-testid="reference-row">
				<label
					>Reference id
					<input
						value={reference.id}
						spellcheck="false"
						autocomplete="off"
						oninput={(event) => renameReferenceInstrument(index, event.currentTarget.value)}
					/></label
				>
				<label
					>Reference product
					<input
						value={reference.product_id}
						spellcheck="false"
						autocomplete="off"
						placeholder={`BTC-${quote}`}
						oninput={(event) => setReferenceProduct(index, event.currentTarget.value)}
					/></label
				>
				<label
					>Reference timeframe
					<select bind:value={reference.timeframe} onchange={markDirty}>
						{#each validReferenceTimeframes(model.timeframe) as clock (clock)}
							<option value={clock}>{clock}</option>
						{/each}
					</select></label
				>
				<button class="secondary" type="button" onclick={() => removeReferenceInstrument(index)}
					>Remove reference</button
				>
			</div>
		{/each}
		<button
			class="secondary"
			type="button"
			disabled={model.reference_instruments.length >= MAX_REFERENCE_INSTRUMENTS}
			onclick={addReferenceInstrument}>Add reference instrument</button
		>
		<div class="hint">
			Read-only series another market's indicators come from (for example BTC-{quote} 1d for a BTC regime
			gate). Never traded: orders stay on {model.product_id}. Up to {MAX_REFERENCE_INSTRUMENTS}, in
			the same quote currency, on {model.timeframe} or a coarser integer-multiple clock. At each decision
			close only the reference bar that has already closed is used, and paper or live skip entries while
			a reference is stale or missing. Pick a reference as an indicator's instrument under Indicators.
		</div>
	</div>
	<div class="hint">
		{futures
			? 'A Coinbase CFM futures contract, settled in USD. Futures run as backtests and paper books only; there is no live futures path.'
			: "Coinbase spot, quoted in the product's own currency (for example USD or USDC)."} Research, paper,
		and live use any ingested venue clock (this strategy uses {model.timeframe} candles). Sub-hour live
		requires a connected user-order feed. Optional HTF filters may use a strictly coarser integer-multiple
		venue clock; paper and live evaluate those strategies on last-completed HTF bars.
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
	@media (max-width: 640px) {
		.grid-two {
			grid-template-columns: 1fr;
		}
	}
</style>
