<script lang="ts">
	/**
	 * Start-paper form for the Run stage (the start half of the former
	 * DeployWorkstation). Paper starting cash plus maker/taker fee
	 * assumptions, prefilled from the Coinbase fee-tier suggestion unless the
	 * operator edited them. Starting is a mutation behind a confirmation, and
	 * it starts a new deployment of the strategy's current rules (the server
	 * snapshots them): it is not a promotion of any backtest.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { marketLabel, productIdQuote } from '$lib/deployment-detail';
	import { createDeployment, type Deployment } from '$lib/deployments';
	import {
		PAPER_DEFAULT_MAKER_FEE_RATE,
		PAPER_DEFAULT_TAKER_FEE_RATE,
		PAPER_FEE_ENGINE_NOTE,
		fetchFeeProfile,
		readResearchFeeSuggestion,
		shouldPrefillPaperFeeRates
	} from '$lib/fees';
	import { extraIndicatorTimeframes, type BuilderModel } from '$lib/strategies';

	let {
		strategyId,
		currentFingerprint,
		name,
		model,
		disabled = false,
		onStarted
	}: {
		strategyId: string;
		/** Fingerprint the start snapshot gets (the current valid definition). */
		currentFingerprint: string;
		name: string;
		model: BuilderModel;
		/** Blocks starting (for example while a lifecycle outcome is unknown). */
		disabled?: boolean;
		onStarted: (deployment: Deployment) => void;
	} = $props();

	let cash = $state('10000');
	let makerFee = $state(PAPER_DEFAULT_MAKER_FEE_RATE);
	let takerFee = $state(PAPER_DEFAULT_TAKER_FEE_RATE);
	let feesTouched = $state(false);
	let confirmOpen = $state(false);
	let starting = $state(false);
	let error = $state<string | null>(null);
	let feeRequestId = 0;

	const quote = $derived(productIdQuote(model.product_id) ?? 'quote');
	const extraTimeframes = $derived(extraIndicatorTimeframes(model.indicators, model.timeframe));

	$effect(() => {
		void strategyId;
		void loadFeeSuggestion();
	});

	async function loadFeeSuggestion(): Promise<void> {
		const requestId = ++feeRequestId;
		try {
			const profile = await fetchFeeProfile();
			if (requestId !== feeRequestId) return;
			const suggestion = readResearchFeeSuggestion(profile);
			if (
				suggestion !== null &&
				shouldPrefillPaperFeeRates({
					makerFeeRate: makerFee,
					takerFeeRate: takerFee,
					touched: feesTouched,
					suggestion
				})
			) {
				makerFee = suggestion.makerFeeRate;
				takerFee = suggestion.takerFeeRate;
			}
		} catch {
			/* documented 0.001 / 0.002 defaults remain */
		}
	}

	async function start(): Promise<void> {
		if (starting) return;
		starting = true;
		error = null;
		try {
			const deployment = await createDeployment({
				strategy_id: strategyId,
				mode: 'paper',
				paper_starting_cash: cash,
				maker_fee_rate: makerFee,
				taker_fee_rate: takerFee
			});
			confirmOpen = false;
			onStarted(deployment);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not start the deployment.';
		} finally {
			starting = false;
		}
	}
</script>

<div class="start-form" data-testid="paper-start-form">
	<div class="fields">
		<label
			>Paper starting cash ({quote})
			<input bind:value={cash} inputmode="decimal" /></label
		>
		<label
			>Maker fee rate
			<input
				inputmode="decimal"
				bind:value={makerFee}
				oninput={() => (feesTouched = true)}
			/></label
		>
		<label
			>Taker fee rate
			<input
				inputmode="decimal"
				bind:value={takerFee}
				oninput={() => (feesTouched = true)}
			/></label
		>
	</div>
	<p class="note">{PAPER_FEE_ENGINE_NOTE}</p>
	{#if model.htf_filter}
		<p class="note">
			These rules AND last-completed {model.htf_filter.timeframe} HTF bars with LTF entry. Paper and live
			load live complete-only HTF candles; missing coverage pauses.
		</p>
	{/if}
	{#if extraTimeframes.length > 0}
		<p class="note">
			These rules also evaluate last-completed {extraTimeframes.join(', ')} indicator bars. Paper and
			live load those complete-only candles; missing coverage pauses.
		</p>
	{/if}
	<button
		class="btn primary"
		type="button"
		disabled={disabled || currentFingerprint === ''}
		onclick={() => (confirmOpen = true)}>Start paper deployment…</button
	>
</div>

<ConfirmDialog
	open={confirmOpen}
	title="Start paper deployment?"
	confirmLabel="Start paper deployment"
	pendingLabel="Starting…"
	pending={starting}
	{error}
	testId="paper-start-dialog"
	oncancel={() => (confirmOpen = false)}
	onconfirm={() => void start()}
>
	<p><strong>{name}</strong> · current rules · Paper</p>
	<div class="row">
		<span>Market</span><span>{marketLabel(model.product_id)} · {model.timeframe}</span>
	</div>
	<div class="row"><span>Starting cash</span><span>{cash} {quote}</span></div>
	<div class="row">
		<span>Fee assumptions</span><span>maker {makerFee} · taker {takerFee}</span>
	</div>
	<div class="row"><span>Rules snapshot</span><code class="fp">{currentFingerprint}</code></div>
	<p>
		This starts a new simulated deployment of the current saved rules. Later edits do not change it.
		It is not a promotion of any backtest, and it places no Coinbase orders.
	</p>
</ConfirmDialog>

<style>
	.start-form {
		display: grid;
		gap: 10px;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 10px;
	}
	label {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	input {
		min-height: 34px;
		padding: 6px 9px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	.note {
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.btn {
		justify-self: start;
	}
	.fp {
		font-size: var(--fs-xs);
		word-break: break-all;
	}
	@media (max-width: 720px) {
		.fields {
			grid-template-columns: 1fr;
		}
	}
</style>
