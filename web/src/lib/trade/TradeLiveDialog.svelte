<script lang="ts">
	/**
	 * Live order confirmation for the trade ticket: what will be sent, and a
	 * confirm that stays disabled until "I understand this places real orders" is
	 * ticked. Only that ticked box lets the page send `i_understand_live: true`.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import type { TradeReview } from '$lib/trade-review';

	let {
		open,
		side,
		market,
		timeframe,
		productId,
		review,
		sizeText,
		stopPrice,
		takeProfitPrice,
		submitting,
		error,
		liveAcknowledged = $bindable(),
		oncancel,
		onconfirm
	}: {
		open: boolean;
		side: 'long' | 'short';
		market: string;
		timeframe: string;
		productId: string;
		review: TradeReview;
		sizeText: string;
		stopPrice: string;
		takeProfitPrice: string;
		submitting: boolean;
		error: string | null;
		liveAcknowledged: boolean;
		oncancel: () => void;
		onconfirm: () => void;
	} = $props();
</script>

<ConfirmDialog
	{open}
	title="Send live order?"
	tone="live"
	confirmLabel={side === 'short' ? 'Send live short' : 'Send live long'}
	pendingLabel="Sending…"
	pending={submitting}
	confirmDisabled={!liveAcknowledged}
	confirmDisabledReason="Tick the acknowledgement to continue."
	{error}
	testId="live-order-dialog"
	{oncancel}
	{onconfirm}
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
</style>
