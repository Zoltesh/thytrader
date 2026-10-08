<script lang="ts">
	/**
	 * One ALL / ANY / NOT rule tree of the definition form (entry, the HTF
	 * filter, or the optional "Exit when" rule), rendered recursively from its
	 * root. Groups add comparisons, nested groups, and negated rows; the root
	 * group itself can be negated. Every edit mutates the bound model in place
	 * and calls `onchange`.
	 */
	import { MAX_INDICATOR_OFFSET } from '$lib/indicator-catalog';
	import {
		defaultIndicatorOperand,
		parseIndicatorOperandKey,
		type BuilderModel,
		type ConditionDraft,
		type IndicatorDraft
	} from '$lib/strategies';
	import {
		CONDITION_OPERATORS,
		childIndex,
		isGroup,
		isNot,
		isRootGroup,
		leftOperandKey,
		operandLabel,
		operandOptionBlocks,
		rightOperandKey,
		rowKeyword
	} from './rule-tree';

	let {
		model = $bindable(),
		root,
		indicators,
		kind,
		onchange
	}: {
		model: BuilderModel;
		/** The tree's root condition (`model.entry.when`, the HTF or exit `when`). */
		root: ConditionDraft;
		/** Indicators the tree's operands may reference. */
		indicators: IndicatorDraft[];
		kind: 'entry' | 'htf' | 'exit';
		/** Called after every edit. */
		onchange: () => void;
	} = $props();

	function markDirty(): void {
		onchange();
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
</script>

{@render conditionNode(root, root, 0, root, indicators, kind)}

{#snippet operandOptions(indicators: IndicatorDraft[])}
	{#each operandOptionBlocks(indicators, model.reference_instruments) as block (block.key)}
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
					{#each CONDITION_OPERATORS as operator (operator.value)}
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
	.rule-comparison input.literal {
		width: 96px;
		min-width: 72px;
	}
</style>
