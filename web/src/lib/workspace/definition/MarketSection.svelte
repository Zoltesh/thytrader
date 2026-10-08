<script lang="ts">
	/**
	 * Market and data: the traded product and decision clock, warmup, and up to
	 * `MAX_REFERENCE_INSTRUMENTS` read-only reference instruments (ADR 0096)
	 * that indicators may read. Renaming a reference retargets every indicator
	 * that read it; removing one leaves those indicators for validation to name.
	 */
	import {
		defaultReferenceInstrument,
		EXECUTION_TIMEFRAMES,
		MAX_REFERENCE_INSTRUMENTS,
		validReferenceTimeframes,
		quoteLabelFor,
		type BuilderModel
	} from '$lib/strategies';

	let {
		model = $bindable(),
		onchange
	}: {
		model: BuilderModel;
		/** Called after every edit. */
		onchange: () => void;
	} = $props();

	const quote = $derived(quoteLabelFor(model.product_id));

	function markDirty(): void {
		onchange();
	}

	/** Edit the Coinbase product id (`BASE-QUOTE`); the base currency follows the id. */
	function setProduct(raw: string): void {
		const productId = raw.trim().toUpperCase();
		model.product_id = productId;
		model.base_currency = productId.split('-')[0] ?? '';
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
	<div class="grid-two">
		<label
			>Product
			<input
				value={model.product_id}
				spellcheck="false"
				autocomplete="off"
				placeholder="BTC-USDC"
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
		Coinbase spot, quoted in the product's own currency (for example USD or USDC). Research, paper,
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
