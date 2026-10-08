<script lang="ts">
	/** Paste-a-definition import dialog; the page parses, imports, and opens the new strategy. */
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';

	let {
		open,
		importText = $bindable(),
		pending,
		importHint,
		oncancel,
		onconfirm
	}: {
		open: boolean;
		/** The pasted strategy definition JSON. */
		importText: string;
		pending: boolean;
		/** Why the last import did not go through (invalid JSON or a server problem). */
		importHint: string | null;
		oncancel: () => void;
		onconfirm: () => void;
	} = $props();
</script>

<ConfirmDialog
	{open}
	title="Import strategy JSON"
	confirmLabel="Import strategy"
	pendingLabel="Importing…"
	{pending}
	confirmDisabled={importText.trim().length === 0}
	confirmDisabledReason="Paste a strategy definition first."
	error={importHint}
	testId="import-dialog"
	{oncancel}
	{onconfirm}
>
	<p>
		Paste one strategy definition. It becomes a new strategy with its own identity; work in progress
		with problems is saved too and shows its problems in Build.
	</p>
	<textarea
		bind:value={importText}
		rows={12}
		spellcheck="false"
		aria-label="Strategy definition JSON"></textarea>
</ConfirmDialog>

<style>
	textarea {
		width: 100%;
		padding: 12px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		font-family: var(--font-mono);
		font-size: var(--fs-sm);
		resize: vertical;
	}
</style>
