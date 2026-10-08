<script lang="ts">
	/**
	 * Lifecycle confirmation (pause, resume, stop, flatten, breaker reset) on
	 * the shared accessible `ConfirmDialog` shell: native modal, Cancel focused
	 * by default, Escape closes only before submission, focus returns to the
	 * trigger. Wording comes from `lifecycleDialog()` (design spec §10).
	 *
	 * Live resume re-arms real orders: with `requireLiveAcknowledgement`, the
	 * confirm stays disabled until the "real orders" checkbox is ticked, and
	 * `onconfirm` reports that acknowledgement so the caller sends
	 * `i_understand_live: true` only when it is true.
	 */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { lifecycleDialog, marketLabel, type LifecycleAction } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';

	let {
		deployment,
		action,
		stopWithFlatten = $bindable(false),
		mutating,
		actionError = null,
		requireLiveAcknowledgement = false,
		oncancel,
		onconfirm
	}: {
		/** Target deployment; the dialog is open while both this and `action` are set. */
		deployment: Deployment | null;
		action: LifecycleAction | null;
		/** Bound two-way for the managed-stop vs flatten radio choice. */
		stopWithFlatten: boolean;
		mutating: boolean;
		actionError: string | null;
		/** Live resume: gate Confirm behind an explicit "real orders" checkbox. */
		requireLiveAcknowledgement?: boolean;
		oncancel: () => void;
		onconfirm: (options: { liveAcknowledged: boolean }) => void;
	} = $props();

	const open = $derived(deployment !== null && action !== null);
	const dialog = $derived(
		deployment !== null && action !== null ? lifecycleDialog(deployment, action) : null
	);
	const isLive = $derived(deployment?.mode === 'live');
	const needsAck = $derived(requireLiveAcknowledgement && isLive && action === 'resume');
	let liveAcknowledged = $state(false);

	$effect(() => {
		// Every opening starts unacknowledged; nothing carries over between dialogs.
		if (!open) liveAcknowledged = false;
	});

	const tone = $derived(
		action === 'resume' && isLive ? 'live' : dialog?.danger ? 'danger' : 'default'
	);
	const confirmLabel = $derived(
		dialog === null
			? ''
			: stopWithFlatten && dialog.chooseStopMode
				? 'Stop and flatten'
				: dialog.confirm
	);
	const pendingLabel = $derived(
		dialog === null
			? ''
			: stopWithFlatten && dialog.chooseStopMode
				? 'Requesting flatten…'
				: dialog.pending
	);
</script>

<ConfirmDialog
	{open}
	title={dialog?.title ?? ''}
	{tone}
	liveChip={isLive}
	{confirmLabel}
	{pendingLabel}
	pending={mutating}
	confirmDisabled={needsAck && !liveAcknowledged}
	confirmDisabledReason="Tick the acknowledgement to continue."
	error={actionError}
	testId="lifecycle-dialog"
	{oncancel}
	onconfirm={() => onconfirm({ liveAcknowledged: needsAck && liveAcknowledged })}
>
	{#if dialog !== null && deployment !== null}
		<div class="row">
			<span>Bot</span><span
				>{isLive ? 'Live' : 'Paper'} · {marketLabel(deployment.product_id)} · {deployment.timeframe ??
					'clock unknown'}</span
			>
		</div>
		{#each dialog.body as line, index (index)}
			<p>{line}</p>
		{/each}
		{#if dialog.chooseStopMode}
			<fieldset class="stop-mode">
				<legend>Shutdown mode</legend>
				<label class="choice">
					<input type="radio" name="stop-mode" bind:group={stopWithFlatten} value={false} />
					<span>
						<strong>Managed stop — keep protection</strong>
						Stop new entries and cancel risk-increasing entry orders. Keep protective exits active. Open
						positions remain in account-level risk until they close.
					</span>
				</label>
				<label class="choice">
					<input type="radio" name="stop-mode" bind:group={stopWithFlatten} value={true} />
					<span>
						<strong>Stop and flatten</strong>
						Stop new entries, submit marketable exits for open inventory, then cancel remaining orders.
						Exit price and fees are not guaranteed.
					</span>
				</label>
			</fieldset>
			<p class="async-note">
				The lifecycle state changes when the request is accepted. Cancellation and exits are applied
				asynchronously by the worker.
			</p>
		{/if}
		{#if needsAck}
			<label class="live-ack">
				<input type="checkbox" bind:checked={liveAcknowledged} />
				<span>I understand this places real orders on Coinbase with real money.</span>
			</label>
		{/if}
	{/if}
</ConfirmDialog>

<style>
	.stop-mode {
		display: grid;
		gap: 10px;
		margin: 0;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
	}
	.stop-mode legend {
		padding: 0 4px;
		color: var(--muted);
		font-size: var(--fs-xs);
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}
	.choice {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 10px;
		color: var(--muted);
		font-size: var(--fs-sm);
		cursor: pointer;
	}
	.choice strong {
		display: block;
		margin-bottom: 2px;
		color: var(--text);
		font-size: var(--fs-base);
	}
	.async-note {
		font-size: var(--fs-sm);
	}
	.live-ack {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 12px;
		border-radius: var(--radius-md);
		background: var(--live-soft);
		color: var(--text);
		cursor: pointer;
	}
	.live-ack input {
		margin-top: 2px;
	}
</style>
