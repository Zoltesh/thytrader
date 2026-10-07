<script lang="ts">
	/**
	 * Explicit fleet controls for Portfolio. Preview is required before confirm.
	 * Disarm, managed stop, and flatten are separate actions. None of them claims
	 * that venue cancellation or exits have finished.
	 */
	import { onMount } from 'svelte';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import {
		executeFleet,
		fetchFleetInhibition,
		fleetEffect,
		fleetNeedsLiveAck,
		previewFleet,
		residualLabel,
		type FleetAction,
		type FleetInhibition,
		type FleetMode,
		type FleetOperation,
		type FleetPreview
	} from '$lib/fleet-control';

	const actions: { id: FleetAction; label: string }[] = [
		{ id: 'disarm', label: 'Disarm entries' },
		{ id: 'managed_stop', label: 'Managed stop' },
		{ id: 'flatten', label: 'Flatten' },
		{ id: 'rearm', label: 'Rearm entries' }
	];

	let hydrated = $state(false);
	onMount(() => {
		hydrated = true;
	});
	let mode = $state<FleetMode>('paper');
	let inhibition = $state<FleetInhibition | null>(null);
	let inhibitionError = $state<string | null>(null);
	let preview = $state<FleetPreview | null>(null);
	let confirmationPreview = $state<FleetPreview | null>(null);
	let previewGeneration = 0;
	let previewError = $state<string | null>(null);
	let pendingAction = $state<FleetAction | null>(null);
	let liveAcknowledged = $state(false);
	let idempotencyKey = $state<string | null>(null);
	let acting = $state(false);
	let actionError = $state<string | null>(null);
	let result = $state<FleetOperation | null>(null);

	const needsAck = $derived(
		pendingAction !== null &&
			confirmationPreview !== null &&
			fleetNeedsLiveAck(pendingAction, confirmationPreview.mode)
	);
	const confirmedTargets = $derived(
		confirmationPreview === null ||
			pendingAction === null ||
			pendingAction === 'disarm' ||
			pendingAction === 'rearm'
			? []
			: confirmationPreview.targets.map((target) => ({
					deployment_id: target.deployment_id,
					revision: target.revision
				}))
	);

	async function refreshInhibition(): Promise<void> {
		try {
			inhibition = await fetchFleetInhibition();
			inhibitionError = null;
		} catch (caught) {
			inhibition = null;
			inhibitionError = caught instanceof Error ? caught.message : 'Entry latch is unavailable.';
		}
	}

	async function loadPreview(action: FleetAction): Promise<void> {
		if (!hydrated || acting || pendingAction !== null) return;
		const generation = ++previewGeneration;
		const requestedMode = mode;
		previewError = null;
		result = null;
		try {
			const received = await previewFleet(action, requestedMode);
			if (generation !== previewGeneration || pendingAction !== null || mode !== requestedMode)
				return;
			preview = received;
		} catch (caught) {
			if (generation !== previewGeneration || pendingAction !== null || mode !== requestedMode)
				return;
			preview = null;
			previewError = caught instanceof Error ? caught.message : 'Fleet preview failed.';
		}
	}

	function openConfirm(action: FleetAction): void {
		if (acting || pendingAction !== null) return;
		if (preview === null || preview.action !== action || preview.mode !== mode) return;
		// The person's consent is this immutable observation, never a later read.
		confirmationPreview = $state.snapshot(preview);
		previewGeneration += 1;
		pendingAction = action;
		liveAcknowledged = false;
		idempotencyKey = crypto.randomUUID();
		actionError = null;
	}

	async function confirm(): Promise<void> {
		const reviewed = confirmationPreview;
		if (acting || pendingAction === null || reviewed === null || idempotencyKey === null) return;
		if (reviewed.mode !== mode || reviewed.action !== pendingAction) return;
		if (needsAck && !liveAcknowledged) return;
		acting = true;
		actionError = null;
		try {
			result = await executeFleet(pendingAction, {
				mode: reviewed.mode,
				idempotencyKey,
				expectedTargets:
					pendingAction === 'managed_stop' || pendingAction === 'flatten' ? confirmedTargets : [],
				liveAcknowledged: needsAck && liveAcknowledged,
				allowEmptyScope: confirmedTargets.length === 0,
				expectedInhibition: {
					...(reviewed.mode === 'paper' || reviewed.mode === 'all'
						? { paper_revision: reviewed.inhibition.paper_revision }
						: {}),
					...(reviewed.mode === 'live' || reviewed.mode === 'all'
						? { live_revision: reviewed.inhibition.live_revision }
						: {})
				}
			});
			pendingAction = null;
			confirmationPreview = null;
			idempotencyKey = null;
			await refreshInhibition();
		} catch (caught) {
			actionError = caught instanceof Error ? caught.message : 'Fleet action failed.';
		} finally {
			acting = false;
		}
	}

	$effect(() => {
		void refreshInhibition();
	});
</script>

<section class="card fleet" data-testid="fleet-controls" aria-labelledby="fleet-controls-title">
	<div class="head">
		<h2 id="fleet-controls-title">Fleet controls</h2>
		<p>
			Disarm only blocks new starts and entries. Managed stop cancels risk-increasing entries and
			keeps protection. Flatten is explicit and separate. None of these prove a venue fill.
		</p>
	</div>
	<div class="row">
		<label>
			Mode
			<select
				bind:value={mode}
				disabled={!hydrated || acting || pendingAction !== null}
				data-testid="fleet-mode"
			>
				<option value="paper">Paper</option>
				<option value="live">Live</option>
				<option value="all">Paper and live</option>
			</select>
		</label>
		<p data-testid="fleet-latch">
			{#if inhibition === null}
				{inhibitionError ?? 'Reading entry latch…'}
			{:else}
				Paper entries {inhibition.paper_inhibited ? 'inhibited' : 'admitted'} · Live entries
				{inhibition.live_inhibited ? 'inhibited' : 'admitted'}
			{/if}
		</p>
	</div>
	<div class="actions">
		{#each actions as action (action.id)}
			<button
				type="button"
				class="btn"
				disabled={!hydrated || acting || pendingAction !== null}
				onclick={() => void loadPreview(action.id)}
			>
				Preview {action.label}
			</button>
		{/each}
	</div>
	{#if previewError !== null}
		<p class="warn" data-testid="fleet-preview-error">{previewError}</p>
	{/if}
	{#if preview !== null}
		{@const previewAction = preview.action}
		<div data-testid="fleet-preview">
			<p><strong>{fleetEffect(previewAction)}</strong></p>
			<p>
				Confirmed latch revisions: paper {preview.inhibition.paper_revision} · live {preview
					.inhibition.live_revision}
			</p>
			<p>
				Cancels entries: {preview.cancels_entries ? 'yes' : 'no'} · Flattens:
				{preview.flattens ? 'yes' : 'no'} · Pauses: {preview.pauses ? 'yes' : 'no'}
			</p>
			{#if preview.targets.length === 0}
				<p>No deployments in this mode.</p>
			{:else}
				<ul>
					{#each preview.targets as target (target.deployment_id)}
						<li>
							<code>{target.deployment_id}</code>
							rev {target.revision} · {target.mode} · {target.status} · {residualLabel(target)}
							<span>{target.effect}</span>
						</li>
					{/each}
				</ul>
			{/if}
			<button
				type="button"
				class="btn"
				data-testid="fleet-open-confirm"
				disabled={!hydrated || acting || pendingAction !== null}
				onclick={() => openConfirm(previewAction)}
			>
				Confirm {previewAction.replaceAll('_', ' ')}
			</button>
		</div>
	{/if}
	{#if result !== null}
		<div data-testid="fleet-result">
			<p>
				{result.status}. {result.note}
			</p>
			{#if result.targets.length > 0}
				<ul>
					{#each result.targets as target (target.deployment_id + target.status)}
						<li>
							{target.deployment_id}: {target.status} · {target.venue_effect}. {target.detail}
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{/if}
</section>

<ConfirmDialog
	open={pendingAction !== null}
	title={pendingAction === null ? '' : fleetEffect(pendingAction)}
	tone={pendingAction === 'flatten' ? 'danger' : needsAck ? 'live' : 'default'}
	confirmLabel="Confirm fleet action"
	pendingLabel="Recording…"
	pending={acting}
	confirmDisabled={needsAck && !liveAcknowledged}
	confirmDisabledReason="Tick the acknowledgement to continue."
	error={actionError}
	testId="fleet-dialog"
	liveChip={needsAck}
	oncancel={() => {
		pendingAction = null;
		confirmationPreview = null;
		idempotencyKey = null;
	}}
	onconfirm={() => void confirm()}
>
	<p>{pendingAction === null ? '' : fleetEffect(pendingAction)}</p>
	<p>Affected ids are the preview you just loaded. A changed revision is not commanded.</p>
	{#if needsAck}
		<label>
			<input type="checkbox" bind:checked={liveAcknowledged} />
			<span>I understand this can re-enable or exit live trading with real money.</span>
		</label>
	{/if}
</ConfirmDialog>

<style>
	.fleet {
		display: grid;
		gap: 12px;
		margin-bottom: 16px;
	}
	.head p,
	.row,
	.actions {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
		align-items: center;
	}
	.warn {
		color: var(--danger, #9a3412);
	}
	ul {
		display: grid;
		gap: 6px;
		padding-left: 18px;
	}
</style>
