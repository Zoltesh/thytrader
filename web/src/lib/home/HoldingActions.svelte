<script lang="ts">
	/**
	 * One holdings action (ADR 0124, live only), always behind a live confirmation.
	 *
	 * - "Sell to USDC" adopts the unmanaged coins into a stopped FLATTEN book. The execution
	 *   worker then sells them on its next cycle; no protective order is placed.
	 * - "Adopt into bot" starts a live strategy bot on this coin that already holds them;
	 *   the worker rests the strategy's stop and target on its next cycle.
	 *
	 * The preview shows what bots already manage and what is unmanaged. The confirm stays
	 * disabled until the live acknowledgement is ticked, and only then is
	 * `i_understand_live: true` sent. One idempotency key per opened sale makes a retry
	 * return the same book.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import AdoptionFigures from '$lib/adoption/AdoptionFigures.svelte';
	import {
		adoptHoldings,
		adoptionQuantity,
		adoptionQuantityError,
		blockingReasons,
		fetchAdoptionPreview,
		strategiesForBase,
		type AdoptionPreview
	} from '$lib/adoption';
	import { createDeployment, type Deployment } from '$lib/deployments';
	import { listStrategies } from '$lib/strategies-api';
	import type { StrategyLibraryEntry } from '$lib/strategies-types';
	import { holdingActionTitle, sellProduct, type HoldingAction } from './holding-actions';

	let {
		open,
		currency,
		action,
		oncancel,
		ondone
	}: {
		open: boolean;
		currency: string;
		action: HoldingAction;
		oncancel: () => void;
		ondone: (deployment: Deployment) => void;
	} = $props();

	let all = $state(true);
	let quantity = $state('');
	let acknowledged = $state(false);
	let pending = $state(false);
	let error = $state<string | null>(null);
	let preview = $state<AdoptionPreview | null>(null);
	let previewError = $state<string | null>(null);
	let loading = $state(false);
	let strategies = $state<StrategyLibraryEntry[]>([]);
	let strategiesError = $state<string | null>(null);
	let strategyId = $state('');
	let idempotencyKey = $state('');

	const chosen = $derived(strategies.find((item) => item.strategy_id === strategyId) ?? null);
	const product = $derived(
		action === 'sell' ? sellProduct(currency) : (chosen?.product_id ?? null)
	);
	const reasons = $derived(blockingReasons(preview, action === 'sell' ? 'sell' : 'protect'));
	const problem = $derived(
		adoptionQuantityError(all, quantity) ??
			(action === 'adopt' && chosen === null ? 'Choose a strategy for this coin.' : null)
	);

	$effect(() => {
		if (!open) return;
		all = true;
		quantity = '';
		acknowledged = false;
		error = null;
		idempotencyKey = crypto.randomUUID();
		if (action === 'adopt') void loadStrategies();
	});

	$effect(() => {
		const target = product;
		if (!open || target === null) {
			preview = null;
			return;
		}
		void loadPreview(target);
	});

	async function loadStrategies(): Promise<void> {
		try {
			strategies = strategiesForBase(await listStrategies(), currency);
			strategiesError = null;
			if (strategies.length === 1) strategyId = strategies[0].strategy_id;
		} catch (caught) {
			strategies = [];
			strategiesError = caught instanceof Error ? caught.message : 'Strategies unavailable.';
		}
	}

	async function loadPreview(target: string): Promise<void> {
		loading = true;
		try {
			preview = await fetchAdoptionPreview(target);
			previewError = null;
		} catch (caught) {
			preview = null;
			previewError = caught instanceof Error ? caught.message : 'Preview failed.';
		} finally {
			loading = false;
		}
	}

	async function confirm(): Promise<void> {
		if (!acknowledged || problem !== null || product === null) return;
		pending = true;
		error = null;
		try {
			const amount = adoptionQuantity(all, quantity);
			const result =
				action === 'sell'
					? await adoptHoldings({
							action: 'sell',
							product_id: product,
							quantity: amount,
							idempotency_key: idempotencyKey,
							i_understand_live: true
						})
					: await createDeployment({
							strategy_id: strategyId,
							mode: 'live',
							adopt_holdings: amount,
							i_understand_live: true
						});
			ondone(result);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'The request was not accepted.';
		} finally {
			pending = false;
		}
	}
</script>

<ConfirmDialog
	{open}
	title={holdingActionTitle(action, currency)}
	tone="live"
	confirmLabel={action === 'sell' ? 'Sell to USDC' : 'Start live bot'}
	pendingLabel={action === 'sell' ? 'Selling…' : 'Starting…'}
	{pending}
	confirmDisabled={!acknowledged || problem !== null || reasons.length > 0}
	confirmDisabledReason={problem ??
		(reasons.length > 0
			? 'The preview blocks this action.'
			: 'Tick the acknowledgement to continue.')}
	{error}
	testId="holding-action-dialog"
	{oncancel}
	onconfirm={() => void confirm()}
>
	{#if action === 'sell'}
		<p>
			ThyTrader adopts the unmanaged {currency} into a stopped book, and the execution worker sells it
			at market into USDC on its next cycle. No stop or take profit is placed.
		</p>
	{:else}
		<p>
			A live strategy bot starts already holding the unmanaged {currency}: nothing is bought, and
			the worker rests the strategy's stop and target on its next cycle.
		</p>
		<label>
			Strategy
			<select bind:value={strategyId} data-testid="holding-strategy">
				<option value="">Choose a {currency} strategy</option>
				{#each strategies as entry (entry.strategy_id)}
					<option value={entry.strategy_id}>{entry.name} · {entry.product_id}</option>
				{/each}
			</select>
		</label>
		{#if strategiesError !== null}
			<p class="warning" role="alert">{strategiesError}</p>
		{:else if strategies.length === 0}
			<p class="warning">No valid strategy trades {currency}. Create one in Strategies first.</p>
		{/if}
	{/if}
	{#if product !== null}
		<AdoptionFigures
			{preview}
			{loading}
			error={previewError}
			action={action === 'sell' ? 'sell' : 'protect'}
		/>
	{/if}
	<div class="quantity">
		<label class="inline">
			<input type="checkbox" bind:checked={all} data-testid="holding-all" />
			<span>All unmanaged</span>
		</label>
		<label>
			Quantity
			<input
				bind:value={quantity}
				inputmode="decimal"
				disabled={all}
				placeholder={all ? 'All adoptable' : `${currency} amount`}
				data-testid="holding-quantity"
			/>
		</label>
	</div>
	<label class="inline live-ack">
		<input type="checkbox" bind:checked={acknowledged} data-testid="holding-ack" />
		<span>I understand this places real orders on Coinbase with real money.</span>
	</label>
</ConfirmDialog>

<style>
	label {
		display: flex;
		flex-direction: column;
		gap: 6px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	label.inline {
		flex-direction: row;
		align-items: center;
		gap: 8px;
	}
	.quantity {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		gap: 12px;
		align-items: end;
		margin: 12px 0;
	}
	input:not([type='checkbox']),
	select {
		min-height: 36px;
		padding: 0 10px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font: inherit;
	}
	.warning {
		margin: 6px 0 0;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.live-ack {
		padding: 12px;
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
		font-size: var(--fs-base);
	}
</style>
