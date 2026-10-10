<script lang="ts">
	/**
	 * "This bot is running an earlier edit" (ADR 0082).
	 *
	 * A bot keeps the snapshot it started with; editing the strategy never
	 * changes a running bot. The honest path to the current rules is a managed
	 * stop of this bot followed by a new start of the current definition. The
	 * guided "Update bot" does exactly that, behind one confirmation that names
	 * both steps; live still needs the real-orders acknowledgement before
	 * `i_understand_live: true` is sent.
	 */
	import { resolve } from '$app/paths';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import { createDeployment, stopDeployment, type Deployment } from '$lib/deployments';
	import { futuresRestartFees, type FuturesRestartFees } from '$lib/futures-book-api';
	import { isFuturesProductId } from '$lib/product-id';
	import type { BuilderModel } from '$lib/strategies';
	import { rulesState, shortStrategyFingerprint, workspaceHref } from '$lib/strategy-workspace';
	import SnapshotDiff from './SnapshotDiff.svelte';

	let {
		deployment,
		currentFingerprint,
		current,
		disabled = false,
		onupdated
	}: {
		deployment: Deployment;
		currentFingerprint: string | null;
		/** Current valid definition; null while the saved definition is invalid. */
		current: BuilderModel | null;
		disabled?: boolean;
		/** Called after the old bot stopped (and with the new bot when the start succeeded). */
		onupdated: (result: { stopped: Deployment; started: Deployment | null }) => void;
	} = $props();

	let open = $state(false);
	let diffOpen = $state(false);
	let pending = $state(false);
	let error = $state<string | null>(null);
	let liveAcknowledged = $state(false);
	let partial = $state<string | null>(null);

	const isLive = $derived(deployment.mode === 'live');
	const earlier = $derived(
		deployment.strategy_id !== null &&
			rulesState(deployment.strategy_fingerprint, currentFingerprint) === 'earlier'
	);
	const active = $derived(deployment.status === 'running' || deployment.status === 'paused');
	const canUpdate = $derived(active && current !== null && currentFingerprint !== null);

	function openDialog(): void {
		error = null;
		partial = null;
		liveAcknowledged = false;
		open = true;
	}

	async function update(): Promise<void> {
		if (pending || deployment.strategy_id === null) return;
		if (isLive && !liveAcknowledged) return;
		pending = true;
		error = null;
		let futuresFees: FuturesRestartFees | null = null;
		if (!isLive && isFuturesProductId(deployment.product_id)) {
			// A futures start requires its fees: read them before stopping anything.
			try {
				futuresFees = await futuresRestartFees(deployment.id);
			} catch (caught) {
				error = caught instanceof Error ? caught.message : 'Could not read the futures fees.';
				pending = false;
				return;
			}
		}
		let stopped: Deployment;
		try {
			// Step 1: managed stop (protective exits stay until flat; no flatten).
			stopped = await stopDeployment(deployment.id, false);
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not stop this bot.';
			pending = false;
			return;
		}
		try {
			// Step 2: a new bot of the current rules; the server snapshots them.
			const started = await createDeployment(
				isLive
					? { strategy_id: deployment.strategy_id, mode: 'live', i_understand_live: true }
					: {
							strategy_id: deployment.strategy_id,
							mode: 'paper',
							// Spot fee rates are omitted so the new bot takes the account's current
							// rates; a futures book keeps its own (they are required).
							paper_starting_cash: deployment.paper_starting_cash ?? undefined,
							...(futuresFees ?? {})
						}
			);
			open = false;
			onupdated({ stopped, started });
		} catch (caught) {
			const reason = caught instanceof Error ? caught.message : 'The new start failed.';
			partial = `This bot was stopped (managed stop), but the new bot did not start: ${reason} Start it from Run when ready.`;
			open = false;
			onupdated({ stopped, started: null });
		} finally {
			pending = false;
		}
	}
</script>

{#if earlier}
	<div class="earlier" role="status" data-testid="earlier-edit-notice">
		<div class="text">
			<strong>This bot is running an earlier edit</strong>
			<p>
				It keeps the rules it started with ({shortStrategyFingerprint(
					deployment.strategy_fingerprint ?? ''
				)}). Editing the strategy never changes a running bot. To use the current rules, stop this
				bot and start a new one.
			</p>
			<button
				class="link"
				type="button"
				aria-expanded={diffOpen}
				onclick={() => (diffOpen = !diffOpen)}>What changed</button
			>
			{#if diffOpen && deployment.strategy_fingerprint}
				<SnapshotDiff fingerprint={deployment.strategy_fingerprint} {current} />
			{/if}
			{#if current === null}
				<p class="note">The current definition is invalid; fix it in Build before updating.</p>
			{/if}
		</div>
		{#if canUpdate}
			<button
				class="btn"
				class:live={isLive}
				type="button"
				{disabled}
				data-testid="update-bot"
				onclick={openDialog}>Update bot…</button
			>
		{:else if !active && deployment.strategy_id !== null}
			<a class="btn" href={resolve(workspaceHref(deployment.strategy_id, 'run'))}
				>Start with current rules</a
			>
		{/if}
	</div>
{/if}
{#if partial}
	<p class="problem" role="alert" data-testid="update-bot-partial">{partial}</p>
{/if}

<ConfirmDialog
	{open}
	title="Update bot to the current rules?"
	tone={isLive ? 'live' : 'default'}
	confirmLabel={isLive ? 'Stop and start live bot' : 'Stop and start paper bot'}
	pendingLabel="Updating…"
	{pending}
	confirmDisabled={isLive && !liveAcknowledged}
	confirmDisabledReason={isLive ? 'Tick the acknowledgement to continue.' : null}
	{error}
	testId="update-bot-dialog"
	oncancel={() => (open = false)}
	onconfirm={() => void update()}
>
	<p>This is two separate actions, in order:</p>
	<ol>
		<li>
			<strong>Managed stop of this bot.</strong> No new entries; protective exits stay until the book
			is flat. Its history and the rules it ran are kept.
		</li>
		<li>
			<strong>Start a new {isLive ? 'live' : 'paper'} bot</strong> with the strategy's current rules
			{#if current}({marketLabel(current.product_id)} · {current.timeframe}){/if}.
			{#if !isLive}It uses the same paper starting cash ({deployment.paper_starting_cash ??
					'default'}) and your Coinbase account's current fee rates.{/if}
		</li>
	</ol>
	<div class="row">
		<span>Current rules</span><code class="fp">{currentFingerprint ?? 'unknown'}</code>
	</div>
	{#if isLive}
		<label class="live-ack">
			<input type="checkbox" bind:checked={liveAcknowledged} />
			<span>I understand the new bot places real orders on Coinbase with real money.</span>
		</label>
	{/if}
</ConfirmDialog>

<style>
	.earlier {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 16px;
		margin: 0 0 var(--space-3);
		padding: 12px 14px;
		border: 1px solid var(--warn-line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
	}
	.text {
		display: grid;
		gap: 4px;
		min-width: 0;
	}
	.text p {
		margin: 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.note {
		color: var(--warn) !important;
	}
	.link {
		justify-self: start;
		padding: 0;
		border: 0;
		background: none;
		color: var(--accent);
		font-size: var(--fs-sm);
		text-decoration: underline;
		cursor: pointer;
	}
	.problem {
		color: var(--neg);
	}
	.fp {
		font-size: var(--fs-xs);
		word-break: break-all;
	}
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
	ol {
		margin: 0;
		padding-left: 20px;
	}
</style>
