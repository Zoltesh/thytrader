<script lang="ts">
	/**
	 * Futures settings of a CFM contract strategy (ADR 0128), shown under Market and
	 * data when the instrument kind is futures: the contract's underlying (the base
	 * currency the catalog confirms when the contract is bound), the strategy's
	 * maximum leverage and its margin mode. `flatten_before_expiry_hours` is kept as
	 * saved and shown read-only: paper books do not run the dated-contract flatten.
	 */
	import { MAX_STRATEGY_LEVERAGE, MIN_STRATEGY_LEVERAGE, type BuilderModel } from '$lib/strategies';

	let {
		model = $bindable(),
		onchange
	}: {
		model: BuilderModel;
		/** Called after every edit. */
		onchange: () => void;
	} = $props();

	function setUnderlying(raw: string): void {
		model.base_currency = raw.trim().toUpperCase();
		onchange();
	}

	function setLeverage(raw: string): void {
		if (model.derivatives === null) return;
		model.derivatives.max_leverage = raw.trim();
		onchange();
	}
</script>

{#if model.derivatives !== null}
	<div class="futures-block" data-testid="futures-fields">
		<div class="grid-three">
			<label
				>Underlying
				<input
					value={model.base_currency}
					spellcheck="false"
					autocomplete="off"
					placeholder="ETH"
					oninput={(event) => setUnderlying(event.currentTarget.value)}
				/></label
			>
			<label
				>Maximum leverage
				<input
					inputmode="decimal"
					value={model.derivatives.max_leverage}
					oninput={(event) => setLeverage(event.currentTarget.value)}
				/></label
			>
			<label
				>Margin mode
				<select aria-label="Margin mode" bind:value={model.derivatives.margin_mode} {onchange}>
					<option value="overnight">Overnight</option>
				</select></label
			>
		</div>
		{#if model.derivatives.flatten_before_expiry_hours !== null}
			<p class="hint" data-testid="futures-flatten">
				Flatten before expiry: {model.derivatives.flatten_before_expiry_hours} h (kept as saved; paper
				books do not run the dated-contract flatten).
			</p>
		{/if}
		<p class="hint">
			The underlying is the contract's root unit from the futures catalog (BIP settles on BTC, ETP
			on ETH); a backtest or paper start refuses a mismatch. Leverage is this strategy's ceiling ({MIN_STRATEGY_LEVERAGE}
			to {MAX_STRATEGY_LEVERAGE}); the risk policy's ceiling applies too and the lower wins. Sizing
			and admission use overnight margin rates only.
		</p>
	</div>
{/if}

<style>
	.futures-block {
		display: grid;
		gap: 10px;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.grid-three {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 14px;
	}
	label {
		display: grid;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	input,
	select {
		width: 100%;
		min-height: 34px;
		padding: 7px 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
		font: inherit;
	}
	.hint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	@media (max-width: 640px) {
		.grid-three {
			grid-template-columns: 1fr;
		}
	}
</style>
