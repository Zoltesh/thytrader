<script lang="ts">
	/**
	 * Trade ticket "Adopt holdings" (ADR 0124, live only): take over coins already held at
	 * Coinbase into a discretionary long book and rest a stop and take-profit. Nothing is
	 * bought. The preview shows the balance, what bots already manage, and what is
	 * adoptable. The confirm stays disabled until the live acknowledgement is ticked, and
	 * only then is `i_understand_live: true` sent. One idempotency key per opened dialog
	 * makes a retry after a timeout return the same book instead of adopting twice.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import AdoptionFigures from '$lib/adoption/AdoptionFigures.svelte';
	import {
		adoptHoldings,
		adoptionQuantity,
		adoptionQuantityError,
		blockingReasons,
		fetchAdoptionPreview,
		type AdoptionPreview
	} from '$lib/adoption';
	import type { Deployment } from '$lib/deployments';

	let {
		isLive,
		productId,
		timeframe,
		note,
		onresult
	}: {
		isLive: boolean;
		productId: string;
		timeframe: string;
		note: string;
		onresult: (deployment: Deployment) => void;
	} = $props();

	const PRODUCT = /^[A-Z0-9]{2,20}-(USD|USDC)$/;

	let all = $state(true);
	let quantity = $state('');
	let stopPrice = $state('');
	let takeProfitPrice = $state('');
	let preview = $state<AdoptionPreview | null>(null);
	let previewError = $state<string | null>(null);
	let loading = $state(false);
	let confirmOpen = $state(false);
	let acknowledged = $state(false);
	let submitting = $state(false);
	let submitError = $state<string | null>(null);
	let idempotencyKey = $state('');
	let generation = 0;

	const product = $derived(productId.trim());
	const quantityProblem = $derived(adoptionQuantityError(all, quantity));
	const blocked = $derived(blockingReasons(preview, 'protect').length > 0);
	const levelsProblem = $derived(
		stopPrice.trim() === '' || takeProfitPrice.trim() === ''
			? 'Stop loss and take profit are both required.'
			: null
	);
	const problem = $derived(
		!isLive
			? 'Adopt holdings is live only: switch Mode to Live.'
			: (quantityProblem ?? levelsProblem)
	);

	async function loadPreview(): Promise<void> {
		const requested = ++generation;
		if (!PRODUCT.test(product)) {
			preview = null;
			previewError = null;
			return;
		}
		loading = true;
		try {
			const next = await fetchAdoptionPreview(product, timeframe);
			if (requested !== generation) return;
			preview = next;
			previewError = null;
		} catch (caught) {
			if (requested !== generation) return;
			preview = null;
			previewError = caught instanceof Error ? caught.message : 'Preview failed.';
		} finally {
			if (requested === generation) loading = false;
		}
	}

	$effect(() => {
		void product;
		void timeframe;
		void loadPreview();
	});

	function openConfirm(): void {
		submitError = null;
		if (problem !== null) {
			submitError = problem;
			return;
		}
		acknowledged = false;
		idempotencyKey = crypto.randomUUID();
		confirmOpen = true;
	}

	async function confirm(): Promise<void> {
		if (!acknowledged) return;
		submitting = true;
		submitError = null;
		try {
			const trimmedNote = note.trim();
			const result = await adoptHoldings({
				action: 'protect',
				product_id: product,
				quantity: adoptionQuantity(all, quantity),
				stop_price: stopPrice.trim(),
				take_profit_price: takeProfitPrice.trim(),
				idempotency_key: idempotencyKey,
				timeframe,
				note: trimmedNote === '' ? undefined : trimmedNote,
				i_understand_live: true
			});
			confirmOpen = false;
			onresult(result);
			void loadPreview();
		} catch (caught) {
			submitError = caught instanceof Error ? caught.message : 'The adoption was not accepted.';
		} finally {
			submitting = false;
		}
	}
</script>

<section class="adopt" data-testid="trade-adopt">
	<p class="lede">
		Take over {product.split('-')[0] || 'coins'} you already hold at Coinbase: a discretionary book adopts
		them at the mark and rests your stop loss and take profit. Nothing is bought. Live only.
	</p>
	<AdoptionFigures {preview} {loading} error={previewError} action="protect" />
	<div class="fields">
		<label class="all">
			<input type="checkbox" bind:checked={all} data-testid="adopt-all" />
			<span>All unmanaged</span>
		</label>
		<label>
			Quantity
			<input
				bind:value={quantity}
				inputmode="decimal"
				disabled={all}
				placeholder={all ? 'All adoptable' : 'Base amount'}
				data-testid="adopt-quantity"
			/>
		</label>
		<label>
			Stop loss
			<input bind:value={stopPrice} inputmode="decimal" data-testid="adopt-stop" />
		</label>
		<label>
			Take profit
			<input bind:value={takeProfitPrice} inputmode="decimal" data-testid="adopt-target" />
		</label>
	</div>
	{#if submitError && !confirmOpen}
		<p class="warning" role="alert">{submitError}</p>
	{/if}
	<button
		type="button"
		class="btn live"
		disabled={submitting || blocked}
		onclick={openConfirm}
		data-testid="adopt-review">Review adoption…</button
	>
</section>

<ConfirmDialog
	open={confirmOpen}
	title="Adopt held coins?"
	tone="live"
	confirmLabel="Adopt and protect"
	pendingLabel="Adopting…"
	pending={submitting}
	confirmDisabled={!acknowledged}
	confirmDisabledReason="Tick the acknowledgement to continue."
	error={submitError}
	testId="adopt-dialog"
	oncancel={() => (confirmOpen = false)}
	onconfirm={() => void confirm()}
>
	<p>
		A live discretionary book takes ownership of {adoptionQuantity(all, quantity)}
		{preview?.base_currency ?? ''} at the {timeframe} close, then rests a real stop loss and take profit
		on Coinbase. Nothing is bought.
	</p>
	<div class="row"><span>Product</span><code>{product}</code></div>
	<div class="row"><span>Mark</span><span>{preview?.mark ?? 'Unknown'}</span></div>
	<div class="row">
		<span>Stop / take profit</span><span>{stopPrice || '—'} / {takeProfitPrice || '—'}</span>
	</div>
	<label class="live-ack">
		<input type="checkbox" bind:checked={acknowledged} data-testid="adopt-ack" />
		<span>I understand this places real orders on Coinbase with real money.</span>
	</label>
</ConfirmDialog>

<style>
	.adopt {
		display: grid;
		gap: 12px;
	}
	.lede {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
		gap: 12px;
		align-items: end;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	label.all,
	.live-ack {
		flex-direction: row;
		align-items: center;
		gap: 8px;
	}
	input:not([type='checkbox']) {
		min-height: 36px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	.warning {
		margin: 0;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.btn {
		justify-self: start;
	}
	.row {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		padding: 4px 0;
	}
	.live-ack {
		padding: 12px;
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
		font-size: var(--fs-base);
	}
</style>
