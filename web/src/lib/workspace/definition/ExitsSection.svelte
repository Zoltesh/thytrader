<script lang="ts">
	/**
	 * Exit conditions and protective stops: the ATR initial stop, reward/risk
	 * take-profit or none, time exit, the read-only trailing-stop summary, the
	 * optional economic guard, and the optional "Exit when" rule (ADR 0093).
	 */
	import { defaultSignalExit, type BuilderModel } from '$lib/strategies';
	import RuleTree from './RuleTree.svelte';

	let {
		model = $bindable(),
		lastTakeProfitMultiple = $bindable(),
		readonly,
		onchange
	}: {
		model: BuilderModel;
		/**
		 * Last reward/risk multiple, restored when the operator switches back from
		 * `none`. Owned by the form so it survives switching sections.
		 */
		lastTakeProfitMultiple: string;
		readonly: boolean;
		/** Called after every edit. */
		onchange: () => void;
	} = $props();

	function markDirty(): void {
		onchange();
	}

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
</script>

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
				No take-profit order rests. Paper enforces the stop on closed bars; live rests a Coinbase
				stop-limit at the stop.
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
				onchange={(event) => toggleSignalExit((event.currentTarget as HTMLInputElement).checked)}
			/>
			Exit when a rule matches (optional)
		</label>
		{#if model.exits.signal_exit}
			<h3 class="exit-rule-title">Exit when</h3>
			<div class="rule-tree" data-testid="signal-exit-tree">
				<RuleTree
					bind:model
					root={model.exits.signal_exit.when}
					indicators={model.indicators}
					kind="exit"
					{onchange}
				/>
			</div>
		{/if}
		<p class="hint">
			Checked on every closed bar after the fill bar while a position is open, using the same
			indicators as entry. A match sells at that bar's close as a taker, like the time exit. The
			initial stop still guards the position until then: when a bar trades through the stop, the
			stop exit wins. The trailing stop, take-profit, and time exit still apply; whichever triggers
			first closes the position.
		</p>
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
	.grid-two {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 14px;
	}
	.hint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.field {
		display: grid;
		gap: 6px;
		align-content: start;
		min-width: 0;
	}
	.rule-tree {
		display: grid;
		gap: 8px;
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
	@media (max-width: 640px) {
		.grid-two {
			grid-template-columns: 1fr;
		}
	}
</style>
