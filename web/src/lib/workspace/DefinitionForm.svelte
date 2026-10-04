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
	 */
	import { untrack } from 'svelte';
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
		defaultHtfFilter,
		defaultReferenceInstrument,
		defaultSignalExit,
		EXECUTION_TIMEFRAMES,
		MAX_REFERENCE_INSTRUMENTS,
		validHtfTimeframes,
		validReferenceTimeframes,
		IDENTITY_INPUT_OPTIONS,
		applyIndicatorKindDefaults,
		isConfigurableRollingKind,
		defaultIndicatorOperand,
		indicatorOperandKey,
		parseIndicatorOperandKey,
		operandChoices,
		quoteLabelFor,
		referenceBaseLabel,
		type BuilderModel,
		type ConditionDraft,
		type IndicatorDraft,
		type OperandChoice
	} from '$lib/strategies';
	import type { BuildSection } from '$lib/strategy-workspace';
	import IndicatorKindPicker from './IndicatorKindPicker.svelte';

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

	const operators: { value: string; label: string }[] = [
		{ value: 'crosses_above', label: 'crosses above' },
		{ value: 'crosses_below', label: 'crosses below' },
		{ value: 'greater_than', label: '>' },
		{ value: 'greater_than_or_equal', label: '≥' },
		{ value: 'less_than', label: '<' },
		{ value: 'less_than_or_equal', label: '≤' },
		{ value: 'equals', label: '=' }
	];

	function markDirty(): void {
		onchange();
	}

	/** Last reward/risk multiple, restored when the operator switches back from `none`. */
	let lastTakeProfitMultiple = $state('2');

	/** Switch between a reward/risk take-profit and none (stop, trail, time exit only). */
	function setTakeProfitKind(kind: string): void {
		const current = model.exits.take_profit;
		if (kind === 'none') {
			if (current.kind === 'reward_risk') lastTakeProfitMultiple = current.multiple;
			model.exits.take_profit = { kind: 'none' };
		} else {
			model.exits.take_profit = { kind: 'reward_risk', multiple: lastTakeProfitMultiple };
		}
		markDirty();
	}

	function setTakeProfitMultiple(multiple: string): void {
		model.exits.take_profit = { kind: 'reward_risk', multiple };
		markDirty();
	}

	/** Edit the Coinbase product id (`BASE-QUOTE`); the base currency follows the id. */
	function setProduct(raw: string): void {
		const productId = raw.trim().toUpperCase();
		model.product_id = productId;
		model.base_currency = productId.split('-')[0] ?? '';
		markDirty();
	}

	/** Row keyword for a child of a group: IF for the first, then AND / OR. */
	function rowKeyword(parent: object, index: number): string {
		const container = parent as { all?: unknown[]; any?: unknown[]; not?: unknown };
		if (container.not !== undefined) return 'NOT';
		if (index === 0) return 'IF';
		return container.any !== undefined ? 'OR' : 'AND';
	}

	function isGroup(condition: ConditionDraft): boolean {
		return 'all' in condition || 'any' in condition;
	}

	function isNot(condition: ConditionDraft): boolean {
		return 'not' in condition;
	}

	function addComparison(
		parent: { all?: ConditionDraft[]; any?: ConditionDraft[] },
		indicators: IndicatorDraft[]
	): void {
		const child: ConditionDraft = {
			left: defaultIndicatorOperand(indicators),
			operator: 'greater_than',
			right: { literal: '0' }
		};
		if (parent.all) parent.all.push(child);
		if (parent.any) parent.any.push(child);
		markDirty();
	}

	function addGroup(
		parent: { all?: ConditionDraft[]; any?: ConditionDraft[] },
		kind: 'all' | 'any'
	): void {
		const child: ConditionDraft = kind === 'all' ? { all: [] } : { any: [] };
		if (parent.all) parent.all.push(child);
		if (parent.any) parent.any.push(child);
		markDirty();
	}

	function removeChild(
		parent: { all?: ConditionDraft[]; any?: ConditionDraft[] },
		index: number
	): void {
		if (parent.all) parent.all.splice(index, 1);
		if (parent.any) parent.any.splice(index, 1);
		markDirty();
	}

	function childIndex(condition: ConditionDraft, parent: object): number {
		const container = parent as {
			all?: ConditionDraft[];
			any?: ConditionDraft[];
			not?: ConditionDraft;
		};
		if (container.all) return container.all.indexOf(condition);
		if (container.any) return container.any.indexOf(condition);
		return -1;
	}

	/** Add one negated comparison as a direct child of a nested group. */
	function addNotChild(
		group: { all?: ConditionDraft[]; any?: ConditionDraft[] },
		indicators: IndicatorDraft[]
	): void {
		const child: ConditionDraft = {
			not: {
				left: defaultIndicatorOperand(indicators),
				operator: 'greater_than',
				right: { literal: '0' }
			}
		};
		if (group.all) group.all.push(child);
		if (group.any) group.any.push(child);
		markDirty();
	}

	/** Wrap the root condition itself so the top-level group can be negated. */
	function toggleRootNot(kind: 'entry' | 'htf' | 'exit'): void {
		if (!model) return;
		if (kind === 'entry') {
			const root = model.entry.when;
			model.entry.when = 'not' in root ? root.not : ({ not: root } satisfies ConditionDraft);
		} else if (kind === 'exit') {
			const signalExit = model.exits.signal_exit;
			if (signalExit === undefined) return;
			const root = signalExit.when;
			signalExit.when = 'not' in root ? root.not : ({ not: root } satisfies ConditionDraft);
		} else if (model.htf_filter !== null) {
			const root = model.htf_filter.when;
			model.htf_filter.when = 'not' in root ? root.not : ({ not: root } satisfies ConditionDraft);
		}
		markDirty();
	}

	function isRootGroup(condition: ConditionDraft, root: ConditionDraft): boolean {
		return condition === root;
	}

	function rightOperandKey(comparison: {
		right: { indicator?: string; series?: string; literal?: string };
	}): string {
		return comparison.right.indicator !== undefined
			? indicatorOperandKey({
					indicator: comparison.right.indicator,
					series: comparison.right.series
				})
			: 'literal';
	}

	function setRightOperand(
		comparison: { right: { indicator?: string; series?: string; literal?: string } },
		key: string
	): void {
		if (key === 'literal') {
			comparison.right = { literal: '0' };
		} else {
			comparison.right = parseIndicatorOperandKey(key) ?? { literal: '0' };
		}
		markDirty();
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

	/**
	 * Operand select blocks: single-output indicators as plain options and each
	 * multi-series indicator as one `<optgroup>` of its series (keys unchanged).
	 */
	function operandOptionBlocks(
		indicators: IndicatorDraft[]
	): { key: string; group?: string; choices: OperandChoice[] }[] {
		const blocks: { key: string; group?: string; choices: OperandChoice[] }[] = [];
		for (const choice of operandChoices(indicators, model.reference_instruments)) {
			const last = blocks.at(-1);
			if (choice.group !== undefined && last !== undefined && last.group === choice.group) {
				last.choices.push(choice);
				continue;
			}
			blocks.push({ key: choice.key, group: choice.group, choices: [choice] });
		}
		return blocks;
	}

	const quote = $derived(quoteLabelFor(model.product_id));

	function leftOperandKey(comparison: {
		left: { indicator?: string; series?: string; literal?: string };
	}): string {
		return comparison.left.indicator !== undefined
			? indicatorOperandKey({
					indicator: comparison.left.indicator,
					series: comparison.left.series
				})
			: 'literal';
	}

	function setLeftOperand(
		comparison: { left: { indicator?: string; series?: string; literal?: string } },
		key: string
	): void {
		if (key === 'literal') {
			comparison.left = { literal: '0' };
		} else {
			comparison.left = parseIndicatorOperandKey(key) ?? { literal: '0' };
		}
		markDirty();
	}

	function operandLabel(operand: { indicator?: string; series?: string }): string {
		if (operand.indicator === undefined) return '';
		return operand.series === undefined
			? operand.indicator
			: `${operand.indicator}.${operand.series}`;
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

	/** Add the optional exit rule (mirroring a single-cross entry) or drop it from the document. */
	function toggleSignalExit(enabled: boolean): void {
		if (!model) return;
		if (enabled) {
			model.exits.signal_exit = defaultSignalExit(model.entry.when, model.indicators);
		} else {
			delete model.exits.signal_exit;
		}
		markDirty();
	}

	/** Read-only trailing-stop summary: the ATR trail when enabled, else "disabled". */
	const trailingStopText = $derived(
		model.exits.trailing_stop.enabled
			? `${model.exits.trailing_stop.multiple}× ATR (${model.exits.trailing_stop.atr_indicator})`
			: 'disabled'
	);

	function toggleHtfFilter(enabled: boolean): void {
		if (!model) return;
		model.htf_filter = enabled ? defaultHtfFilter(model.timeframe) : null;
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

	/** Point one indicator at the traded instrument ('') or a reference id. */
	function setIndicatorSource(indicator: IndicatorDraft, source: string): void {
		indicator.source = source;
		if (source !== '') indicator.timeframe = '';
		markDirty();
	}
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
							<button
								class="secondary"
								type="button"
								onclick={() => removeReferenceInstrument(index)}>Remove reference</button
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
						Read-only series another market's indicators come from (for example BTC-{quote} 1d for a BTC
						regime gate). Never traded: orders stay on {model.product_id}. Up to {MAX_REFERENCE_INSTRUMENTS},
						in the same quote currency, on {model.timeframe} or a coarser integer-multiple clock. At each
						decision close only the reference bar that has already closed is used, and paper or live skip
						entries while a reference is stale or missing. Pick a reference as an indicator's instrument
						under Indicators.
					</div>
				</div>
				<div class="hint">
					Coinbase spot, quoted in the product's own currency (for example USD or USDC). Research,
					paper, and live use any ingested venue clock (this strategy uses {model.timeframe} candles).
					Sub-hour live requires a connected user-order feed. Optional HTF filters may use a strictly
					coarser integer-multiple venue clock; paper and live evaluate those strategies on last-completed
					HTF bars.
				</div>
			</section>
		{:else if activeSection === 'indicators'}
			<section class="panel">
				<h2>Indicators</h2>
				{#each model.indicators as indicator, index (index)}
					<div class="indicator-row" data-testid="indicator-row">
						{@render indicatorFields(indicator, true, `ltf-${index}`)}
						<button class="secondary" type="button" onclick={() => removeIndicator(index)}
							>Remove</button
						>
					</div>
				{/each}
				<button class="secondary" type="button" onclick={addIndicator}>Add indicator</button>
				<div class="hint">
					Pick a kind to see its parameters, defaults, and one-line help. Multi-series kinds (MACD,
					Bollinger, Supertrend, Ichimoku, …) expose each output as its own operand in conditions.
					Offset reads the value from that many completed bars earlier on the indicator's own clock
					(offset 1 on a 20-bar Donchian is the prior 20-bar high) and adds to the warmup. An
					optional timeframe uses a coarser integer-multiple venue clock; constants take neither.
					Stop ATRs stay on the decision clock.
				</div>
			</section>
		{:else if activeSection === 'entry'}
			<section class="panel">
				<h2>Entry conditions</h2>
				<div class="rule-tree">
					{#if model.entry.when}
						{@render conditionNode(
							model.entry.when,
							model.entry.when,
							0,
							model.entry.when,
							model.indicators,
							'entry'
						)}
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
					{#each model.htf_filter.indicators as indicator, index (index)}
						<div class="indicator-row" data-testid="htf-indicator-row">
							{@render indicatorFields(indicator, false, `htf-${index}`)}
							<button class="secondary" type="button" onclick={() => removeHtfIndicator(index)}
								>Remove</button
							>
						</div>
					{/each}
					<button class="secondary" type="button" onclick={addHtfIndicator}
						>Add HTF indicator</button
					>
					<div class="rule-tree">
						{@render conditionNode(
							model.htf_filter.when,
							model.htf_filter.when,
							0,
							model.htf_filter.when,
							model.htf_filter.indicators,
							'htf'
						)}
					</div>
					<div class="hint">
						The HTF <code>when</code> tree is AND-ed with LTF entry using the last completed HTF bar.
						Paper and live evaluate this block on live complete-only HTF candles.
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
					<input
						type="number"
						min="0"
						bind:value={model.cooldown_bars}
						oninput={markDirty}
					/></label
				>
			</section>
		{:else if activeSection === 'exits'}
			<section class="panel">
				<h2>Exit conditions and protective stops</h2>
				<div class="grid-two">
					<label
						>Initial stop — ATR indicator
						<select bind:value={model.exits.initial_stop.atr_indicator} onchange={markDirty}>
							{#each model.indicators.filter((candidate) => candidate.kind === 'atr') as atr (atr.id)}
								<option value={atr.id}>{atr.id} (ATR)</option>
							{/each}
							{#if !model.indicators.some((candidate) => candidate.kind === 'atr')}
								<option value="">No ATR indicator defined</option>
							{/if}
						</select></label
					>
					<label
						>Initial stop — ATR multiple
						<input
							inputmode="decimal"
							bind:value={model.exits.initial_stop.multiple}
							oninput={markDirty}
						/></label
					>
				</div>
				<div class="grid-two">
					<label
						>Take profit
						<select
							aria-label="Take profit kind"
							value={model.exits.take_profit.kind}
							onchange={(event) => setTakeProfitKind(event.currentTarget.value)}
						>
							<option value="reward_risk">Reward/risk multiple</option>
							<option value="none">None — exit on stop, trail, or time</option>
						</select></label
					>
					{#if model.exits.take_profit.kind === 'reward_risk'}
						<label
							>Take profit — reward/risk multiple
							<input
								inputmode="decimal"
								value={model.exits.take_profit.multiple}
								oninput={(event) => setTakeProfitMultiple(event.currentTarget.value)}
							/></label
						>
					{:else}
						<p class="hint">
							No take-profit order rests. Paper enforces the stop on closed bars; live rests a
							Coinbase stop-limit at the stop.
						</p>
					{/if}
				</div>
				<div class="grid-two">
					<label
						>Time exit — max bars held
						<input
							type="number"
							min="1"
							bind:value={model.exits.time_exit.max_bars_held}
							oninput={markDirty}
						/></label
					>
					<label>Trailing stop<input value={trailingStopText} disabled /></label>
					<label class="field">
						<span>Minimum net maker-target return (fraction, optional)</span>
						<input
							type="text"
							value={model.economic_guard?.minimum_net_target_return_fraction ?? ''}
							oninput={(event) => {
								const value = event.currentTarget.value.trim();
								model.economic_guard =
									value === '' ? null : { minimum_net_target_return_fraction: value };
								markDirty();
							}}
							placeholder="Disabled when empty"
							disabled={readonly}
						/>
						<small
							>Requires a target. Includes entry and exit maker fees; live requires a fee profile.</small
						>
					</label>
				</div>
				<div class="exit-rule" data-testid="signal-exit-section">
					<label class="cooldown-row"
						><input
							type="checkbox"
							data-testid="signal-exit-toggle"
							checked={model.exits.signal_exit !== undefined}
							onchange={(event) =>
								toggleSignalExit((event.currentTarget as HTMLInputElement).checked)}
						/>
						Exit when a rule matches (optional)
					</label>
					{#if model.exits.signal_exit}
						<h3 class="exit-rule-title">Exit when</h3>
						<div class="rule-tree" data-testid="signal-exit-tree">
							{@render conditionNode(
								model.exits.signal_exit.when,
								model.exits.signal_exit.when,
								0,
								model.exits.signal_exit.when,
								model.indicators,
								'exit'
							)}
						</div>
					{/if}
					<p class="hint">
						Checked on every closed bar after the fill bar while a position is open, using the same
						indicators as entry. A match sells at that bar's close as a taker, like the time exit.
						The initial stop still guards the position until then: when a bar trades through the
						stop, the stop exit wins. The trailing stop, take-profit, and time exit still apply;
						whichever triggers first closes the position.
					</p>
				</div>
			</section>
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

{#snippet indicatorFields(indicator: IndicatorDraft, allowTimeframe: boolean, key: string)}
	{@const entry = findCatalogEntry(indicator.kind)}
	<label>Id<input bind:value={indicator.id} oninput={markDirty} /></label>
	<div class="field">
		<span class="field-label" id={`${key}-kind-label`}>Kind</span>
		<IndicatorKindPicker
			kind={indicator.kind}
			labelledby={`${key}-kind-label`}
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
		{@const inputId = `${key}-${spec.name}`}
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
			<label class="field-label" for={`${key}-offset`}>Offset (bars ago)</label>
			<input
				id={`${key}-offset`}
				type="number"
				min="0"
				max={MAX_INDICATOR_OFFSET}
				step="1"
				placeholder="0"
				aria-describedby={`${key}-offset-help`}
				value={indicator.offset ?? ''}
				oninput={(event) => setOffsetFromInput(indicator, event.currentTarget)}
			/>
			<small class="field-help" id={`${key}-offset-help`}
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
{/snippet}

{#snippet operandOptions(indicators: IndicatorDraft[])}
	{#each operandOptionBlocks(indicators) as block (block.key)}
		{#if block.group === undefined}
			{#each block.choices as choice (choice.key)}
				<option value={choice.key}>{choice.label}</option>
			{/each}
		{:else}
			<optgroup label={block.group}>
				{#each block.choices as choice (choice.key)}
					<option value={choice.key}>{choice.label}</option>
				{/each}
			</optgroup>
		{/if}
	{/each}
{/snippet}

{#snippet conditionNode(
	condition: ConditionDraft,
	parent: object,
	depth: number,
	root: ConditionDraft,
	indicators: IndicatorDraft[],
	kind: 'entry' | 'htf' | 'exit'
)}
	{@const index = childIndex(condition, parent)}
	{@const isRoot = isRootGroup(condition, root)}
	<div class="rule-node" style="margin-left: {Math.min(depth, 4) * 14}px" data-depth={depth}>
		{#if isGroup(condition)}
			{@const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] }}
			<div class="rule-group-head">
				{#if depth > 0}<span class="kw">{rowKeyword(parent, index)}</span>{/if}
				<span class="group-kind">{group.all !== undefined ? 'ALL' : 'ANY'}</span>
				<span class="group-hint"
					>{group.all !== undefined
						? 'every rule below is true'
						: 'at least one rule below is true'}</span
				>
				{#if depth > 0}
					<button class="secondary" type="button" onclick={() => removeChild(parent, index)}
						>Remove group</button
					>
				{/if}
				<button class="secondary" type="button" onclick={() => addComparison(group, indicators)}
					>+ comparison</button
				>
				<button class="secondary" type="button" onclick={() => addGroup(group, 'all')}>+ ALL</button
				>
				<button class="secondary" type="button" onclick={() => addGroup(group, 'any')}>+ ANY</button
				>
				{#if isRoot}
					<button
						class="secondary"
						type="button"
						onclick={() => toggleRootNot(kind)}
						aria-label="Negate the root condition group">+ NOT</button
					>
				{:else}
					<button
						class="secondary"
						type="button"
						onclick={() => addNotChild(group, indicators)}
						aria-label="Add a negated condition">+ NOT</button
					>
				{/if}
			</div>
			{#each group.all ?? group.any ?? [] as child, childIdx (childIdx)}
				{@render conditionNode(child, group, depth + 1, root, indicators, kind)}
			{/each}
		{:else if isNot(condition)}
			<div class="rule-group-head">
				<span class="group-kind">NOT</span>
				<button class="secondary" type="button" onclick={() => removeChild(parent, index)}
					>Remove</button
				>
			</div>
			{@render conditionNode(
				(condition as { not: ConditionDraft }).not,
				condition,
				depth + 1,
				root,
				indicators,
				kind
			)}
		{:else}
			{@const comparison = condition as {
				left: { indicator?: string; series?: string; literal?: string; offset?: number };
				operator: string;
				right: { indicator?: string; series?: string; literal?: string; offset?: number };
			}}
			<div class="rule-comparison">
				<span class="kw">{depth === 0 ? 'IF' : rowKeyword(parent, index)}</span>
				<select
					aria-label="Left operand"
					value={leftOperandKey(comparison)}
					onchange={(event) =>
						setLeftOperand(comparison, (event.currentTarget as HTMLSelectElement).value)}
				>
					{@render operandOptions(indicators)}
				</select>
				{#if comparison.left.indicator !== undefined && indicators.find((item) => item.id === comparison.left.indicator)?.kind !== 'constant'}
					<label
						title="Completed bars of this indicator's timeframe; adds to its indicator offset."
					>
						Bars ago
						<input
							type="number"
							min="0"
							max={MAX_INDICATOR_OFFSET}
							step="1"
							aria-label="Left operand offset (bars ago)"
							value={comparison.left.offset ?? 0}
							oninput={(event) => {
								const offset = Number(event.currentTarget.value);
								if (offset === 0) delete comparison.left.offset;
								else comparison.left.offset = offset;
								markDirty();
							}}
						/>
					</label>
				{/if}
				{#if comparison.left.indicator === undefined}
					<input
						class="literal"
						inputmode="decimal"
						value={comparison.left.literal ?? ''}
						oninput={(event) => {
							comparison.left = { literal: (event.currentTarget as HTMLInputElement).value };
							markDirty();
						}}
						placeholder="value"
						aria-label="Left literal value"
					/>
				{/if}
				<select bind:value={comparison.operator} onchange={markDirty} aria-label="Operator">
					{#each operators as operator (operator.value)}
						<option value={operator.value}>{operator.label}</option>
					{/each}
				</select>
				<select
					aria-label="Right operand"
					value={rightOperandKey(comparison)}
					onchange={(event) =>
						setRightOperand(comparison, (event.currentTarget as HTMLSelectElement).value)}
				>
					{@render operandOptions(indicators)}
				</select>
				{#if comparison.right.indicator !== undefined}
					<span class="operand-name">{operandLabel(comparison.right)}</span>
					{#if indicators.find((item) => item.id === comparison.right.indicator)?.kind !== 'constant'}
						<label
							title="Completed bars of this indicator's timeframe; adds to its indicator offset."
						>
							Bars ago
							<input
								type="number"
								min="0"
								max={MAX_INDICATOR_OFFSET}
								step="1"
								aria-label="Right operand offset (bars ago)"
								value={comparison.right.offset ?? 0}
								oninput={(event) => {
									const offset = Number(event.currentTarget.value);
									if (offset === 0) delete comparison.right.offset;
									else comparison.right.offset = offset;
									markDirty();
								}}
							/>
						</label>
					{/if}
				{:else}
					<input
						class="literal"
						inputmode="decimal"
						value={comparison.right.literal ?? ''}
						oninput={(event) => {
							comparison.right = { literal: (event.currentTarget as HTMLInputElement).value };
							markDirty();
						}}
						placeholder="value"
						aria-label="Right literal value"
					/>
				{/if}
				<button
					class="secondary"
					type="button"
					onclick={() => removeChild(parent, index)}
					aria-label="Remove this rule"
				>
					×
				</button>
			</div>
		{/if}
	</div>
{/snippet}

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
	fieldset:disabled .secondary {
		display: none;
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
	.rule-node {
		display: grid;
		gap: 8px;
		padding-left: 10px;
		border-left: 2px solid var(--line-2);
	}
	.rule-node[data-depth='0'] {
		padding-left: 0;
		border-left: 0;
	}
	.rule-group-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
	}
	.group-kind {
		color: var(--accent);
		font-size: var(--fs-sm);
		font-weight: 700;
		letter-spacing: 0.08em;
	}
	.group-hint {
		margin-right: auto;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.kw {
		flex: none;
		width: 36px;
		color: var(--faint);
		font-size: 11.5px;
		font-weight: 600;
	}
	.rule-comparison {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		padding: 8px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.rule-comparison select,
	.rule-comparison input.literal {
		width: auto;
		min-width: 110px;
		background: var(--surface);
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
	}
	.operand-name {
		padding: 0 2px;
		color: var(--accent);
		font-size: var(--fs-sm);
	}
	.exit-rule {
		display: grid;
		gap: 8px;
		margin-top: 12px;
		padding-top: 12px;
		border-top: 1px solid var(--line-2);
	}
	.exit-rule-title {
		margin: 0;
		font-size: var(--fs-sm);
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
	.rule-comparison input.literal {
		width: 96px;
		min-width: 72px;
	}
	@media (max-width: 640px) {
		.grid-two {
			grid-template-columns: 1fr;
		}
	}
</style>
