<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import {
		fetchDraftVersion,
		toBuilderModel,
		fromBuilderModel,
		saveDraft,
		publishDraft,
		listStrategies,
		type BuilderModel,
		type ConditionDraft,
		type IndicatorDraft
	} from '$lib/strategies';

	let model = $state<BuilderModel | null>(null);
	let loading = $state(true);
	let saving = $state(false);
	let publishing = $state(false);
	let dirty = $state(false);
	let error = $state<string | null>(null);
	let savedAt = $state<string | null>(null);
	let validationErrors = $state<string[]>([]);
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

	// The current bar-backtest engine consumes these settings. It fills every
	// entry at the next bar open unconditionally, so cooldown and execution
	// preferences are declared by the schema but not modeled by the engine.
	const engineSupport: { label: string; supported: boolean; note: string }[] = [
		{
			label: 'Entry conditions (ALL / ANY / NOT, comparisons, crossovers)',
			supported: true,
			note: 'evaluated on completed candles, no lookahead'
		},
		{
			label: 'Indicators: EMA, SMA, RSI, ATR, volume SMA',
			supported: true,
			note: 'exact Decimal arithmetic'
		},
		{
			label: 'Risk-fraction sizing with notional bounds',
			supported: true,
			note: 'bounded by exposure fraction'
		},
		{ label: 'ATR initial stop', supported: true, note: 'stop-loss priority inside the bar' },
		{ label: 'Reward/risk take profit', supported: true, note: 'checked after the stop' },
		{ label: 'Time exit (max bars held)', supported: true, note: 'exits at the open' },
		{
			label: 'Entry cooldown (cooldown_bars)',
			supported: false,
			note: 'not modeled by the current backtester'
		},
		{
			label: 'Maker-only / marketable entry preference',
			supported: false,
			note: 'fills at next open; no order book'
		},
		{
			label: 'Entry wait and unfilled policy',
			supported: false,
			note: 'not modeled by the current backtester'
		},
		{ label: 'Trailing stop', supported: false, note: 'schema allows disabled only in V1' }
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

	function strategyId(): string {
		return page.params.id ?? '';
	}

	function markDirty(): void {
		dirty = true;
		validate(model);
	}

	function isComparison(condition: ConditionDraft): boolean {
		return 'operator' in condition;
	}

	function isGroup(condition: ConditionDraft): boolean {
		return 'all' in condition || 'any' in condition;
	}

	function isNot(condition: ConditionDraft): boolean {
		return 'not' in condition;
	}

	function conditionLabel(condition: ConditionDraft): string {
		if (isComparison(condition)) {
			const comparison = condition as {
				left: { indicator?: string; literal?: string };
				operator: string;
				right: { indicator?: string; literal?: string };
			};
			const left = comparison.left.indicator ?? comparison.left.literal ?? '?';
			const right = comparison.right.indicator ?? comparison.right.literal ?? '?';
			const symbol =
				operators.find((entry) => entry.value === comparison.operator)?.label ??
				comparison.operator;
			return `${left} ${symbol} ${right}`;
		}
		if (isGroup(condition)) {
			const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] };
			const children = group.all ?? group.any ?? [];
			const joiner = group.all ? ' AND ' : ' OR ';
			return children.map(conditionLabel).join(joiner);
		}
		return `NOT (${conditionLabel((condition as { not: ConditionDraft }).not)})`;
	}

	function addComparison(parent: { all?: ConditionDraft[]; any?: ConditionDraft[] }): void {
		const child: ConditionDraft = {
			left: { indicator: model?.indicators[0]?.id ?? 'fast' },
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

	function toggleNot(
		group: { all?: ConditionDraft[]; any?: ConditionDraft[] },
		index: number,
		parent: object
	): void {
		const child = (group.all ?? group.any ?? [])[index];
		if (!child) return;
		const wrapped: ConditionDraft = { not: child };
		if (group.all) group.all[index] = wrapped;
		if (group.any) group.any[index] = wrapped;
		void parent;
		markDirty();
	}

	function leftOperandKey(comparison: { left: { indicator?: string; literal?: string } }): string {
		return comparison.left.indicator !== undefined
			? `indicator:${comparison.left.indicator}`
			: 'literal';
	}

	function setLeftOperand(
		comparison: { left: { indicator?: string; literal?: string } },
		key: string
	): void {
		if (key === 'literal') {
			comparison.left = { literal: '0' };
		} else {
			comparison.left = { indicator: key.slice('indicator:'.length) };
		}
		markDirty();
	}

	function operandChoices(): { key: string; label: string }[] {
		const choices = (model?.indicators ?? []).map((indicator) => ({
			key: `indicator:${indicator.id}`,
			label: indicator.id
		}));
		choices.push({ key: 'literal', label: 'literal value' });
		return choices;
	}

	function validate(current: BuilderModel | null): void {
		if (current === null) {
			validationErrors = [];
			return;
		}
		const problems: string[] = [];
		if (current.name.trim().length === 0) problems.push('Name is required.');
		if (current.indicators.length === 0) problems.push('At least one indicator is required.');
		const ids = new Set(current.indicators.map((indicator) => indicator.id));
		if (ids.size !== current.indicators.length)
			problems.push('Indicator identifiers must be unique.');
		if (
			current.indicators.some(
				(indicator) => indicator.parameters.period < 2 || indicator.parameters.period > 500
			)
		) {
			problems.push('Indicator periods must be between 2 and 500 (RSI/ATR max 100).');
		}
		problems.push(...validateCondition(current.entry.when, ids));
		for (const field of ['risk_fraction', 'min_quote_notional', 'max_quote_notional'] as const) {
			if (!/^\d+(\.\d+)?$/.test(current.sizing[field]) || Number(current.sizing[field]) < 0) {
				problems.push(`Sizing ${field.replace('_', ' ')} must be a non-negative decimal.`);
			}
		}
		if (!/^\d*\.?\d+$/.test(current.portfolio_limits.max_strategy_exposure_fraction)) {
			problems.push('Max strategy exposure must be a non-negative decimal.');
		}
		if (
			current.exits.initial_stop.multiple !== '' &&
			Number(current.exits.initial_stop.multiple) <= 0
		) {
			problems.push('Initial stop multiple must be positive.');
		}
		if (
			current.exits.take_profit.multiple !== '' &&
			Number(current.exits.take_profit.multiple) <= 0
		) {
			problems.push('Take profit multiple must be positive.');
		}
		if (current.exits.time_exit.max_bars_held < 1)
			problems.push('Time exit must hold at least one bar.');
		if (current.warmup_bars < 1) problems.push('Warmup must be at least one bar.');
		validationErrors = problems;
	}

	function validateCondition(condition: ConditionDraft, ids: Set<string>): string[] {
		const problems: string[] = [];
		if (isComparison(condition)) {
			const comparison = condition as {
				left: { indicator?: string; literal?: string };
				right: { indicator?: string; literal?: string };
			};
			if (comparison.left.indicator !== undefined && !ids.has(comparison.left.indicator)) {
				problems.push(`Entry references unknown indicator "${comparison.left.indicator}".`);
			}
			if (comparison.right.indicator !== undefined && !ids.has(comparison.right.indicator)) {
				problems.push(`Entry references unknown indicator "${comparison.right.indicator}".`);
			}
			if (
				comparison.left.literal !== undefined &&
				!/^-?\d+(\.\d+)?$/.test(comparison.left.literal)
			) {
				problems.push('Entry literals must be exact decimal numbers.');
			}
			if (
				comparison.right.literal !== undefined &&
				!/^-?\d+(\.\d+)?$/.test(comparison.right.literal)
			) {
				problems.push('Entry literals must be exact decimal numbers.');
			}
			return problems;
		}
		if (isGroup(condition)) {
			const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] };
			const children = group.all ?? group.any ?? [];
			if (children.length === 0) problems.push('Empty condition groups are not allowed.');
			for (const child of children) problems.push(...validateCondition(child, ids));
			return problems;
		}
		return validateCondition((condition as { not: ConditionDraft }).not, ids);
	}

	async function load(): Promise<void> {
		loading = true;
		error = null;
		try {
			const id = strategyId();
			const [draftResponse, library] = await Promise.all([
				fetchDraftVersion(id, 1),
				listStrategies().catch(() => [])
			]);
			model = toBuilderModel(draftResponse.strategy, draftResponse.revision);
			const entry = library.find((candidate) => candidate.strategy_id === id);
			if (entry && entry.status !== 'draft' && entry.latest_fingerprint) {
				error =
					'This strategy identity is published or archived; its builder is read-only history.';
				model = null;
			}
			dirty = false;
			validate(model);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not load the strategy draft.';
			model = null;
		} finally {
			loading = false;
		}
	}

	async function save(): Promise<void> {
		if (!model || saving || validationErrors.length > 0) return;
		saving = true;
		error = null;
		try {
			const saved = await saveDraft(fromBuilderModel(model), model.revision);
			model = toBuilderModel(saved.strategy, saved.revision);
			dirty = false;
			savedAt = new Date().toLocaleTimeString();
			validate(model);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not save the strategy draft.';
		} finally {
			saving = false;
		}
	}

	async function publish(): Promise<void> {
		if (!model || publishing || validationErrors.length > 0) return;
		publishing = true;
		error = null;
		try {
			await publishDraft(fromBuilderModel(model), model.revision);
			await load();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Strategy publication failed.';
		} finally {
			publishing = false;
		}
	}

	function addIndicator(): void {
		if (!model) return;
		const kinds: IndicatorDraft['kind'][] = ['ema', 'sma', 'rsi', 'atr', 'volume_sma'];
		const next: IndicatorDraft = {
			id: `indicator_${model.indicators.length + 1}`,
			kind: 'sma',
			input: 'close',
			parameters: { period: 50 }
		};
		if (kinds.includes('ema') && model.indicators.length === 0) next.kind = 'ema';
		model.indicators.push(next);
		markDirty();
	}

	function removeIndicator(index: number): void {
		if (!model) return;
		model.indicators.splice(index, 1);
		markDirty();
	}

	function summaryText(): string {
		if (!model) return '';
		const entryText = conditionLabel(model.entry.when);
		return [
			`${model.name}: when ${entryText}, enter long on ${model.product_id} ${model.timeframe}.`,
			`Risk ${model.sizing.risk_fraction} of equity per trade between $${model.sizing.min_quote_notional} and $${model.sizing.max_quote_notional}.`,
			`Initial stop ${model.exits.initial_stop.multiple}× ATR, take profit at ${model.exits.take_profit.multiple}× risk, time exit after ${model.exits.time_exit.max_bars_held} bars.`
		].join(' ');
	}

	onMount(() => void load());
</script>

<svelte:head><title>Strategy builder · ThyTrader</title></svelte:head>

<div class="shell">
	<header class="topbar">
		<a class="brand" href={resolve('/')} aria-label="ThyTrader home"
			><span class="brand-mark">T</span><span>ThyTrader</span></a
		>
		<nav aria-label="Primary navigation">
			<a href={resolve('/')}>Portfolio</a>
			<a class="active" href={resolve('/strategies')}>Strategies</a>
			<a href={resolve('/backtests')}>Backtests</a>
			<a href={resolve('/audit')}>Audit</a>
		</nav>
		<div class="local-pill"><span></span> Research only</div>
	</header>
	<main>
		{#if loading}
			<div class="loading-card"><div class="skeleton wide"></div></div>
		{:else if error}
			<div class="error-banner" role="alert">
				<div>
					<strong>Builder unavailable</strong>
					<p>{error}</p>
				</div>
				<a class="secondary" href={resolve('/strategies')}>Back to library</a>
			</div>
		{:else if model}
			<section class="builder-head">
				<div>
					<p class="eyebrow">Draft v{model.version}</p>
					<h1>{model.name}</h1>
				</div>
				<div class="head-actions">
					{#if dirty}<span class="dirty-pill">Unsaved changes</span>{/if}
					{#if savedAt}<span class="saved">Saved {savedAt}</span>{/if}
					<button
						class="secondary"
						type="button"
						onclick={save}
						disabled={saving || validationErrors.length > 0}
						>{saving ? 'Saving…' : 'Save draft'}</button
					>
					<button
						class="refresh"
						type="button"
						onclick={publish}
						disabled={publishing || validationErrors.length > 0}
						>{publishing ? 'Publishing…' : 'Validate & publish immutable version'}</button
					>
				</div>
			</section>
			<div class="builder-grid">
				<div class="sections">
					<nav aria-label="Builder sections">
						{#each sections as section (section.id)}
							<button
								class="section-tab"
								class:active={activeSection === section.id}
								type="button"
								onclick={() => (activeSection = section.id)}>{section.label}</button
							>
						{/each}
					</nav>

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
								<input
									type="number"
									min="1"
									bind:value={model.warmup_bars}
									oninput={markDirty}
								/></label
							>
							<div class="hint">
								V1 is Coinbase USD spot, 1h candles, long-only. Other markets arrive with later
								runtimes.
							</div>
						</section>
					{:else if activeSection === 'indicators'}
						<section class="panel">
							<h2>Indicators</h2>
							{#each model.indicators as indicator, index (index)}
								<div class="indicator-row">
									<label>Id<input bind:value={indicator.id} oninput={markDirty} /></label>
									<label
										>Kind
										<select bind:value={indicator.kind} onchange={markDirty}>
											<option value="ema">EMA</option>
											<option value="sma">SMA</option>
											<option value="rsi">RSI</option>
											<option value="atr">ATR</option>
											<option value="volume_sma">Volume SMA</option>
										</select></label
									>
									<label
										>Period<input
											type="number"
											min="2"
											bind:value={indicator.parameters.period}
											oninput={markDirty}
										/></label
									>
									<button class="secondary" type="button" onclick={() => removeIndicator(index)}
										>Remove</button
									>
								</div>
							{/each}
							<button class="secondary" type="button" onclick={addIndicator}>Add indicator</button>
							<div class="hint">
								ATR uses high/low/close. RSI and ATR periods cap at 100. Inputs are fixed per kind
								in V1.
							</div>
						</section>
					{:else if activeSection === 'entry'}
						<section class="panel">
							<h2>Entry conditions</h2>
							<div class="rule-tree">
								{#if model.entry.when}
									{@render conditionNode(model.entry.when, model.entry.when, 0)}
								{/if}
							</div>
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
									>Initial stop — ATR multiple
									<input
										inputmode="decimal"
										bind:value={model.exits.initial_stop.multiple}
										oninput={markDirty}
									/></label
								>
								<label
									>Take profit — reward/risk multiple
									<input
										inputmode="decimal"
										bind:value={model.exits.take_profit.multiple}
										oninput={markDirty}
									/></label
								>
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
								<label
									>Trailing stop
									<input value="disabled (V1)" disabled /></label
								>
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
				</div>

				<aside class="inspector" aria-label="Strategy inspector">
					<h2>Inspector</h2>
					<div class="inspector-block">
						<h3>Plain-English summary</h3>
						<p>{summaryText()}</p>
					</div>
					<div class="inspector-block">
						<h3>Validation</h3>
						{#if validationErrors.length === 0}
							<p class="ok">No problems detected.</p>
						{:else}
							<ul class="problems">
								{#each validationErrors as problem (problem)}
									<li>{problem}</li>
								{/each}
							</ul>
						{/if}
					</div>
					<div class="inspector-block">
						<h3>Required data</h3>
						<p>{model.warmup_bars} completed 1h bars (OHLCV) before the first signal.</p>
					</div>
					<div class="inspector-block">
						<h3>Unsaved changes</h3>
						{#if dirty}<p class="warn">This draft has unsaved edits.</p>
						{:else}<p class="ok">All edits saved.</p>{/if}
					</div>
					<div class="inspector-block">
						<h3>Engine support (thytrader-bar-backtest-v1)</h3>
						<ul class="engine-list">
							{#each engineSupport as row (row.label)}
								<li class={row.supported ? 'supported' : 'unsupported'}>
									<span class="mark">{row.supported ? '✓' : '✗'}</span>
									<span>{row.label}<small>{row.note}</small></span>
								</li>
							{/each}
						</ul>
					</div>
				</aside>
			</div>
		{/if}
	</main>
</div>

{#snippet conditionNode(condition: ConditionDraft, parent: object, depth: number)}
	{@const index = childIndex(condition, parent)}
	<div class="rule-node" style="margin-left: {depth * 18}px">
		{#if isGroup(condition)}
			{@const group = condition as { all?: ConditionDraft[]; any?: ConditionDraft[] }}
			<div class="rule-group-head">
				<span class="group-kind">{group.all !== undefined ? 'ALL' : 'ANY'}</span>
				{#if depth > 0}
					<button class="secondary" type="button" onclick={() => removeChild(parent, index)}
						>Remove group</button
					>
				{/if}
				<button class="secondary" type="button" onclick={() => addComparison(group)}
					>+ comparison</button
				>
				<button class="secondary" type="button" onclick={() => addGroup(group, 'all')}>+ ALL</button
				>
				<button class="secondary" type="button" onclick={() => addGroup(group, 'any')}>+ ANY</button
				>
				<button class="secondary" type="button" onclick={() => toggleNot(group, index, parent)}
					>+ NOT</button
				>
			</div>
			{#each group.all ?? group.any ?? [] as child, childIdx (childIdx)}
				{@render conditionNode(child, group, depth + 1)}
			{/each}
		{:else if isNot(condition)}
			<div class="rule-group-head">
				<span class="group-kind">NOT</span>
				<button class="secondary" type="button" onclick={() => removeChild(parent, index)}
					>Remove</button
				>
			</div>
			{@render conditionNode((condition as { not: ConditionDraft }).not, condition, depth + 1)}
		{:else}
			{@const comparison = condition as {
				left: { indicator?: string; literal?: string };
				operator: string;
				right: { indicator?: string; literal?: string };
			}}
			<div class="rule-comparison">
				<select
					value={leftOperandKey(comparison)}
					onchange={(event) =>
						setLeftOperand(comparison, (event.currentTarget as HTMLSelectElement).value)}
				>
					{#each operandChoices() as choice (choice.key)}
						<option value={choice.key}>{choice.label}</option>
					{/each}
				</select>
				<select bind:value={comparison.operator} onchange={markDirty}>
					{#each operators as operator (operator.value)}
						<option value={operator.value}>{operator.label}</option>
					{/each}
				</select>
				<input
					class="literal"
					inputmode="decimal"
					value={comparison.right.literal ?? ''}
					oninput={(event) => {
						comparison.right = { literal: (event.currentTarget as HTMLInputElement).value };
						markDirty();
					}}
					placeholder="value"
				/>
				<button class="secondary" type="button" onclick={() => removeChild(parent, index)}>×</button
				>
			</div>
		{/if}
	</div>
{/snippet}

<style>
	.builder-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-end;
		gap: 16px;
		flex-wrap: wrap;
		margin-bottom: 16px;
	}
	.head-actions {
		display: flex;
		gap: 10px;
		align-items: center;
		flex-wrap: wrap;
	}
	.dirty-pill {
		color: #f0c987;
		font-size: 12px;
		border: 1px solid #5c4a2f;
		border-radius: 999px;
		padding: 3px 10px;
	}
	.saved {
		color: #83d5a3;
		font-size: 12px;
	}
	.builder-grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 340px;
		gap: 18px;
		align-items: start;
	}
	.sections nav {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		margin-bottom: 12px;
	}
	.section-tab {
		border: 1px solid #303a3c;
		background: transparent;
		color: #aeb9bb;
		border-radius: 999px;
		padding: 7px 13px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
	}
	.section-tab.active {
		background: #1d2b26;
		color: #9fe0bd;
		border-color: #2f5c44;
	}
	.panel {
		background: var(--card, #141b1c);
		border: 1px solid #303a3c;
		border-radius: 12px;
		padding: 20px 22px;
		display: grid;
		gap: 14px;
	}
	.panel h2 {
		margin: 0;
		font-size: 16px;
		color: #edf3f3;
	}
	label {
		display: grid;
		gap: 6px;
		color: #aeb9bb;
		font-size: 12px;
	}
	input,
	select,
	textarea {
		width: 100%;
		border: 1px solid #303a3c;
		border-radius: 8px;
		background: #101617;
		color: #edf3f3;
		padding: 9px 11px;
		font: inherit;
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
		color: #77888b;
		font-size: 12px;
	}
	.warn {
		color: #f0c987;
		font-size: 12px;
	}
	.ok {
		color: #83d5a3;
		font-size: 13px;
	}
	.indicator-row {
		display: grid;
		grid-template-columns: 1.2fr 1fr 0.8fr auto;
		gap: 10px;
		align-items: end;
	}
	.secondary {
		border: 1px solid #455457;
		border-radius: 8px;
		background: transparent;
		color: #d8e1e2;
		padding: 8px 11px;
		font: inherit;
		font-size: 12px;
		cursor: pointer;
	}
	.refresh {
		border: none;
		border-radius: 8px;
		background: #2f6f52;
		color: #eafff3;
		padding: 10px 14px;
		font: inherit;
		font-size: 13px;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.55;
		cursor: default;
	}
	.rule-tree {
		display: grid;
		gap: 8px;
	}
	.rule-node {
		border-left: 2px solid #2c3839;
		padding-left: 12px;
	}
	.rule-group-head {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
		margin-bottom: 6px;
	}
	.group-kind {
		font-weight: 700;
		color: #9fe0bd;
		letter-spacing: 0.08em;
		font-size: 12px;
	}
	.rule-comparison {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
	}
	.rule-comparison select,
	.rule-comparison .literal {
		width: auto;
		min-width: 110px;
	}
	.cooldown-row {
		margin-top: 6px;
	}
	.inspector {
		background: var(--card, #141b1c);
		border: 1px solid #303a3c;
		border-radius: 12px;
		padding: 18px 20px;
		display: grid;
		gap: 14px;
		position: sticky;
		top: 16px;
	}
	.inspector h2 {
		margin: 0;
		font-size: 14px;
		color: #edf3f3;
		text-transform: uppercase;
		letter-spacing: 0.08em;
	}
	.inspector-block h3 {
		margin: 0 0 6px;
		font-size: 12px;
		color: #aeb9bb;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.inspector-block p {
		margin: 0;
		font-size: 13px;
		color: #d8e1e2;
	}
	.problems {
		margin: 0;
		padding-left: 16px;
		color: #f0a3a3;
		font-size: 12px;
		display: grid;
		gap: 4px;
	}
	.engine-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 8px;
	}
	.engine-list li {
		display: flex;
		gap: 8px;
		font-size: 12px;
		color: #d8e1e2;
	}
	.engine-list li .mark {
		font-weight: 700;
	}
	.engine-list li.supported .mark {
		color: #83d5a3;
	}
	.engine-list li.unsupported {
		color: #9aa8aa;
	}
	.engine-list li.unsupported .mark {
		color: #f0a3a3;
	}
	.engine-list small {
		display: block;
		color: #77888b;
	}
	@media (max-width: 900px) {
		.builder-grid {
			grid-template-columns: 1fr;
		}
		.inspector {
			position: static;
		}
		/* Keep the inspector readable without scrolling past the whole form. */
		.builder-grid .inspector {
			order: -1;
		}
	}
</style>
