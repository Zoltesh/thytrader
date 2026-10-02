<script lang="ts">
	/**
	 * "New portfolio…" dialog: name, mode, quote currency, capital, cash reserve.
	 *
	 * Mode and quote currency are fixed once the portfolio exists. A live
	 * portfolio places no orders in this release; the dialog says so.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import { compareDecimalStrings } from '$lib/portfolio';
	import {
		QUOTE_CURRENCIES,
		createPortfolio,
		errorText,
		percentInputToFraction,
		quoteInput,
		type Portfolio,
		type PortfolioMode,
		type QuoteCurrency
	} from '$lib/portfolios';

	let {
		open,
		oncancel,
		oncreated
	}: {
		open: boolean;
		oncancel: () => void;
		oncreated: (portfolio: Portfolio) => void;
	} = $props();

	let name = $state('');
	let mode = $state<PortfolioMode>('paper');
	let quote = $state<QuoteCurrency>('USDC');
	let capital = $state('');
	let reserve = $state('0');
	let pending = $state(false);
	let error = $state<string | null>(null);

	const capitalValue = $derived(quoteInput(capital));
	const reserveValue = $derived(percentInputToFraction(reserve));
	const problem = $derived.by((): string | null => {
		if (name.trim() === '') return 'Name the portfolio.';
		if (name.trim().length > 120) return 'Names are at most 120 characters.';
		if (capitalValue === null) return 'Capital must be a positive amount (up to 8 decimals).';
		if (reserveValue === null || compareDecimalStrings(reserveValue, '1') >= 0)
			return 'Cash reserve is a percent from 0 to below 100 (up to 2 decimals).';
		return null;
	});

	$effect(() => {
		if (!open) return;
		name = '';
		mode = 'paper';
		quote = 'USDC';
		capital = '';
		reserve = '0';
		error = null;
		pending = false;
	});

	async function submit(): Promise<void> {
		if (problem !== null || capitalValue === null || reserveValue === null) return;
		pending = true;
		error = null;
		try {
			const created = await createPortfolio({
				name: name.trim(),
				mode,
				quote_currency: quote,
				capital_quote: capitalValue,
				cash_reserve_fraction: reserveValue
			});
			oncreated(created);
		} catch (caught) {
			error = errorText(caught, 'The portfolio could not be created.');
		} finally {
			pending = false;
		}
	}
</script>

<ConfirmDialog
	{open}
	title="New portfolio"
	confirmLabel="Create portfolio"
	pendingLabel="Creating…"
	{pending}
	confirmDisabled={problem !== null}
	confirmDisabledReason={problem}
	{error}
	testId="new-portfolio-dialog"
	{oncancel}
	onconfirm={() => void submit()}
>
	<p>
		A portfolio holds sleeves (one strategy each, with a capital weight) under shared limits. Its
		mode and quote currency are fixed once it exists.
	</p>
	<label class="field">
		<span>Name</span>
		<input type="text" bind:value={name} maxlength="120" placeholder="Core" autocomplete="off" />
	</label>
	<div class="field">
		<span>Mode</span>
		<Segmented
			label="Portfolio mode"
			options={[
				{ id: 'paper', label: 'Paper' },
				{ id: 'live', label: 'Live', live: true }
			]}
			value={mode}
			onchange={(next) => (mode = next)}
			testId="new-portfolio-mode"
		/>
	</div>
	{#if mode === 'live'}
		<p class="live-note" data-testid="new-portfolio-live-note">
			A live portfolio is for real Coinbase capital, but nothing trades yet: deploying a portfolio
			arrives next. Creating it places no orders.
		</p>
	{/if}
	<div class="row-fields">
		<label class="field">
			<span>Quote currency</span>
			<select bind:value={quote}>
				{#each QUOTE_CURRENCIES as option (option)}
					<option value={option}>{option}</option>
				{/each}
			</select>
		</label>
		<label class="field">
			<span>Capital ({quote})</span>
			<input type="text" inputmode="decimal" bind:value={capital} placeholder="1000" />
		</label>
		<label class="field">
			<span>Cash reserve (%)</span>
			<input type="text" inputmode="decimal" bind:value={reserve} placeholder="0" />
		</label>
	</div>
</ConfirmDialog>

<style>
	.field {
		display: grid;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.field input,
	.field select {
		min-height: 34px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
	}
	.row-fields {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 10px;
	}
	.live-note {
		padding: 10px 12px;
		border: 1px solid var(--live-line);
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
	}
	@media (max-width: 560px) {
		.row-fields {
			grid-template-columns: 1fr;
		}
	}
</style>
