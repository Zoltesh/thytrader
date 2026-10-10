<script lang="ts">
	/**
	 * Start-paper form of a futures strategy (ADR 0129): futures run as paper books
	 * only. Starting cash (USD), maker and taker fee rates and the fee per contract
	 * (USD) are all required; the server refuses a futures start without them. The
	 * fields prefill from the futures fee evidence of the operator `fees` report
	 * when Coinbase reported it, never from an invented default. "Quote from Coinbase"
	 * asks the server for one contract's `orders/preview` (it places no order).
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { createDeployment, type Deployment } from '$lib/deployments';
	import { SHARED_COLLATERAL_NOTE } from '$lib/futures-book';
	import { fetchFuturesFeeEvidence, type FuturesFeeEvidence } from '$lib/futures-book-api';
	import {
		futuresFeeSourceText,
		futuresStartMissing,
		prefillFuturesFees,
		type FuturesStartFields
	} from '$lib/workspace/futures-start';
	import type { BuilderModel } from '$lib/strategies';

	let {
		strategyId,
		currentFingerprint,
		name,
		model,
		disabled = false,
		onStarted
	}: {
		strategyId: string;
		currentFingerprint: string;
		name: string;
		model: BuilderModel;
		disabled?: boolean;
		onStarted: (deployment: Deployment) => void;
	} = $props();

	let fields = $state<FuturesStartFields>({ cash: '', maker: '', taker: '', perContract: '' });
	let evidence = $state<FuturesFeeEvidence | null>(null);
	let evidenceState = $state<'loading' | 'ready' | 'error'>('loading');
	let quoting = $state(false);
	let quoteError = $state<string | null>(null);
	let confirmOpen = $state(false);
	let starting = $state(false);
	let error = $state<string | null>(null);
	let requestId = 0;

	const missing = $derived(futuresStartMissing(fields));

	$effect(() => {
		void strategyId;
		void loadEvidence();
	});

	async function loadEvidence(previewProductId?: string): Promise<void> {
		const id = ++requestId;
		try {
			const loaded = await fetchFuturesFeeEvidence(previewProductId);
			if (id !== requestId) return;
			evidence = loaded;
			evidenceState = 'ready';
			fields = prefillFuturesFees(fields, loaded, previewProductId !== undefined);
		} catch (caught) {
			if (id !== requestId) return;
			if (previewProductId === undefined) evidenceState = 'error';
			else quoteError = caught instanceof Error ? caught.message : 'The quote failed.';
		}
	}

	async function quote(): Promise<void> {
		if (quoting) return;
		quoting = true;
		quoteError = null;
		try {
			await loadEvidence(model.product_id);
			if (evidence?.fee_per_contract === null && evidence.preview_unavailable_reason) {
				quoteError = `Coinbase could not quote it (${evidence.preview_unavailable_reason}).`;
			}
		} finally {
			quoting = false;
		}
	}

	async function start(): Promise<void> {
		if (starting || missing.length > 0) return;
		starting = true;
		error = null;
		try {
			const deployment = await createDeployment({
				strategy_id: strategyId,
				mode: 'paper',
				paper_starting_cash: fields.cash.trim(),
				maker_fee_rate: fields.maker.trim(),
				taker_fee_rate: fields.taker.trim(),
				paper_fee_per_contract: fields.perContract.trim()
			});
			confirmOpen = false;
			onStarted(deployment);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not start the futures book.';
		} finally {
			starting = false;
		}
	}
</script>

<div class="start-form" data-testid="futures-start-form">
	<p class="note">
		Futures run as paper books only. Every amount is USD and is never added to USDC.
	</p>
	<div class="fields">
		<label
			>Paper starting cash (USD)
			<input bind:value={fields.cash} inputmode="decimal" placeholder="Required" /></label
		>
		<label
			>Maker fee rate
			<input bind:value={fields.maker} inputmode="decimal" placeholder="Required" /></label
		>
		<label
			>Taker fee rate
			<input bind:value={fields.taker} inputmode="decimal" placeholder="Required" /></label
		>
		<label
			>Fee per contract (USD)
			<input bind:value={fields.perContract} inputmode="decimal" placeholder="Required" /></label
		>
	</div>
	<p class="note" data-testid="futures-fee-source">
		{futuresFeeSourceText(evidence, evidenceState)}
		<button type="button" class="link" disabled={quoting} onclick={() => void quote()}
			>{quoting ? 'Quoting…' : 'Quote fee per contract from Coinbase'}</button
		>
	</p>
	{#if quoteError}<p class="warn" role="status">{quoteError}</p>{/if}
	{#if missing.length > 0}
		<p class="note" data-testid="futures-start-missing">Required: {missing.join(', ')}.</p>
	{/if}
	<button
		class="btn primary"
		type="button"
		disabled={disabled || currentFingerprint === '' || missing.length > 0}
		onclick={() => (confirmOpen = true)}>Start paper futures book…</button
	>
</div>

<ConfirmDialog
	open={confirmOpen}
	title="Start paper futures book?"
	confirmLabel="Start paper futures book"
	pendingLabel="Starting…"
	pending={starting}
	{error}
	testId="futures-start-dialog"
	oncancel={() => (confirmOpen = false)}
	onconfirm={() => void start()}
>
	<p><strong>{name}</strong> · current rules · Paper futures</p>
	<div class="row"><span>Contract</span><span>{model.product_id} · {model.timeframe}</span></div>
	<div class="row"><span>Starting cash</span><span>{fields.cash} USD</span></div>
	<div class="row">
		<span>Fee assumptions</span><span
			>maker {fields.maker} · taker {fields.taker} · {fields.perContract} USD per contract</span
		>
	</div>
	<div class="row"><span>Rules snapshot</span><code class="fp">{currentFingerprint}</code></div>
	<p>
		This starts a simulated futures book of the current saved rules. It places no Coinbase orders;
		there is no live futures path. {SHARED_COLLATERAL_NOTE}
	</p>
</ConfirmDialog>

<style>
	.start-form {
		display: grid;
		gap: 10px;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
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
	.warn {
		margin: 0;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.link {
		padding: 0;
		border: 0;
		background: none;
		color: var(--accent);
		font: inherit;
		cursor: pointer;
		text-decoration: underline;
	}
	.link:disabled {
		color: var(--faint);
		cursor: progress;
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
