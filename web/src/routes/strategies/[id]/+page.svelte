<script lang="ts">
	/**
	 * Build stage (ADR 0082): edit the strategy and save it in place.
	 *
	 * Saves carry the revision the form was loaded from; a stale save is
	 * rejected by the server (`strategy_revision_conflict`) and never
	 * overwrites. Invalid work in progress can be saved; the saved validation
	 * result is shown, and Test / Run stay blocked until it is valid. Leaving
	 * with unsaved edits asks first (in-app navigation and browser unload).
	 */
	import { beforeNavigate, goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import ConfirmDialog from '$lib/ConfirmDialog.svelte';
	import {
		builderModelFromRecord,
		fromBuilderModel,
		saveStrategy,
		strategyErrorCode,
		StrategyApiError,
		type BuilderModel,
		type StrategyDocument,
		type StrategyRecord
	} from '$lib/strategies';
	import { validateDefinition } from '$lib/strategy-insight';
	import BuildInspector from '$lib/workspace/BuildInspector.svelte';
	import DefinitionForm from '$lib/workspace/DefinitionForm.svelte';
	import { useWorkspace } from '$lib/workspace/workspace.svelte';

	const workspace = useWorkspace();

	let model = $state<BuilderModel | null>(null);
	/** Raw JSON editor for a saved document the form cannot represent. */
	let rawText = $state('');
	let rawMode = $state(false);
	let revision = $state(0);
	let modelKey = '';
	let saving = $state(false);
	let dirty = $state(false);
	let error = $state<string | null>(null);
	let conflict = $state<{ currentRevision: number | null } | null>(null);
	let savedAt = $state<string | null>(null);
	let validationErrors = $state<string[]>([]);
	let leaveTarget = $state<URL | null>(null);
	let leaving = false;

	const record = $derived(workspace.record);
	const savedIssues = $derived(record?.validation.issues ?? []);
	const savedValid = $derived(record?.validation.valid === true);

	$effect(() => {
		const current = record;
		const key = current === null ? '' : `${current.strategy_id}`;
		untrack(() => {
			if (key === modelKey) return;
			modelKey = key;
			if (current === null) {
				model = null;
				return;
			}
			load(current);
		});
	});

	function load(current: StrategyRecord): void {
		revision = current.revision;
		const next = builderModelFromRecord(current);
		model = next;
		rawMode = next === null;
		rawText = JSON.stringify(current.document, null, 2);
		dirty = false;
		conflict = null;
		validate();
	}

	$effect(() => {
		workspace.dirty = dirty;
		workspace.draftName = model?.name ?? null;
		return () => {
			workspace.dirty = false;
			workspace.draftName = null;
		};
	});

	$effect(() => {
		if (!dirty) return;
		const onBeforeUnload = (event: BeforeUnloadEvent): void => {
			event.preventDefault();
		};
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
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

	function currentDocument(): StrategyDocument | string {
		if (!rawMode && model !== null) return fromBuilderModel(model) as StrategyDocument;
		try {
			const parsed: unknown = JSON.parse(rawText);
			if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
				return 'The document must be one JSON object.';
			}
			return parsed as StrategyDocument;
		} catch {
			return 'That is not valid JSON.';
		}
	}

	async function save(): Promise<void> {
		if (record === null || saving) return;
		const document = currentDocument();
		if (typeof document === 'string') {
			error = document;
			return;
		}
		saving = true;
		error = null;
		const sent = JSON.stringify(document);
		try {
			const saved = await saveStrategy(record.strategy_id, document, revision);
			revision = saved.revision;
			const liveDocument = currentDocument();
			// Keep edits made while the request was in flight.
			const unchanged = typeof liveDocument !== 'string' && JSON.stringify(liveDocument) === sent;
			workspace.accept(saved);
			modelKey = saved.strategy_id;
			if (unchanged) {
				const next = builderModelFromRecord(saved);
				if (next !== null && !rawMode) model = next;
				rawText = JSON.stringify(saved.document, null, 2);
				dirty = false;
			} else if (model !== null) {
				model.revision = saved.revision;
			}
			savedAt = new Date().toLocaleTimeString();
			validate();
		} catch (caught) {
			if (strategyErrorCode(caught) === 'strategy_revision_conflict') {
				const detail = caught instanceof StrategyApiError ? caught.detail : {};
				conflict = {
					currentRevision:
						typeof detail.current_revision === 'number' ? detail.current_revision : null
				};
			} else {
				error = caught instanceof Error ? caught.message : 'Could not save the strategy.';
			}
		} finally {
			saving = false;
		}
	}

	async function reloadLatest(): Promise<void> {
		await workspace.refresh();
		if (workspace.record !== null) load(workspace.record);
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

{#snippet saveControls()}
	<p class="save-state">
		{#if dirty}<span class="dirty-pill">Unsaved changes</span>
		{:else}<span class="saved-ok">All edits saved</span>{/if}
		{#if savedAt}<span class="saved">Saved {savedAt}</span>{/if}
	</p>
	<div class="saved-validation" data-testid="saved-validation" data-valid={savedValid}>
		{#if savedValid}
			<p class="ok-text">Saved definition is valid. Test and Run use these rules.</p>
		{:else}
			<p class="warn-text">
				Saved definition has {savedIssues.length || 1} problem{savedIssues.length === 1 ? '' : 's'}.
				Backtests, studies, and bots cannot start until it is valid.
			</p>
			<ul class="issues">
				{#each savedIssues as issue (issue.loc + issue.message)}
					<li><code>{issue.loc}</code> {issue.message}</li>
				{/each}
			</ul>
		{/if}
	</div>
	<div class="buttons">
		<button
			class="btn primary grow"
			type="button"
			onclick={() => void save()}
			disabled={saving || conflict !== null}
			data-testid="save-strategy">{saving ? 'Saving…' : 'Save'}</button
		>
	</div>
	{#if validationErrors.length > 0}
		<p class="note">
			You can save work in progress with problems; it stays blocked from Test and Run until fixed.
		</p>
	{/if}
	<p class="note">
		Saving updates this strategy in place. Backtests and bots already started keep the rules they
		started with.
	</p>
{/snippet}

{#if record}
	{#if conflict}
		<div class="error-banner" role="alert" data-testid="save-conflict">
			<div>
				<strong>Not saved: this strategy changed elsewhere</strong>
				<p>
					Someone saved a newer revision{conflict.currentRevision !== null
						? ` (revision ${conflict.currentRevision})`
						: ''} after you opened it. Nothing was overwritten. Your edits are still in the form; reload
					the latest to continue (this discards your unsaved edits).
				</p>
			</div>
			<button type="button" onclick={() => void reloadLatest()}>Reload latest</button>
		</div>
	{/if}
	{#if error}
		<div class="error-banner" role="alert">
			<div>
				<strong>Strategy not saved</strong>
				<p>{error}</p>
			</div>
		</div>
	{/if}
	{#if !rawMode && model}
		<div class="build-grid">
			<DefinitionForm bind:model onchange={onEdit} />
			<BuildInspector {model} {validationErrors}>
				{#snippet actions()}
					{@render saveControls()}
				{/snippet}
			</BuildInspector>
		</div>
	{:else}
		<div class="build-grid">
			<section class="card raw" aria-labelledby="raw-title">
				<h2 id="raw-title">Definition JSON</h2>
				<p class="note">
					This saved document is missing parts the form needs, so it is shown as JSON. Fix it here
					and save; the form returns once the document has every block.
				</p>
				<textarea
					bind:value={rawText}
					oninput={() => (dirty = true)}
					rows={24}
					spellcheck="false"
					aria-label="Strategy definition JSON"
					data-testid="raw-definition"></textarea>
			</section>
			<aside class="card block raw-side" aria-label="Save">
				{@render saveControls()}
			</aside>
		</div>
	{/if}
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
	<p>This strategy has edits that are not saved. Leaving discards them.</p>
</ConfirmDialog>

<style>
	.build-grid {
		display: grid;
		grid-template-columns: minmax(0, 1.6fr) minmax(300px, 1fr);
		gap: var(--space-4);
		align-items: start;
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
	.saved,
	.ok-text {
		color: var(--pos);
		font-size: var(--fs-sm);
	}
	.warn-text {
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.saved-validation p {
		margin: 0;
	}
	.issues {
		margin: 4px 0 0;
		padding-left: 18px;
		color: var(--muted);
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
	.raw,
	.raw-side {
		display: grid;
		gap: 10px;
		padding: 16px;
	}
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
	@media (max-width: 1100px) {
		.build-grid {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
