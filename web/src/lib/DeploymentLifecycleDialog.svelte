<script lang="ts">
	import { lifecycleDialog, type LifecycleAction } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';

	let {
		deployment,
		action,
		stopWithFlatten = $bindable(false),
		mutating,
		actionError = null,
		oncancel,
		onconfirm
	}: {
		deployment: Deployment;
		action: LifecycleAction;
		/** Bound two-way for the managed-stop vs flatten radio choice. */
		stopWithFlatten: boolean;
		mutating: boolean;
		actionError: string | null;
		oncancel: () => void;
		onconfirm: () => void;
	} = $props();

	const dialog = $derived(lifecycleDialog(deployment, action));

	let cancelButton: HTMLButtonElement | null = $state(null);
	let dialogRef: HTMLDivElement | null = $state(null);

	$effect(() => {
		// Move focus to Cancel by default for irreversible or risk-affecting actions.
		cancelButton?.focus();
		return () => {
			(dialogRef?.ownerDocument.activeElement as HTMLElement | null)?.blur();
		};
	});

	function handleKeydown(event: KeyboardEvent): void {
		if (event.key === 'Escape' && !mutating) {
			event.preventDefault();
			oncancel();
			return;
		}
		if (event.key === 'Tab' && dialogRef) {
			// Minimal focus trap: keep tab cycles among the dialog's focusables.
			const focusables = dialogRef.querySelectorAll<HTMLElement>(
				'button, [href], input, select, textarea'
			);
			if (focusables.length === 0) return;
			const first = focusables[0];
			const last = focusables[focusables.length - 1];
			if (event.shiftKey && document.activeElement === first) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && document.activeElement === last) {
				event.preventDefault();
				first.focus();
			}
		}
	}
</script>

<div class="backdrop">
	<div
		class="dialog"
		role="dialog"
		aria-modal="true"
		aria-labelledby="lifecycle-dialog-title"
		aria-describedby="lifecycle-dialog-body"
		tabindex="-1"
		bind:this={dialogRef}
		onkeydown={handleKeydown}
	>
		<h2 id="lifecycle-dialog-title">{dialog.title}</h2>
		<div id="lifecycle-dialog-body">
			{#each dialog.body as line (line)}
				<p>{line}</p>
			{/each}
		</div>
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
		{#if actionError}
			<p class="dialog-problem" role="alert">{actionError}</p>
		{/if}
		<div class="dialog-actions">
			<button bind:this={cancelButton} type="button" onclick={oncancel} disabled={mutating}>
				Cancel
			</button>
			<button
				type="button"
				class:danger={dialog.danger}
				disabled={mutating}
				onclick={onconfirm}
				aria-live="polite"
			>
				{mutating
					? dialog.pending
					: stopWithFlatten && dialog.chooseStopMode
						? 'Stop and flatten'
						: dialog.confirm}
			</button>
		</div>
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(5, 8, 9, 0.78);
		display: grid;
		place-items: center;
		z-index: 60;
		padding: 16px;
	}
	.dialog {
		width: min(560px, 100%);
		max-height: 90vh;
		overflow-y: auto;
		border: 1px solid #303a3c;
		border-radius: 13px;
		background: #12191b;
		padding: 22px 24px;
		display: grid;
		gap: 12px;
	}
	.dialog h2 {
		margin: 0;
		font-size: 18px;
		color: #e9edf1;
	}
	.dialog p {
		margin: 0 0 6px;
		font-size: 13px;
		color: #c6cfd1;
	}
	.stop-mode {
		border: 1px solid #232d2e;
		border-radius: 8px;
		padding: 10px 12px;
		display: grid;
		gap: 10px;
		margin: 0;
	}
	.stop-mode legend {
		color: #aeb9bb;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		padding: 0 4px;
	}
	.choice {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 10px;
		font-size: 12px;
		color: #aeb9bb;
		cursor: pointer;
	}
	.choice strong {
		display: block;
		color: #e9edf1;
		font-size: 13px;
		margin-bottom: 2px;
	}
	.async-note {
		color: #8d999c;
		font-size: 12px;
	}
	.dialog-problem {
		color: #f0a3a3;
		font-size: 13px;
	}
	.dialog-actions {
		display: flex;
		justify-content: flex-end;
		gap: 10px;
		margin-top: 4px;
	}
	.dialog-actions button {
		border: 1px solid #303a3c;
		background: #151b1d;
		color: #dce4e5;
		border-radius: 8px;
		padding: 9px 14px;
		font: inherit;
		font-size: 13px;
		cursor: pointer;
	}
	.dialog-actions button:hover:not(:disabled) {
		border-color: #5ce1b5;
	}
	.dialog-actions button.danger {
		background: #2c1212;
		border-color: #733d3d;
		color: #ffd4d4;
		font-weight: 600;
	}
	.dialog-actions button:disabled {
		opacity: 0.55;
		cursor: wait;
	}
	@media (max-width: 640px) {
		.backdrop {
			padding: 0;
			align-items: end;
		}
		.dialog {
			border-radius: 13px 13px 0 0;
			max-height: 92vh;
		}
	}
</style>
