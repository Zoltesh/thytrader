<script lang="ts">
	/**
	 * The fields of one indicator row: id, kind picker, optional instrument
	 * (a declared reference) and timeframe, identity/rolling source, every
	 * catalog parameter with its help, the bar offset, and the kind summary with
	 * its warmup. Edits mutate the bound indicator and call `onchange`.
	 */
	import {
		MAX_INDICATOR_OFFSET,
		findCatalogEntry,
		indicatorWarmupBars,
		readParameter,
		writeParameter,
		type IndicatorKindValue,
		type IndicatorParameterSpec
	} from '$lib/indicator-catalog';
	import {
		validHtfTimeframes,
		IDENTITY_INPUT_OPTIONS,
		applyIndicatorKindDefaults,
		isConfigurableRollingKind,
		referenceBaseLabel,
		type BuilderModel,
		type IndicatorDraft
	} from '$lib/strategies';
	import IndicatorKindPicker from '../IndicatorKindPicker.svelte';

	let {
		indicator = $bindable(),
		model,
		allowTimeframe,
		idPrefix,
		readonly,
		onchange
	}: {
		indicator: IndicatorDraft;
		/** The strategy (decision clock, traded product, declared references). */
		model: BuilderModel;
		/** Decision-clock indicators may pick an instrument and a coarser clock; HTF ones may not. */
		allowTimeframe: boolean;
		/** Unique prefix for this row's element ids (`ltf-0`, `htf-1`). */
		idPrefix: string;
		readonly: boolean;
		/** Called after every edit. */
		onchange: () => void;
	} = $props();

	const entry = $derived(findCatalogEntry(indicator.kind));

	function markDirty(): void {
		onchange();
	}

	/** Switch kinds and realign the input, parameters, timeframe, and offset with the new kind. */
	function changeIndicatorKind(indicator: IndicatorDraft, kind: IndicatorKindValue): void {
		indicator.kind = kind;
		applyIndicatorKindDefaults(indicator);
		markDirty();
	}

	/** Text for one parameter input: the stored number or decimal text, or blank. */
	function parameterInputValue(indicator: IndicatorDraft, spec: IndicatorParameterSpec): string {
		const value = readParameter(indicator.parameters, spec.name);
		return typeof value === 'number' || typeof value === 'string' ? String(value) : '';
	}

	/** Store one edited parameter: integers as numbers, decimals as exact text, blank as omitted. */
	function setParameterFromInput(
		indicator: IndicatorDraft,
		spec: IndicatorParameterSpec,
		input: HTMLInputElement
	): void {
		if (spec.value_type === 'integer') {
			const parsed = input.valueAsNumber;
			writeParameter(indicator.parameters, spec.name, Number.isNaN(parsed) ? undefined : parsed);
		} else {
			const text = input.value.trim();
			writeParameter(indicator.parameters, spec.name, text === '' ? undefined : text);
		}
		markDirty();
	}

	/** Store the bar lag; a blank field means the current bar (offset omitted). */
	function setOffsetFromInput(indicator: IndicatorDraft, input: HTMLInputElement): void {
		const parsed = input.valueAsNumber;
		if (Number.isNaN(parsed)) {
			delete indicator.offset;
		} else {
			indicator.offset = parsed;
		}
		markDirty();
	}

	function parameterBound(value: number | string | null): string | undefined {
		return value === null ? undefined : String(value);
	}

	/** Point one indicator at the traded instrument ('') or a reference id. */
	function setIndicatorSource(indicator: IndicatorDraft, source: string): void {
		indicator.source = source;
		if (source !== '') indicator.timeframe = '';
		markDirty();
	}
</script>

<label>Id<input bind:value={indicator.id} oninput={markDirty} /></label>
<div class="field">
	<span class="field-label" id={`${idPrefix}-kind-label`}>Kind</span>
	<IndicatorKindPicker
		kind={indicator.kind}
		labelledby={`${idPrefix}-kind-label`}
		disabled={readonly}
		onselect={(kind) => changeIndicatorKind(indicator, kind)}
	/>
</div>
{#if allowTimeframe && (entry?.supports_source ?? true) && model && model.reference_instruments.length > 0}
	<label
		>Instrument
		<select
			value={indicator.source ?? ''}
			onchange={(event) => setIndicatorSource(indicator, event.currentTarget.value)}
		>
			<option value="">Traded instrument ({model.product_id})</option>
			{#each model.reference_instruments as reference (reference.id)}
				<option value={reference.id}
					>{referenceBaseLabel(reference)} reference · {reference.product_id}
					{reference.timeframe} ({reference.id})</option
				>
			{/each}
		</select></label
	>
{/if}
{#if allowTimeframe && (entry?.supports_timeframe ?? true) && model && !indicator.source}
	<label
		>Timeframe
		<select bind:value={indicator.timeframe} onchange={markDirty}>
			<option value="">Decision clock ({model.timeframe})</option>
			{#each validHtfTimeframes(model.timeframe) as timeframe (timeframe)}
				<option value={timeframe}>{timeframe}</option>
			{/each}
		</select></label
	>
{/if}
{#if indicator.kind === 'identity' || isConfigurableRollingKind(indicator.kind)}
	<label
		>Source
		<select bind:value={indicator.input} onchange={markDirty}>
			{#each IDENTITY_INPUT_OPTIONS as option (option.value)}
				<option value={option.value}>{option.label}</option>
			{/each}
		</select></label
	>
{/if}
{#each entry?.parameters ?? [] as spec (spec.name)}
	{@const inputId = `${idPrefix}-${spec.name}`}
	<div class="field">
		<label class="field-label" for={inputId}
			>{spec.label}{#if spec.optional}<span class="optional">(optional)</span>{/if}</label
		>
		{#if spec.value_type === 'integer'}
			<input
				id={inputId}
				type="number"
				step="1"
				min={parameterBound(spec.minimum)}
				max={parameterBound(spec.maximum)}
				placeholder={spec.optional ? 'Leave blank to omit' : String(spec.default ?? '')}
				aria-describedby={`${inputId}-help`}
				value={parameterInputValue(indicator, spec)}
				oninput={(event) => setParameterFromInput(indicator, spec, event.currentTarget)}
			/>
		{:else}
			<input
				id={inputId}
				inputmode="decimal"
				spellcheck="false"
				autocomplete="off"
				placeholder={spec.optional ? 'Leave blank to omit' : String(spec.default ?? '')}
				aria-describedby={`${inputId}-help`}
				value={parameterInputValue(indicator, spec)}
				oninput={(event) => setParameterFromInput(indicator, spec, event.currentTarget)}
			/>
		{/if}
		<small class="field-help" id={`${inputId}-help`}>{spec.help}</small>
	</div>
{/each}
{#if entry?.supports_offset ?? true}
	<div class="field">
		<label class="field-label" for={`${idPrefix}-offset`}>Offset (bars ago)</label>
		<input
			id={`${idPrefix}-offset`}
			type="number"
			min="0"
			max={MAX_INDICATOR_OFFSET}
			step="1"
			placeholder="0"
			aria-describedby={`${idPrefix}-offset-help`}
			value={indicator.offset ?? ''}
			oninput={(event) => setOffsetFromInput(indicator, event.currentTarget)}
		/>
		<small class="field-help" id={`${idPrefix}-offset-help`}
			>Completed bars back on this clock; blank is the current bar.</small
		>
	</div>
{/if}
{#if entry !== undefined}
	{@const warmup = indicatorWarmupBars(indicator)}
	<p class="kind-summary">
		<span>{entry.summary}</span>
		{#if Number.isFinite(warmup) && warmup > 0}
			<span class="warmup">Needs {warmup} completed {warmup === 1 ? 'bar' : 'bars'}.</span>
		{/if}
	</p>
{/if}

<style>
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
	.field {
		display: grid;
		gap: 6px;
		align-content: start;
		min-width: 0;
	}
	.field-label {
		display: block;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.optional {
		margin-left: 0.35em;
		color: var(--faint);
	}
	.field-help {
		color: var(--faint);
		font-size: var(--fs-xs);
		line-height: 1.35;
	}
	.kind-summary {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 12px;
		grid-column: 1 / -1;
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.kind-summary .warmup {
		color: var(--muted);
	}
</style>
