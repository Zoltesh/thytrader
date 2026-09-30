<script lang="ts">
	/**
	 * Build stage: edit the open draft (revision-guarded saves, validation,
	 * publish behind an explicit confirmation), or read an immutable published
	 * definition in the same layout when there is no draft or `?version=` asks
	 * for one. Publishing never starts trading.
	 */
	import { beforeNavigate, goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { untrack } from 'svelte';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import {
		fromBuilderModel,
		publishDraft,
		saveDraft,
		toBuilderModel,
		type BuilderModel
	} from '$lib/strategies';
	import { validateDefinition } from '$lib/strategy-insight';
	import { workspaceHref } from '$lib/strategy-workspace';
	import BuildInspector from '$lib/workspace/BuildInspector.svelte';
	import DefinitionForm from '$lib/workspace/DefinitionForm.svelte';
	import { useWorkspace } from '$lib/workspace/workspace.svelte';

	const workspace = useWorkspace();

	let model = $state<BuilderModel | null>(null);
	let modelKey = '';
	let saving = $state(false);
	let publishing = $state(false);
	let dirty = $state(false);
	let error = $state<string | null>(null);
	let savedAt = $state<string | null>(null);
	let validationErrors = $state<string[]>([]);
	let publishOpen = $state(false);
	let published = $state<{ version: number; fingerprint: string } | null>(null);
	let leaveTarget = $state<URL | null>(null);
	let leaving = false;

	const editingDraft = $derived(workspace.draft !== null && workspace.requestedVersion === null);
	const readonlyModel = $derived(editingDraft ? null : workspace.selectedModel);
	const readonlyVersion = $derived(workspace.version.entry);
	const blockReason = $derived(
		validationErrors.length > 0
			? `Fix ${validationErrors.length} definition problem${validationErrors.length === 1 ? '' : 's'} before saving or publishing.`
			: null
	);

	$effect(() => {
		const draft = workspace.draft;
		const key =
			editingDraft && draft !== null ? `${workspace.strategyId}:${draft.strategy.version}` : '';
		untrack(() => {
			if (key === modelKey) return;
			modelKey = key;
			if (key === '' || draft === null) {
				model = null;
				return;
			}
			model = toBuilderModel(draft.strategy, draft.revision);
			dirty = false;
			validate();
		});
	});

	$effect(() => {
		workspace.draftDirty = dirty;
		workspace.draftName = model?.name ?? null;
		return () => {
			workspace.draftDirty = false;
			workspace.draftName = null;
		};
	});

	beforeNavigate((navigation) => {
		if (!dirty || leaving || navigation.to === null) return;
		if (navigation.type === 'leave') {
			navigation.cancel();
			return;
		}
		navigation.cancel();
		leaveTarget = navigation.to.url;
	});

	function validate(): void {
		validationErrors = model === null ? [] : validateDefinition(model);
	}

	function onEdit(): void {
		dirty = true;
		validate();
	}

	async function save(): Promise<void> {
		if (!model || saving || publishing || validationErrors.length > 0) return;
		saving = true;
		error = null;
		const snapshot = fromBuilderModel(model);
		const snapshotRevision = model.revision;
		try {
			const saved = await saveDraft(snapshot, snapshotRevision);
			const responseModel = toBuilderModel(saved.strategy, saved.revision);
			// Preserve edits made while the request was in flight: only accept the
			// server response when the live model is still the snapshot we sent.
			if (JSON.stringify(fromBuilderModel(model)) === JSON.stringify(snapshot)) {
				model = responseModel;
				dirty = false;
			} else {
				model.revision = saved.revision;
			}
			// Keep the shared history on the new revision so a later stage visit
			// does not reload a stale revision into the builder.
			if (workspace.history?.draft) {
				workspace.history = {
					...workspace.history,
					draft: { ...workspace.history.draft, strategy: saved.strategy, revision: saved.revision }
				};
			}
			savedAt = new Date().toLocaleTimeString();
			validate();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Could not save the strategy draft.';
		} finally {
			saving = false;
		}
	}

	async function publish(): Promise<void> {
		if (!model || publishing || saving || validationErrors.length > 0) return;
		publishing = true;
		error = null;
		try {
			const result = await publishDraft(fromBuilderModel(model), model.revision);
			published = { version: model.version, fingerprint: result.strategy_fingerprint };
			publishOpen = false;
			dirty = false;
			model = null;
			modelKey = '';
			await workspace.refresh();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Strategy publication failed.';
		} finally {
			publishing = false;
		}
	}

	async function copy(text: string): Promise<void> {
		try {
			await navigator.clipboard.writeText(text);
		} catch {
			/* the full value stays visible and selectable */
		}
	}

	async function leaveAnyway(): Promise<void> {
		const target = leaveTarget;
		leaveTarget = null;
		if (target === null) return;
		leaving = true;
		dirty = false;
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- target came from SvelteKit's own navigation
		await goto(`${target.pathname}${target.search}${target.hash}`);
		leaving = false;
	}
</script>

<svelte:head><title>Build · {workspace.name ?? 'Strategy'} · ThyTrader</title></svelte:head>

{#if published}
	<section class="card success" role="status" data-testid="publish-success">
		<h2>Published v{published.version}</h2>
		<p>
			This version is now immutable. Research and runtimes use its fingerprint. Publishing does not
			start trading.
		</p>
		<p class="fingerprint">
			<code>{published.fingerprint}</code>
			<button
				class="btn ghost small"
				type="button"
				onclick={() => void copy(published!.fingerprint)}>Copy full fingerprint</button
			>
		</p>
		<div class="success-actions">
			<a
				class="btn"
				href={resolve(
					workspaceHref(workspace.strategyId, 'build', { version: published.fingerprint })
				)}
				onclick={() => (published = null)}>View published version</a
			>
			<a
				class="btn primary"
				href={resolve(
					workspaceHref(workspace.strategyId, 'test', { version: published.fingerprint })
				)}>Set up a backtest</a
			>
			<a class="btn ghost" href={resolve('/strategies')}>Back to strategies</a>
		</div>
	</section>
{:else if model}
	{@const draftModel = model}
	{#if error}
		<div class="error-banner" role="alert">
			<div>
				<strong>Draft not saved</strong>
				<p>{error}</p>
			</div>
		</div>
	{/if}
	<div class="build-grid">
		<DefinitionForm bind:model onchange={onEdit} />
		<BuildInspector {model} {validationErrors}>
			{#snippet actions()}
				<p class="save-state">
					{#if dirty}<span class="dirty-pill">Unsaved changes</span>
					{:else}<span class="saved-ok">All edits saved</span>{/if}
					{#if savedAt}<span class="saved">Saved {savedAt}</span>{/if}
				</p>
				<div class="buttons">
					<button
						class="btn"
						type="button"
						onclick={() => void save()}
						disabled={saving || publishing || validationErrors.length > 0}
						aria-describedby={blockReason ? 'build-block-reason' : undefined}
						>{saving ? 'Saving…' : 'Save draft'}</button
					>
					<button
						class="btn primary grow"
						type="button"
						onclick={() => (publishOpen = true)}
						disabled={saving || publishing || validationErrors.length > 0}
						aria-describedby={blockReason ? 'build-block-reason' : undefined}
						>Publish v{draftModel.version}…</button
					>
				</div>
				{#if blockReason}<p class="note" id="build-block-reason">{blockReason}</p>{/if}
				<p class="note">
					Publishing locks v{draftModel.version} so it can't be edited. It doesn't start trading.
				</p>
			{/snippet}
		</BuildInspector>
	</div>
	<ConfirmDialog
		open={publishOpen}
		title="Publish immutable strategy version?"
		confirmLabel="Publish version"
		pendingLabel="Publishing…"
		pending={publishing}
		{error}
		testId="publish-dialog"
		oncancel={() => (publishOpen = false)}
		onconfirm={() => void publish()}
	>
		<p><strong>{draftModel.name}</strong> · Version {draftModel.version}</p>
		<p>{marketLabel(draftModel.product_id)} · {draftModel.timeframe}</p>
		<p>
			Publishing creates an immutable fingerprint used by research and runtimes. This version cannot
			be edited. Further changes require a new draft version. It does not start paper or live
			trading.
		</p>
		<div class="row">
			<span>Validation</span><span
				>{validationErrors.length === 0
					? 'No blocking definition errors'
					: validationErrors.join(' ')}</span
			>
		</div>
		<div class="row"><span>Canonical product record</span><code>{draftModel.product_id}</code></div>
		<div class="row"><span>Expected fingerprint</span><span>Created after publication</span></div>
	</ConfirmDialog>
{:else if readonlyModel && readonlyVersion}
	<div class="read-only-banner" role="status">
		<div>
			<strong
				>{workspace.draft === null
					? 'No editable draft'
					: `Published v${readonlyVersion.version}`}</strong
			>
			<p>
				Published v{readonlyVersion.version} is immutable and shown read-only. Use
				<em>Revise into new draft</em> (or Versions → Edit into next draft) to keep editing.
			</p>
		</div>
		{#if workspace.draft !== null}
			<a class="btn" href={resolve(workspaceHref(workspace.strategyId, 'build'))}
				>Open draft v{workspace.draft.strategy.version}</a
			>
		{/if}
	</div>
	<div class="build-grid">
		<DefinitionForm model={readonlyModel} readonly onchange={() => undefined} />
		<BuildInspector model={readonlyModel} validationErrors={validateDefinition(readonlyModel)}>
			{#snippet actions()}
				<p class="note">
					Published v{readonlyVersion.version} · immutable. Publishing and editing do not apply to this
					version.
				</p>
			{/snippet}
		</BuildInspector>
	</div>
{:else if workspace.version.status === 'none' && workspace.draft === null}
	<div class="empty-state">
		<h2>Nothing to build yet</h2>
		<p>This strategy has no open draft and no published version.</p>
	</div>
{:else if readonlyVersion && workspace.modelErrors[readonlyVersion.strategy_fingerprint]}
	<div class="error-banner" role="alert">
		<div>
			<strong>Published definition unavailable</strong>
			<p>{workspace.modelErrors[readonlyVersion.strategy_fingerprint]}</p>
		</div>
	</div>
{:else}
	<div class="loading-card" aria-busy="true"><div class="skeleton wide"></div></div>
{/if}

<ConfirmDialog
	open={leaveTarget !== null}
	title="Leave with unsaved changes?"
	tone="danger"
	confirmLabel="Discard changes and leave"
	pendingLabel="Leaving…"
	oncancel={() => (leaveTarget = null)}
	onconfirm={() => void leaveAnyway()}
>
	<p>This draft has edits that are not saved. Leaving discards them.</p>
</ConfirmDialog>

<style>
	.build-grid {
		display: grid;
		grid-template-columns: minmax(0, 1.6fr) minmax(300px, 1fr);
		gap: var(--space-4);
		align-items: start;
	}
	.read-only-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin-bottom: var(--space-4);
		padding: 14px 17px;
		border: 1px solid var(--accent-line);
		border-radius: var(--radius-lg);
		background: var(--accent-soft);
	}
	.read-only-banner p {
		margin: 4px 0 0;
		color: var(--muted);
	}
	.save-state {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin: 0;
	}
	.dirty-pill {
		padding: 2px 10px;
		border: 1px solid var(--warn-line);
		border-radius: var(--radius-pill);
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.saved-ok,
	.saved {
		color: var(--pos);
		font-size: var(--fs-sm);
	}
	.buttons {
		display: flex;
		gap: 8px;
	}
	.grow {
		flex: 1;
	}
	.note {
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.success {
		display: grid;
		gap: 10px;
		padding: 18px 20px;
		border-color: var(--accent-line);
	}
	.success p {
		margin: 0;
		color: var(--muted);
	}
	.fingerprint {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		word-break: break-all;
	}
	.btn.small {
		min-height: 26px;
		font-size: var(--fs-sm);
	}
	.success-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	@media (max-width: 1100px) {
		.build-grid {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
