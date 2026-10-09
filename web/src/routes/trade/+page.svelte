<script lang="ts">
	/**
	 * Trade (`/trade`): one-off long/short ticket with SL/TP over the same
	 * intent → risk → broker path as `thytrader-runtime place-order --confirm`.
	 *
	 * Form left, Review aside right. Live mode turns on the shell's live
	 * chrome, and a live submit goes through the live confirmation dialog whose
	 * confirm stays disabled until "I understand this places real orders" is
	 * ticked; only then is `i_understand_live: true` sent.
	 *
	 * This page owns the ticket state and the order submission; the Review aside,
	 * the live confirmation and the outcome panels render in `$lib/trade`. The
	 * "Adopt holdings" order type (live only, ADR 0124) hands the ticket to
	 * `TradeAdopt`, which buys nothing and owns its own preview and confirmation.
	 */
	import { onMount } from 'svelte';
	import PageHead from '$lib/PageHead.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import TradeReasonReview from '$lib/TradeReasonReview.svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import { listDeployments, placeDiscretionaryOrder, type Deployment } from '$lib/deployments';
	import { optionalFeeRate } from '$lib/fees';
	import { declareLiveContext } from '$lib/live-context.svelte';
	import { fetchTradeReasons, type TradeReasonRecord } from '$lib/memory';
	import { EXECUTION_TIMEFRAMES, type ExecutionTimeframe } from '$lib/strategies';
	import type { RiskPolicySnapshot } from '$lib/strategy-workspace';
	import PaperAssumptions from '$lib/trade/PaperAssumptions.svelte';
	import TradeAdopt from '$lib/trade/TradeAdopt.svelte';
	import TradeBooks from '$lib/trade/TradeBooks.svelte';
	import TradeLiveDialog from '$lib/trade/TradeLiveDialog.svelte';
	import TradeReview from '$lib/trade/TradeReview.svelte';
	import { tradeReview } from '$lib/trade-review';
	import { fetchRiskPolicySnapshot } from '$lib/workspace-data';

	let productId = $state('BTC-USD');
	let mode = $state<'paper' | 'live'>('paper');
	let side = $state<'long' | 'short'>('long');
	let timeframe = $state<ExecutionTimeframe>('5m');
	let entryKind = $state<'post_only_limit' | 'marketable' | 'adopt'>('post_only_limit');
	let limitPrice = $state('');
	let quantity = $state('');
	let quoteNotional = $state('');
	let stopPrice = $state('');
	let takeProfitPrice = $state('');
	let paperCash = $state('10000');
	let paperMakerFee = $state('');
	let paperTakerFee = $state('');
	let note = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);
	let result = $state<Deployment | null>(null);
	let books = $state<Deployment[]>([]);
	let booksError = $state<string | null>(null);
	let tradeReasons = $state<TradeReasonRecord[]>([]);
	let hydrated = $state(false);
	let riskPolicy = $state<RiskPolicySnapshot | 'unknown' | null>(null);

	let liveConfirmOpen = $state(false);
	let liveAcknowledged = $state(false);

	const isLive = $derived(mode === 'live');
	const adopting = $derived(entryKind === 'adopt');
	const review = $derived(
		tradeReview({
			productId,
			side,
			entryKind: entryKind === 'adopt' ? 'marketable' : entryKind,
			limitPrice,
			quantity,
			quoteNotional,
			stopPrice,
			takeProfitPrice
		})
	);
	const market = $derived(/-/.test(productId) ? marketLabel(productId.trim()) : productId);
	const sizeText = $derived(
		quantity !== ''
			? `${quantity} ${productId.split('-')[0] ?? ''}`.trim()
			: quoteNotional !== ''
				? `${quoteNotional} ${productId.split('-')[1] ?? 'quote'}`
				: 'Not set'
	);

	$effect(() => {
		// Composing a live order: the shell shows the amber strip and inset frame.
		if (!isLive) return;
		return declareLiveContext({ kind: 'order', productId, cap: null });
	});

	async function refreshBooks(): Promise<void> {
		try {
			const deployments = await listDeployments();
			books = deployments.filter((item) => item.kind === 'discretionary');
			booksError = null;
		} catch (caught) {
			books = [];
			booksError =
				caught instanceof Error ? caught.message : 'Discretionary book inventory is incomplete.';
		}
	}

	async function refreshReasons(deploymentId?: string): Promise<void> {
		try {
			tradeReasons = await fetchTradeReasons(
				deploymentId === undefined ? undefined : { deploymentId }
			);
		} catch {
			tradeReasons = [];
		}
	}

	async function refreshRiskPolicy(): Promise<void> {
		try {
			riskPolicy = await fetchRiskPolicySnapshot();
		} catch {
			riskPolicy = 'unknown';
		}
	}

	/** Client-side checks the ticket always had; the server validates everything again. */
	function validationError(): string | null {
		if ((quantity === '') === (quoteNotional === '')) {
			return 'Provide exactly one of quantity or quote notional.';
		}
		if (entryKind === 'adopt') return 'Use Review adoption to adopt held coins.';
		if (entryKind === 'post_only_limit' && limitPrice === '') {
			return 'Post-only entries require a limit price.';
		}
		return null;
	}

	async function onAdopted(deployment: Deployment): Promise<void> {
		result = deployment;
		await refreshBooks();
		await refreshReasons(deployment.id);
	}

	function onSubmitClick(): void {
		if (adopting) return;
		error = null;
		const invalid = validationError();
		if (invalid !== null) {
			error = invalid;
			return;
		}
		if (isLive) {
			liveAcknowledged = false;
			liveConfirmOpen = true;
			return;
		}
		void submitOrder(false);
	}

	async function submitOrder(liveConfirmed: boolean): Promise<void> {
		if (isLive && !liveConfirmed) return;
		submitting = true;
		error = null;
		result = null;
		try {
			const invalid = validationError();
			if (invalid !== null) throw new Error(invalid);
			const trimmedNote = note.trim();
			result = await placeDiscretionaryOrder({
				mode,
				product_id: productId,
				side,
				stop_price: stopPrice,
				take_profit_price: takeProfitPrice,
				idempotency_key: crypto.randomUUID(),
				origin: 'human',
				entry_kind: entryKind === 'adopt' ? undefined : entryKind,
				timeframe,
				quantity: quantity === '' ? undefined : quantity,
				quote_notional: quoteNotional === '' ? undefined : quoteNotional,
				limit_price: limitPrice === '' ? undefined : limitPrice,
				paper_starting_cash: mode === 'paper' ? paperCash : undefined,
				maker_fee_rate: mode === 'paper' ? optionalFeeRate(paperMakerFee) : undefined,
				taker_fee_rate: mode === 'paper' ? optionalFeeRate(paperTakerFee) : undefined,
				note: trimmedNote === '' ? undefined : trimmedNote,
				// Reached for live only after the dialog's ticked "real orders" checkbox.
				i_understand_live: mode === 'live' && liveConfirmed ? true : undefined
			});
			liveConfirmOpen = false;
			await refreshBooks();
			await refreshReasons(result.id);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'The order could not be placed.';
		} finally {
			submitting = false;
		}
	}

	onMount(() => {
		hydrated = true;
		void refreshBooks();
		void refreshReasons();
		void refreshRiskPolicy();
	});
</script>

<svelte:head>
	<title>Trade · ThyTrader</title>
</svelte:head>

<main>
	<PageHead title="Trade">
		{#snippet intro()}
			<p class="lede">
				One-off long or short with a stop loss and take profit. It goes through the same risk checks
				as bots (the same intent → risk → broker path as
				<code>thytrader-runtime place-order --confirm</code>), and ThyTrader never borrows: live
				shorts need available base. Timeouts are reconciled, never retried. The optional note is
				frozen onto the why-trade record.
			</p>
		{/snippet}
	</PageHead>

	<div class="trade-grid">
		<form
			class="card ticket"
			data-testid="discretionary-ticket"
			data-hydrated={hydrated ? 'true' : 'false'}
			onsubmit={(event) => {
				event.preventDefault();
				onSubmitClick();
			}}
		>
			<div class="field">
				<span class="field-label" aria-hidden="true">Mode</span>
				<Segmented
					label="Mode"
					options={[
						{ id: 'paper', label: 'Paper' },
						{ id: 'live', label: 'Live', live: true }
					]}
					value={mode}
					onchange={(next) => (mode = next)}
					testId="trade-mode"
				/>
				{#if isLive}
					<p class="live-note">
						<span class="chip live">LIVE</span> Real Coinbase spot order with your API keys. Live needs
						Coinbase credentials and a published risk policy.
					</p>
				{/if}
			</div>
			<div class="fields">
				<label>
					Product
					<input bind:value={productId} required pattern={'[A-Z0-9]{2,20}-(?:USD|USDC)'} />
					<small>Coinbase product id, e.g. BTC-USDC</small>
				</label>
				{#if !adopting}
					<div class="field">
						<span class="field-label" aria-hidden="true">Side</span>
						<Segmented
							label="Side"
							options={[
								{ id: 'long', label: 'Buy / long' },
								{ id: 'short', label: 'Sell / short' }
							]}
							value={side}
							onchange={(next) => (side = next)}
							testId="discretionary-side"
						/>
					</div>
				{/if}
				<div class="field">
					<span class="field-label" aria-hidden="true">Order type</span>
					<Segmented
						label="Order type"
						options={[
							{ id: 'post_only_limit', label: 'Post-only limit' },
							{ id: 'marketable', label: 'Marketable' },
							{ id: 'adopt', label: 'Adopt holdings', live: true }
						]}
						value={entryKind}
						onchange={(next) => (entryKind = next)}
						testId="discretionary-entry"
					/>
				</div>
				<label>
					Clock
					<select bind:value={timeframe}>
						{#each EXECUTION_TIMEFRAMES as clock (clock)}
							<option value={clock}>{clock}</option>
						{/each}
					</select>
				</label>
				{#if !adopting}
					<label>
						Limit price
						<input
							bind:value={limitPrice}
							inputmode="decimal"
							placeholder={entryKind === 'post_only_limit' ? 'Required for maker' : 'Not used'}
						/>
					</label>
					<label>
						Quantity
						<input bind:value={quantity} inputmode="decimal" placeholder="Base amount" />
					</label>
					<label>
						Quote notional
						<input bind:value={quoteNotional} inputmode="decimal" placeholder="Or a quote amount" />
					</label>
					<label>
						Stop loss
						<input bind:value={stopPrice} required inputmode="decimal" />
					</label>
					<label>
						Take profit
						<input bind:value={takeProfitPrice} required inputmode="decimal" />
					</label>
				{/if}
			</div>
			{#if adopting}
				<TradeAdopt
					{isLive}
					{productId}
					{timeframe}
					{note}
					onresult={(next) => void onAdopted(next)}
				/>
			{:else if mode === 'paper'}
				<PaperAssumptions bind:paperCash bind:paperMakerFee bind:paperTakerFee />
			{/if}
			<label class="note">
				Why note
				<textarea
					bind:value={note}
					data-testid="discretionary-note"
					maxlength="4000"
					placeholder="Optional. Frozen onto the why-trade record."></textarea>
			</label>
		</form>

		{#if !adopting}
			<TradeReview
				{isLive}
				{market}
				{side}
				{review}
				{sizeText}
				{riskPolicy}
				{submitting}
				onsubmit={onSubmitClick}
			/>
		{/if}
	</div>

	{#if error && !liveConfirmOpen}
		<div class="error-banner" role="alert">
			<div>
				<strong>Order not accepted</strong>
				<p>{error}</p>
			</div>
		</div>
	{/if}

	<TradeBooks {result} {books} {booksError} />

	<TradeReasonReview
		records={tradeReasons}
		emptyMessage="Why-trade records for this ticket appear after an intent is persisted."
	/>
</main>

<TradeLiveDialog
	open={liveConfirmOpen}
	{side}
	{market}
	{timeframe}
	{productId}
	{review}
	{sizeText}
	{stopPrice}
	{takeProfitPrice}
	{submitting}
	{error}
	bind:liveAcknowledged
	oncancel={() => (liveConfirmOpen = false)}
	onconfirm={() => void submitOrder(liveAcknowledged)}
/>

<style>
	.trade-grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 380px;
		gap: 16px;
		align-items: start;
	}
	.ticket {
		display: grid;
		gap: 14px;
		padding: 16px;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 14px;
	}
	.field {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 6px;
	}
	.field-label,
	label {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	label small {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	input,
	select,
	textarea {
		min-height: 36px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	textarea {
		min-height: 4.5rem;
		padding: 8px 10px;
		resize: vertical;
	}
	.live-note {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 2px 0 0;
		color: var(--text);
		font-size: var(--fs-sm);
	}
	.error-banner {
		margin-top: 16px;
	}
	@media (max-width: 1000px) {
		.trade-grid {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
