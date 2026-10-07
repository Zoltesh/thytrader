<script lang="ts">
	/**
	 * Trade (`/trade`): one-off long/short ticket with SL/TP over the same
	 * intent → risk → broker path as `thytrader-runtime place-order --confirm`.
	 *
	 * Form left, Review aside right. Live mode turns on the shell's live
	 * chrome, and a live submit goes through the live confirmation dialog whose
	 * confirm stays disabled until "I understand this places real orders" is
	 * ticked; only then is `i_understand_live: true` sent.
	 */
	import { onMount } from 'svelte';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import PageHead from '$lib/PageHead.svelte';
	import Segmented from '$lib/Segmented.svelte';
	import TradeReasonReview from '$lib/TradeReasonReview.svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import { listDeployments, placeDiscretionaryOrder, type Deployment } from '$lib/deployments';
	import { PAPER_FEE_ENGINE_NOTE, optionalFeeRate } from '$lib/fees';
	import { declareLiveContext } from '$lib/live-context.svelte';
	import { fetchTradeReasons, type TradeReasonRecord } from '$lib/memory';
	import { EXECUTION_TIMEFRAMES, type ExecutionTimeframe } from '$lib/strategies';
	import type { RiskPolicySnapshot } from '$lib/strategy-workspace';
	import { tradeReview } from '$lib/trade-review';
	import { fetchRiskPolicySnapshot } from '$lib/workspace-data';

	let productId = $state('BTC-USD');
	let mode = $state<'paper' | 'live'>('paper');
	let side = $state<'long' | 'short'>('long');
	let timeframe = $state<ExecutionTimeframe>('5m');
	let entryKind = $state<'post_only_limit' | 'marketable'>('post_only_limit');
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
	const review = $derived(
		tradeReview({
			productId,
			side,
			entryKind,
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
	const riskPolicyText = $derived.by((): { text: string; attention: boolean } => {
		if (riskPolicy === null) return { text: 'Reading…', attention: false };
		if (riskPolicy === 'unknown') return { text: 'Unknown', attention: false };
		if (riskPolicy.source === 'published') {
			return {
				text: `Published v${riskPolicy.version} · checked again on submit`,
				attention: false
			};
		}
		return isLive
			? {
					text: 'Compiled default · live orders are refused until a policy is published',
					attention: true
				}
			: { text: 'Compiled default · checked on submit', attention: false };
	});

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
		if (entryKind === 'post_only_limit' && limitPrice === '') {
			return 'Post-only entries require a limit price.';
		}
		return null;
	}

	function onSubmitClick(): void {
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
				entry_kind: entryKind,
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
				<div class="field">
					<span class="field-label" aria-hidden="true">Order type</span>
					<Segmented
						label="Order type"
						options={[
							{ id: 'post_only_limit', label: 'Post-only limit' },
							{ id: 'marketable', label: 'Marketable' }
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
			</div>
			{#if mode === 'paper'}
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

		<aside
			class="card review"
			class:live={isLive}
			aria-labelledby="review-title"
			data-testid="trade-review"
		>
			<h2 id="review-title">Review</h2>
			<div class="check"><span>Mode</span><b>{isLive ? 'LIVE · real money' : 'Paper'}</b></div>
			<div class="check"><span>Market</span><b>{market}</b></div>
			<div class="check">
				<span>Side</span><b>{side === 'long' ? 'Buy / long' : 'Sell / short'}</b>
			</div>
			<div class="check"><span>Entry</span><b data-testid="review-entry">{review.entry}</b></div>
			<div class="check"><span>Size</span><b>{sizeText}</b></div>
			<div class="check">
				<span>Max loss at stop</span><b data-testid="review-max-loss">{review.maxLoss}</b>
			</div>
			<div class="check">
				<span>Reward : risk</span><b data-testid="review-reward-risk">{review.rewardRisk}</b>
			</div>
			<div class="check">
				<span>Risk policy</span><b
					class:attention={riskPolicyText.attention}
					data-testid="review-risk-policy">{riskPolicyText.text}</b
				>
			</div>
			{#each review.warnings as warning (warning)}
				<p class="warning" role="status">{warning}</p>
			{/each}
			<p class="hint">
				Fees and slippage are not included. Sizing, caps, and data health are checked by the server.
			</p>
			{#if isLive}
				<button type="button" class="btn live submit" disabled={submitting} onclick={onSubmitClick}
					>Review live order…</button
				>
			{:else}
				<button
					type="button"
					class="btn primary submit"
					disabled={submitting}
					onclick={onSubmitClick}
					>{submitting
						? 'Submitting…'
						: side === 'short'
							? 'Place paper short'
							: 'Place paper long'}</button
				>
			{/if}
		</aside>
	</div>

	{#if error && !liveConfirmOpen}
		<div class="error-banner" role="alert">
			<div>
				<strong>Order not accepted</strong>
				<p>{error}</p>
			</div>
		</div>
	{/if}

	{#if result}
		<section class="card panel" data-testid="discretionary-result">
			<p class="label">Last snapshot</p>
			<p>{result.id}</p>
			<p>{result.mode} · {result.status} · {result.phase}</p>
			<p>{result.orders.length} orders · {result.fills.length} fills</p>
		</section>
	{/if}

	<section class="card panel">
		<p class="label">Discretionary books</p>
		{#if booksError !== null}
			<p class="empty" data-testid="discretionary-books-incomplete">{booksError}</p>
		{:else if books.length === 0}
			<p class="empty">No discretionary books yet.</p>
		{:else}
			<ul>
				{#each books as book (book.id)}
					<li>{book.product_id} · {book.mode} · {book.status} · {book.phase}</li>
				{/each}
			</ul>
		{/if}
	</section>

	<TradeReasonReview
		records={tradeReasons}
		emptyMessage="Why-trade records for this ticket appear after an intent is persisted."
	/>
</main>

<ConfirmDialog
	open={liveConfirmOpen}
	title="Send live order?"
	tone="live"
	confirmLabel={side === 'short' ? 'Send live short' : 'Send live long'}
	pendingLabel="Sending…"
	pending={submitting}
	confirmDisabled={!liveAcknowledged}
	confirmDisabledReason="Tick the acknowledgement to continue."
	{error}
	testId="live-order-dialog"
	oncancel={() => (liveConfirmOpen = false)}
	onconfirm={() => void submitOrder(liveAcknowledged)}
>
	<p>
		This submits a real {side === 'short' ? 'sell-to-open (short)' : 'buy (long)'} spot order on Coinbase
		using your API keys, with the stop loss and take profit attached when possible. You are solely responsible
		for all trades and market risk.
	</p>
	<div class="row"><span>Market</span><span>{market} · {timeframe}</span></div>
	<div class="row"><span>Coinbase product record</span><code>{productId}</code></div>
	<div class="row"><span>Entry</span><span>{review.entry}</span></div>
	<div class="row"><span>Size</span><span>{sizeText}</span></div>
	<div class="row">
		<span>Stop / take profit</span><span>{stopPrice || '—'} / {takeProfitPrice || '—'}</span>
	</div>
	<div class="row"><span>Max loss at stop</span><span>{review.maxLoss}</span></div>
	<p>
		Shorts never borrow: they need available base. If the request times out, ThyTrader reconciles by
		client order id instead of re-sending.
	</p>
	<label class="live-ack">
		<input type="checkbox" bind:checked={liveAcknowledged} />
		<span>I understand this places real orders on Coinbase with real money.</span>
	</label>
</ConfirmDialog>

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
	.live-note {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 2px 0 0;
		color: var(--text);
		font-size: var(--fs-sm);
	}
	.review {
		position: sticky;
		top: calc(var(--topbar-height) + 50px);
		padding: 16px;
	}
	.review.live {
		border: 2px solid var(--live);
	}
	.check {
		display: flex;
		gap: 10px;
		padding: 9px 0;
		border-bottom: 1px solid var(--line);
	}
	.check span {
		flex: 1;
		color: var(--muted);
	}
	.check b {
		font-weight: 500;
		text-align: right;
	}
	.check b.attention,
	.warning {
		color: var(--warn);
	}
	.warning {
		margin: 8px 0 0;
		font-size: var(--fs-sm);
	}
	.submit {
		width: 100%;
		margin-top: 14px;
	}
	.panel {
		margin-top: 16px;
		padding: 14px 16px;
	}
	.label {
		margin: 0 0 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.panel p,
	.panel li {
		margin: 0;
		color: var(--text);
	}
	.panel ul {
		margin: 0;
		padding-left: 18px;
	}
	.empty {
		color: var(--muted);
	}
	.error-banner {
		margin-top: 16px;
	}
	.live-ack {
		flex-direction: row;
		align-items: flex-start;
		gap: 10px;
		padding: 12px;
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
		font-size: var(--fs-base);
		cursor: pointer;
	}
	.live-ack input {
		min-height: 0;
		margin-top: 2px;
	}
	@media (max-width: 1000px) {
		.trade-grid {
			grid-template-columns: minmax(0, 1fr);
		}
		.review {
			position: static;
		}
	}
</style>
