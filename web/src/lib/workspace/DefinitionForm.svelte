<script lang="ts">
	/**
	 * Strategy definition form (the Build stage's left column).
	 *
	 * Section navigation plus the rule rows: indicators, the ALL / ANY / NOT
	 * entry tree, the optional higher-timeframe filter, exits, sizing, limits,
	 * and execution preferences. `readonly` renders an immutable published
	 * definition in the same layout with every control disabled.
	 */
	import {
		defaultHtfFilter,
		validHtfTimeframes,
		INDICATOR_KIND_OPTIONS,
		IDENTITY_INPUT_OPTIONS,
		applyIndicatorKindDefaults,
		isConfigurableRollingKind,
		defaultIndicatorOperand,
		indicatorOperandKey,
		parseIndicatorOperandKey,
		operandChoices,
		type BuilderModel,
		type ConditionDraft,
		type IndicatorDraft
	} from '$lib/strategies';

	let {
		model = $bindable(),
		readonly = false,
		onchange
	}: {
		model: BuilderModel;
		readonly?: boolean;
		/** Called after every edit (marks dirty and re-validates in the parent). */
		onchange: () => void;
	} = $props();

	let activeSection = $state('overview');

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
	function toggleRootNot(kind: 'entry' | 'htf'): void {
		if (!model) return;
		if (kind === 'entry') {
			const root = model.entry.when;
			model.entry.when = 'not' in root ? root.not : ({ not: root } satisfies ConditionDraft);
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

	/** Keep each indicator's input and parameters aligned with its kind. */
	function onIndicatorKindChange(indicator: IndicatorDraft): void {
		applyIndicatorKindDefaults(indicator);
		markDirty();
	}

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

	function toggleHtfFilter(enabled: boolean): void {
		if (!model) return;
		model.htf_filter = enabled ? defaultHtfFilter(model.timeframe) : null;
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
			>{readonly ? 'Published definition (read-only)' : 'Draft definition'}</legend
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
					Identity note: the name is part of the immutable published fingerprint.
				</div>
			</section>
		{:else if activeSection === 'market'}
			<section class="panel">
				<h2>Market and data</h2>
				<div class="grid-two">
					<label>Product<input value={model.product_id} disabled /></label>
					<label>Timeframe<input value={model.timeframe} disabled /></label>
				</div>
				<label
					>Warmup bars (required history before signals)
					<input type="number" min="1" bind:value={model.warmup_bars} oninput={markDirty} /></label
				>
				<div class="hint">
					V1 is Coinbase USD spot, long-only. Research, paper, and live use any ingested venue clock
					(this draft uses {model.timeframe} candles). Sub-hour live requires a connected user-order feed.
					Optional HTF filters may use a strictly coarser integer-multiple venue clock; paper and live
					evaluate those strategies on last-completed HTF bars.
				</div>
			</section>
		{:else if activeSection === 'indicators'}
			<section class="panel">
				<h2>Indicators</h2>
				{#each model.indicators as indicator, index (index)}
					<div class="indicator-row">
						{@render indicatorFields(indicator, true)}
						<button class="secondary" type="button" onclick={() => removeIndicator(index)}
							>Remove</button
						>
					</div>
				{/each}
				<button class="secondary" type="button" onclick={addIndicator}>Add indicator</button>
				<div class="hint">
					OHLCV identity copies one candle field. Constant is a named level for crossovers (RSI
					crosses 40). ATR / Williams %R / CCI / stochastic / ADX use high/low/close. MFI uses
					high/low/close/volume. EMA, SMA, WMA, highest, lowest, stdev, sample stdev, ROC, and
					momentum select one OHLCV field. RSI, volume SMA, MACD, and Bollinger stay locked. MACD
					declares fast/slow/signal periods (fast &lt; slow). Stochastic declares %K and %D periods.
					Bollinger adds a population-stdev multiplier. Conditions reference
					MACD/Bollinger/stochastic/ADX outputs as series ids. RSI, ATR, Williams %R, CCI, MFI, and
					ADX periods cap at 100; stochastic %K caps at 100. Momentum and MFI need period + 1 bars.
					MACD needs slow + signal − 1 bars. Stochastic needs k + d − 1 bars. ADX needs 2×period − 1
					bars. Optional per-indicator timeframes may use a coarser integer-multiple venue clock;
					omitting the field keeps the decision clock. Constant omits timeframe. Stop ATR stays on
					the decision clock. Extra-TF values overlay LTF entry before the HTF filter AND.
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
					Enable higher-timeframe filter
				</label>
				{#if model.htf_filter}
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
						<div class="indicator-row">
							{@render indicatorFields(indicator, false)}
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
						>Take profit — reward/risk multiple
						<input
							inputmode="decimal"
							bind:value={model.exits.take_profit.multiple}
							oninput={markDirty}
						/></label
					>
					<span></span>
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
					<label>Trailing stop<input value="disabled (V1)" disabled /></label>
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
						>Minimum USD notional
						<input
							inputmode="decimal"
							bind:value={model.sizing.min_quote_notional}
							oninput={markDirty}
						/></label
					>
					<label
						>Maximum USD notional
						<input
							inputmode="decimal"
							bind:value={model.sizing.max_quote_notional}
							oninput={markDirty}
						/></label
					>
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
				<div class="hint">V1 allows exactly one concurrent position per strategy.</div>
			</section>
		{:else if activeSection === 'execution'}
			<section class="panel">
				<h2>Execution preferences</h2>
				<label
					>Entry preference
					<select bind:value={model.execution.entry_preference} onchange={markDirty}>
						<option value="maker_only">Maker only</option>
						<option value="marketable_limit">Marketable limit</option>
					</select></label
				>
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

{#snippet indicatorFields(indicator: IndicatorDraft, allowTimeframe: boolean)}
	<label>Id<input bind:value={indicator.id} oninput={markDirty} /></label>
	<label
		>Kind
		<select bind:value={indicator.kind} onchange={() => onIndicatorKindChange(indicator)}>
			{#each INDICATOR_KIND_OPTIONS as option (option.kind)}
				<option value={option.kind}>{option.label}</option>
			{/each}
		</select></label
	>
	{#if allowTimeframe && indicator.kind !== 'constant' && model}
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
	{#if indicator.kind === 'constant'}
		<label>Value<input bind:value={indicator.parameters.value} oninput={markDirty} /></label>
	{:else if indicator.kind === 'macd'}
		<label
			>Fast period<input
				type="number"
				min="2"
				bind:value={indicator.parameters.fast_period}
				oninput={markDirty}
			/></label
		>
		<label
			>Slow period<input
				type="number"
				min="2"
				bind:value={indicator.parameters.slow_period}
				oninput={markDirty}
			/></label
		>
		<label
			>Signal period<input
				type="number"
				min="2"
				bind:value={indicator.parameters.signal_period}
				oninput={markDirty}
			/></label
		>
	{:else if indicator.kind === 'bollinger'}
		<label
			>Period<input
				type="number"
				min="2"
				bind:value={indicator.parameters.period}
				oninput={markDirty}
			/></label
		>
		<label
			>Stdev multiplier<input
				bind:value={indicator.parameters.stdev_multiplier}
				oninput={markDirty}
			/></label
		>
	{:else if indicator.kind === 'stochastic'}
		<label
			>%K period<input
				type="number"
				min="2"
				bind:value={indicator.parameters.k_period}
				oninput={markDirty}
			/></label
		>
		<label
			>%D period<input
				type="number"
				min="2"
				bind:value={indicator.parameters.d_period}
				oninput={markDirty}
			/></label
		>
	{:else if indicator.kind !== 'identity'}
		<label
			>Period<input
				type="number"
				min="2"
				bind:value={indicator.parameters.period}
				oninput={markDirty}
			/></label
		>
	{/if}
{/snippet}

{#snippet conditionNode(
	condition: ConditionDraft,
	parent: object,
	depth: number,
	root: ConditionDraft,
	indicators: IndicatorDraft[],
	kind: 'entry' | 'htf'
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
				left: { indicator?: string; series?: string; literal?: string };
				operator: string;
				right: { indicator?: string; series?: string; literal?: string };
			}}
			<div class="rule-comparison">
				<span class="kw">{depth === 0 ? 'IF' : rowKeyword(parent, index)}</span>
				<select
					aria-label="Left operand"
					value={leftOperandKey(comparison)}
					onchange={(event) =>
						setLeftOperand(comparison, (event.currentTarget as HTMLSelectElement).value)}
				>
					{#each operandChoices(indicators) as choice (choice.key)}
						<option value={choice.key}>{choice.label}</option>
					{/each}
				</select>
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
					{#each operandChoices(indicators) as choice (choice.key)}
						<option value={choice.key}>{choice.label}</option>
					{/each}
				</select>
				{#if comparison.right.indicator !== undefined}
					<span class="operand-name">{operandLabel(comparison.right)}</span>
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
		grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
		gap: 10px;
		align-items: end;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
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
