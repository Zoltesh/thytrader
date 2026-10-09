<script lang="ts">
	/**
	 * Paper assumptions for a paper trade ticket: starting cash and optional
	 * maker / taker fee rates (blank uses the account rate), with the fee-engine
	 * note.
	 */
	import { PAPER_FEE_ENGINE_NOTE } from '$lib/fees';

	let {
		paperCash = $bindable(),
		paperMakerFee = $bindable(),
		paperTakerFee = $bindable()
	}: {
		paperCash: string;
		paperMakerFee: string;
		paperTakerFee: string;
	} = $props();
</script>

<fieldset class="paper-fields">
	<legend>Paper assumptions</legend>
	<div class="fields">
		<label>
			Paper cash
			<input bind:value={paperCash} required inputmode="decimal" />
		</label>
		<label>
			Maker fee rate
			<input bind:value={paperMakerFee} placeholder="Account rate" inputmode="decimal" />
		</label>
		<label>
			Taker fee rate
			<input bind:value={paperTakerFee} placeholder="Account rate" inputmode="decimal" />
		</label>
	</div>
	<p class="hint">{PAPER_FEE_ENGINE_NOTE}</p>
</fieldset>

<style>
	.fields {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 14px;
	}
	label {
		color: var(--muted);
		font-size: var(--fs-sm);
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	input {
		min-height: 36px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	.paper-fields {
		margin: 0;
		padding: 10px 12px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
	}
	.paper-fields legend {
		padding: 0 4px;
		color: var(--faint);
		font-size: var(--fs-xs);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.hint {
		margin: 8px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
</style>
