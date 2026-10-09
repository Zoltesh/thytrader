<script lang="ts">
	/**
	 * Arm-live confirmation for the Run stage (ADR 0082): what the live deployment
	 * will run, and a confirm that stays disabled until "I understand this places
	 * real orders" is ticked. Only that ticked box lets the page send
	 * `i_understand_live: true`.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import type { BuilderModel } from '$lib/strategies';

	let {
		open,
		arming,
		armError,
		strategyName,
		model,
		fingerprint,
		liveAcknowledged = $bindable(),
		oncancel,
		onconfirm
	}: {
		open: boolean;
		arming: boolean;
		armError: string | null;
		strategyName: string | null;
		model: BuilderModel | null;
		fingerprint: string;
		liveAcknowledged: boolean;
		oncancel: () => void;
		onconfirm: () => void;
	} = $props();
</script>

<ConfirmDialog
	{open}
	title="Arm live trading?"
	tone="live"
	confirmLabel="Arm live trading"
	pendingLabel="Arming…"
	pending={arming}
	confirmDisabled={!liveAcknowledged}
	confirmDisabledReason="Tick the acknowledgement to continue."
	error={armError}
	testId="live-arm-dialog"
	{oncancel}
	{onconfirm}
>
	<p>
		This starts a live deployment of the current rules of
		<strong>{strategyName ?? model?.name ?? 'this strategy'}</strong>
		that places real spot orders on Coinbase with your API keys. Later edits do not change it. You are
		responsible for every trade and its market risk.
	</p>
	{#if model}
		<div class="row">
			<span>Market</span><span>{marketLabel(model.product_id)} · {model.timeframe}</span>
		</div>
		<div class="row"><span>Coinbase product record</span><code>{model.product_id}</code></div>
	{/if}
	<div class="row"><span>Rules snapshot</span><code class="fp">{fingerprint}</code></div>
	<div class="row">
		<span>If you stop it</span><span
			>Managed stop keeps protective exits until flat; flatten is a separate choice</span
		>
	</div>
	<label class="live-ack">
		<input type="checkbox" bind:checked={liveAcknowledged} />
		<span>I understand this places real orders on Coinbase with real money.</span>
	</label>
</ConfirmDialog>

<style>
	.live-ack {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		margin-top: 6px;
		padding: 12px;
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
		cursor: pointer;
	}
	.fp {
		font-size: var(--fs-xs);
		word-break: break-all;
	}
</style>
