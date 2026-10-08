<script lang="ts">
	/**
	 * Lifecycle status banners of the bot detail page: why controls are
	 * blocked, a stale snapshot after an accepted action, an unknown outcome
	 * (refresh before anything else), an action failure, the accepted message,
	 * a reconciliation mismatch, and the latched-breaker row with its reset.
	 */
	import type { LifecycleAction } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';
	import { breakerLatchText } from './lifecycle-actions';

	let {
		current,
		controlsAvailable,
		controlsBlocked,
		stale,
		refreshError,
		acceptedRevision,
		acceptedMessage,
		outcomeUnknown,
		actionError,
		dialogOpen,
		mutating,
		onretryrefresh,
		onrefresh,
		onaction
	}: {
		current: Deployment;
		controlsAvailable: boolean;
		/** An unknown outcome or a stale snapshot (not a missing contract). */
		controlsBlocked: boolean;
		stale: boolean;
		refreshError: string | null;
		acceptedRevision: number | null;
		acceptedMessage: string | null;
		outcomeUnknown: boolean;
		actionError: string | null;
		/** The lifecycle dialog shows its own error while it is open. */
		dialogOpen: boolean;
		mutating: boolean;
		/** Retry the post-mutation refresh. */
		onretryrefresh: () => void;
		/** Full reload after an unknown outcome. */
		onrefresh: () => void;
		onaction: (action: LifecycleAction) => void;
	} = $props();
</script>

{#if !controlsAvailable}
	<p class="contract-note" data-testid="controls-blocked-note" role="status">
		{controlsBlocked
			? 'Lifecycle controls are disabled until this deployment is refreshed successfully. An earlier action had an unknown outcome or the snapshot is stale; refresh to re-enable.'
			: 'Lifecycle controls are unavailable because this server did not return the current lifecycle contract. Values are shown read-only; nothing is inferred.'}
	</p>
{/if}

{#if stale}
	<div class="warn-banner" role="status" data-testid="stale-banner">
		<div>
			<strong>Stale snapshot</strong>
			<p>{refreshError}</p>
			{#if acceptedRevision !== null}
				<p>Showing response revision {acceptedRevision}.</p>
			{/if}
		</div>
		<button type="button" onclick={() => onretryrefresh()}>Retry refresh</button>
	</div>
{/if}
{#if outcomeUnknown}
	<div class="warn-banner" role="alert" data-testid="outcome-unknown-banner">
		<div>
			<strong>Outcome unknown</strong>
			<p>{actionError}</p>
		</div>
		<!-- Refresh first; controls stay disabled until this load succeeds. -->
		<button
			type="button"
			data-testid="outcome-unknown-refresh"
			onclick={() => {
				onrefresh();
			}}>Refresh now</button
		>
	</div>
{:else if actionError && !dialogOpen}
	<div class="error-banner" role="alert">
		<div>
			<strong>Action failed</strong>
			<p>{actionError}</p>
		</div>
	</div>
{/if}
{#if acceptedMessage && !stale}
	<div class="ok-banner" role="status">
		<p>{acceptedMessage}</p>
	</div>
{/if}
{#if current.mismatch_detail}
	<p class="problem-banner" role="alert">{current.mismatch_detail}</p>
{/if}
{#if current.daily_loss_latched || current.drawdown_latched}
	<div class="breaker-row" data-testid="breaker-row">
		<p class="problem" role="status">
			{breakerLatchText(current)} — new entries are blocked until you reset it.
		</p>
		<!-- Confirmation required: the reset dialog names the consequence
		     before any POST is sent. Hidden while controls are unavailable. -->
		{#if controlsAvailable}
			<button
				type="button"
				class="btn"
				disabled={mutating}
				data-testid="reset-breakers-button"
				onclick={() => onaction('reset-breakers')}>Reset breaker latches…</button
			>
		{/if}
	</div>
{/if}

<style>
	.problem {
		color: var(--neg);
	}
	.contract-note {
		margin: 0 0 12px;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.problem-banner {
		margin: 0 0 12px;
		padding: 10px 14px;
		border: 1px solid var(--danger-line);
		border-radius: var(--radius-md);
		background: var(--danger-soft);
		color: var(--neg);
	}
	.breaker-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px 14px;
		margin-bottom: 14px;
		padding: 10px 14px;
		border: 1px solid var(--danger-line);
		border-radius: var(--radius-md);
		background: var(--danger-soft);
	}
	.breaker-row p {
		margin: 0;
	}
	.warn-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 14px;
		padding: 14px 17px;
		border: 1px solid var(--warn-line);
		border-radius: var(--radius-md);
		background: var(--warn-soft);
	}
	.warn-banner p {
		margin: 4px 0 0;
		color: var(--warn);
	}
	.ok-banner {
		margin-bottom: 14px;
		padding: 12px 17px;
		border: 1px solid var(--accent-line);
		border-radius: var(--radius-md);
		background: var(--accent-soft);
	}
	.ok-banner p {
		margin: 0;
		color: var(--muted);
	}
</style>
