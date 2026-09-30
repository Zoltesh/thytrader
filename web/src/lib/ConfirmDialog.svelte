<script lang="ts">
	/**
	 * Accessible confirmation dialog (replaces `window.confirm`).
	 *
	 * Native modal <dialog>: the background is inert and focus stays inside.
	 * Default focus is Cancel. Escape closes only before submission; once the
	 * request is pending, Escape does nothing rather than pretending to cancel
	 * it. Closing restores focus to whatever opened the dialog.
	 */
	import { tick, type Snippet } from 'svelte';

	let {
		open,
		title,
		tone = 'default',
		confirmLabel,
		pendingLabel,
		pending = false,
		confirmDisabled = false,
		confirmDisabledReason = null,
		error = null,
		testId,
		liveChip = false,
		oncancel,
		onconfirm,
		children
	}: {
		open: boolean;
		title: string;
		/** `live` marks real-money actions; `danger` marks destructive ones. */
		tone?: 'default' | 'danger' | 'live';
		confirmLabel: string;
		pendingLabel: string;
		pending?: boolean;
		confirmDisabled?: boolean;
		/** Visible text (and aria-describedby) explaining a disabled confirm. */
		confirmDisabledReason?: string | null;
		error?: string | null;
		testId?: string;
		/** Show the LIVE chip for a real-money action even when the tone is `danger`. */
		liveChip?: boolean;
		oncancel: () => void;
		onconfirm: () => void;
		children: Snippet;
	} = $props();

	const uid = $props.id();
	const titleId = `confirm-title-${uid}`;
	const bodyId = `confirm-body-${uid}`;
	const reasonId = `confirm-reason-${uid}`;

	let dialog: HTMLDialogElement | undefined = $state();
	let cancelButton: HTMLButtonElement | undefined = $state();
	let returnFocus: HTMLElement | null = null;

	$effect(() => {
		const element = dialog;
		if (element === undefined) return;
		if (open && !element.open) {
			returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
			element.showModal();
			void tick().then(() => cancelButton?.focus());
		} else if (!open && element.open) {
			element.close();
			const target = returnFocus;
			returnFocus = null;
			if (target !== null && target.isConnected) target.focus();
		}
	});

	function onCancelEvent(event: Event): void {
		// Escape fires `cancel`; the parent owns `open`, so never let the browser close it.
		event.preventDefault();
		if (!pending) oncancel();
	}
</script>

<dialog
	bind:this={dialog}
	class="confirm-dialog"
	class:live={tone === 'live' || liveChip}
	aria-labelledby={titleId}
	aria-describedby={bodyId}
	data-testid={testId}
	oncancel={onCancelEvent}
>
	{#if open}
		<div class="head">
			{#if tone === 'live' || liveChip}<span class="chip live">LIVE</span>{/if}
			<h2 id={titleId}>{title}</h2>
		</div>
		<div class="body" id={bodyId}>
			{@render children()}
		</div>
		{#if error}<p class="problem" role="alert">{error}</p>{/if}
		<div class="actions">
			{#if confirmDisabled && confirmDisabledReason}
				<span class="reason" id={reasonId}>{confirmDisabledReason}</span>
			{/if}
			<button
				bind:this={cancelButton}
				type="button"
				class="btn"
				disabled={pending}
				onclick={oncancel}>Cancel</button
			>
			<button
				type="button"
				class="btn"
				class:primary={tone === 'default'}
				class:live={tone === 'live'}
				class:danger={tone === 'danger'}
				disabled={pending || confirmDisabled}
				aria-describedby={confirmDisabled && confirmDisabledReason ? reasonId : undefined}
				onclick={onconfirm}>{pending ? pendingLabel : confirmLabel}</button
			>
		</div>
		<p class="sr-only" aria-live="polite">{pending ? pendingLabel : ''}</p>
	{/if}
</dialog>

<style>
	.confirm-dialog {
		width: min(540px, calc(100vw - 32px));
		max-height: 90vh;
		padding: 0;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-xl);
		background: var(--surface);
		color: var(--text);
		box-shadow: var(--shadow);
	}
	.confirm-dialog.live {
		border-color: var(--live);
	}
	.confirm-dialog::backdrop {
		background: var(--scrim);
	}
	.head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 18px 20px;
		border-bottom: 1px solid var(--line);
	}
	.head h2 {
		font-size: var(--fs-lg);
	}
	.body {
		display: grid;
		gap: 10px;
		padding: 18px 20px;
		color: var(--muted);
	}
	.body :global(p) {
		margin: 0;
	}
	.body :global(.row) {
		display: flex;
		gap: 10px;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
		color: var(--text);
	}
	.body :global(.row > span:first-child) {
		flex: 1;
		color: var(--muted);
	}
	.problem {
		margin: 0 20px 12px;
		color: var(--neg);
	}
	.actions {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 8px;
		padding: 14px 20px;
		border-top: 1px solid var(--line);
	}
	.reason {
		margin-right: auto;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.btn.danger {
		border-color: var(--danger-line);
		background: var(--danger-soft);
		color: var(--neg);
		font-weight: 600;
	}
	@media (max-width: 640px) {
		.confirm-dialog {
			width: 100vw;
			max-width: 100vw;
			margin: auto 0 0;
			border-radius: var(--radius-xl) var(--radius-xl) 0 0;
		}
	}
</style>
