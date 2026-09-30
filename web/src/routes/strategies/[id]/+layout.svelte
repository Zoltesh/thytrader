<script lang="ts">
	/**
	 * Strategy workspace shell (ADR 0080): sticky identity bar plus the
	 * Build · Test · Run · Why stage navigation. Stages are ordinary links
	 * with `aria-current="page"`, not an ARIA tablist.
	 *
	 * `?version=<strategy_fingerprint>` selects the exact published version.
	 * A fingerprint that does not belong to this strategy fails closed: the
	 * stage content (and every mutation in it) is not rendered.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { untrack, type Snippet } from 'svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import { clonePublishedStrategy, reviseStrategy } from '$lib/strategies';
	import {
		INVALID_VERSION_MESSAGE,
		WORKSPACE_STAGES,
		shortStrategyFingerprint,
		stageFromRouteId,
		workspaceHref,
		type WorkspaceStage
	} from '$lib/strategy-workspace';
	import VersionsDialog from '$lib/workspace/VersionsDialog.svelte';
	import { StrategyWorkspace, provideWorkspace } from '$lib/workspace/workspace.svelte';

	let { children }: { children: Snippet } = $props();

	const workspace = new StrategyWorkspace();
	provideWorkspace(workspace);

	const strategyId = $derived(page.params.id ?? '');
	const stage = $derived<WorkspaceStage>(stageFromRouteId(page.route.id) ?? 'build');
	const history = $derived(workspace.history);
	const draft = $derived(workspace.draft);
	/** Build shows the open draft unless an exact published version is requested. */
	const buildShowsDraft = $derived(
		stage === 'build' && draft !== null && workspace.requestedVersion === null
	);
	const selected = $derived(workspace.version.entry);
	const model = $derived(workspace.identityModel);
	const pickerValue = $derived(buildShowsDraft ? 'draft' : (selected?.strategy_fingerprint ?? ''));

	let versionsOpen = $state(false);
	let actionPending = $state<'clone' | 'revise' | null>(null);
	let actionError = $state<string | null>(null);
	let copyStatus = $state('');

	$effect(() => {
		const id = strategyId;
		if (id === '') return;
		untrack(() => void workspace.load(id));
	});

	$effect(() => {
		const fingerprint = workspace.selectedFingerprint;
		if (fingerprint !== null) untrack(() => void workspace.ensureModel(fingerprint));
	});

	function stageLink(target: WorkspaceStage): `/strategies/${string}` {
		if (target === 'build') {
			return workspaceHref(strategyId, 'build', {
				version: draft === null ? workspace.requestedVersion : null
			});
		}
		return workspaceHref(strategyId, target, { version: workspace.requestedVersion });
	}

	function stageMeta(target: WorkspaceStage): string | null {
		if (target !== 'build') return null;
		if (draft !== null) return `draft v${draft.strategy.version}`;
		const latest = history?.versions[history.versions.length - 1];
		return latest === undefined ? null : `published v${latest.version}`;
	}

	function pickVersion(value: string): void {
		if (value === 'draft') {
			void goto(resolve(workspaceHref(strategyId, 'build')));
			return;
		}
		void goto(resolve(workspaceHref(strategyId, stage, { version: value })));
	}

	async function copyFingerprint(fingerprint: string): Promise<void> {
		try {
			await navigator.clipboard.writeText(fingerprint);
			copyStatus = 'Full fingerprint copied.';
		} catch {
			copyStatus = `Copy unavailable. Full fingerprint: ${fingerprint}`;
		}
	}

	async function clone(): Promise<void> {
		const fingerprint = selected?.strategy_fingerprint ?? workspace.latestFingerprint;
		if (fingerprint === null || actionPending !== null) return;
		actionPending = 'clone';
		actionError = null;
		try {
			const created = await clonePublishedStrategy(fingerprint);
			await goto(resolve(workspaceHref(created.strategy.strategy_id, 'build')));
		} catch (caught) {
			actionError = caught instanceof Error ? caught.message : 'Could not clone the strategy.';
		} finally {
			actionPending = null;
		}
	}

	async function reviseIntoDraft(): Promise<void> {
		const fingerprint = selected?.strategy_fingerprint ?? workspace.latestFingerprint;
		if (fingerprint === null || actionPending !== null) return;
		actionPending = 'revise';
		actionError = null;
		try {
			await reviseStrategy(strategyId, fingerprint);
			await workspace.refresh();
			await goto(resolve(workspaceHref(strategyId, 'build')));
		} catch (caught) {
			actionError = caught instanceof Error ? caught.message : 'Could not create a new draft.';
		} finally {
			actionPending = null;
		}
	}

	async function onRevised(): Promise<void> {
		versionsOpen = false;
		await workspace.refresh();
		await goto(resolve(workspaceHref(strategyId, 'build')));
	}
</script>

<main class="workspace">
	{#if workspace.loading && history === null}
		<div class="loading-card" aria-busy="true"><div class="skeleton wide"></div></div>
	{:else if workspace.error && history === null}
		<div class="error-banner" role="alert">
			<div>
				<strong>Strategy workspace unavailable</strong>
				<p>{workspace.error}</p>
			</div>
			<button type="button" onclick={() => void workspace.load(strategyId)}>Retry</button>
		</div>
		<a href={resolve('/strategies')}>← Back to strategies</a>
	{:else if history}
		<section class="card identity" aria-label="Strategy identity">
			<div class="idbar">
				<a class="btn ghost back" href={resolve('/strategies')} aria-label="Back to strategies">←</a
				>
				<div class="id-main">
					<h1 data-testid="workspace-name">{workspace.name ?? 'Strategy'}</h1>
					<div class="id-row">
						<span class="pill" data-testid="workspace-version-pill">
							{#if buildShowsDraft && draft}
								<span class="dot draft" aria-hidden="true"></span>Draft v{draft.strategy.version}
							{:else if selected}
								<span class="dot" aria-hidden="true"></span>Published v{selected.version}
							{:else if workspace.version.status === 'invalid'}
								Unknown version
							{:else}
								Not published
							{/if}
						</span>
						<label class="picker">
							<span class="sr-only">Version</span>
							<select
								data-testid="workspace-version-picker"
								value={pickerValue}
								onchange={(event) => pickVersion((event.currentTarget as HTMLSelectElement).value)}
							>
								{#if workspace.version.status === 'invalid'}
									<option value="" disabled>Unknown version</option>
								{/if}
								{#each [...history.versions].reverse() as version (version.strategy_fingerprint)}
									<option value={version.strategy_fingerprint}
										>Published v{version.version}{version.archived ? ' · archived' : ''}</option
									>
								{/each}
								{#if draft}
									<option value="draft">Draft v{draft.strategy.version} (open)</option>
								{/if}
							</select>
						</label>
						{#if buildShowsDraft && draft}
							<span class="faint">fingerprint created at publication</span>
						{:else if selected}
							<span class="pill mono" title={selected.strategy_fingerprint}
								>{shortStrategyFingerprint(selected.strategy_fingerprint)}</span
							>
							<button
								class="btn ghost small"
								type="button"
								onclick={() => void copyFingerprint(selected.strategy_fingerprint)}
								>Copy full fingerprint</button
							>
						{/if}
						{#if model}
							<span class="pill" data-testid="workspace-market"
								>{marketLabel(model.product_id)}</span
							>
							<span class="pill" data-testid="workspace-clock">{model.timeframe}</span>
							<span class="faint mono product-record">Coinbase product {model.product_id}</span>
						{/if}
					</div>
					<p class="draft-state" data-testid="workspace-draft-state">
						{#if draft}
							Draft v{draft.strategy.version} open{stage === 'build' && buildShowsDraft
								? workspace.draftDirty
									? ' · unsaved changes'
									: ' · all edits saved'
								: ''}
						{:else}
							No editable draft
							<button
								class="btn ghost small"
								type="button"
								disabled={actionPending !== null || workspace.latestFingerprint === null}
								onclick={() => void reviseIntoDraft()}
								>{actionPending === 'revise' ? 'Creating draft…' : 'Revise into new draft'}</button
							>
						{/if}
					</p>
				</div>
				<div class="id-actions">
					<button class="btn" type="button" onclick={() => (versionsOpen = true)}>Versions</button>
					<button
						class="btn"
						type="button"
						disabled={actionPending !== null || workspace.latestFingerprint === null}
						title={workspace.latestFingerprint === null
							? 'Publish a version before cloning'
							: undefined}
						onclick={() => void clone()}>{actionPending === 'clone' ? 'Cloning…' : 'Clone'}</button
					>
				</div>
			</div>
			<nav class="stages" aria-label="Strategy stages">
				{#each WORKSPACE_STAGES as item (item.id)}
					{@const meta = stageMeta(item.id)}
					<a
						class="stage"
						class:on={stage === item.id}
						href={resolve(stageLink(item.id))}
						aria-current={stage === item.id ? 'page' : undefined}
						>{item.label}{#if meta}<span class="n">{meta}</span>{/if}</a
					>
				{/each}
			</nav>
			<p class="sr-only" aria-live="polite">{copyStatus}</p>
		</section>
		{#if actionError}<p class="problem" role="alert">{actionError}</p>{/if}
		{#if workspace.version.status === 'invalid'}
			<div class="error-banner invalid" role="alert" data-testid="workspace-invalid-version">
				<div>
					<strong>Version not found for this strategy</strong>
					<p>{INVALID_VERSION_MESSAGE}</p>
					<p class="mono requested">Requested: {workspace.version.requested}</p>
				</div>
				<a class="btn" href={resolve(workspaceHref(strategyId, stage))}>Open latest version</a>
			</div>
		{:else}
			{@render children()}
		{/if}
		<VersionsDialog
			open={versionsOpen}
			{strategyId}
			name={workspace.name ?? 'Strategy'}
			{history}
			onclose={() => (versionsOpen = false)}
			onrevised={() => void onRevised()}
		/>
	{/if}
</main>

<style>
	.identity {
		position: sticky;
		top: var(--topbar-height);
		z-index: 10;
		margin-bottom: var(--space-4);
	}
	.idbar {
		display: flex;
		align-items: flex-start;
		flex-wrap: wrap;
		gap: 12px;
		padding: 14px 18px;
	}
	.back {
		width: 34px;
		padding: 0;
		font-size: 16px;
	}
	.id-main {
		min-width: 0;
		flex: 1;
	}
	h1 {
		margin: 0;
		font-size: var(--fs-xl);
		letter-spacing: -0.01em;
	}
	.id-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-top: 6px;
	}
	.pill {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		height: 24px;
		padding: 0 9px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		font-size: var(--fs-sm);
		white-space: nowrap;
	}
	.pill.mono {
		font-family: var(--font-mono);
	}
	.dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--accent);
	}
	.dot.draft {
		background: var(--warn);
	}
	.picker select {
		height: 26px;
		padding: 0 6px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-sm);
		background: var(--surface);
		color: var(--text);
		font-size: var(--fs-sm);
	}
	.btn.small {
		min-height: 26px;
		padding: 0 8px;
		font-size: var(--fs-sm);
	}
	.faint {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.product-record {
		font-size: var(--fs-xs);
	}
	.draft-state {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 6px 0 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.id-actions {
		display: flex;
		gap: 8px;
	}
	.stages {
		display: flex;
		gap: 4px;
		padding: 0 10px;
		border-top: 1px solid var(--line);
	}
	.stage {
		display: flex;
		align-items: center;
		gap: 8px;
		min-height: 44px;
		margin-bottom: -1px;
		padding: 0 14px;
		border-bottom: 2px solid transparent;
		color: var(--muted);
		font-weight: 500;
		text-decoration: none;
	}
	.stage:hover {
		color: var(--text);
	}
	.stage.on {
		border-bottom-color: var(--accent);
		color: var(--text);
	}
	.stage .n {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.problem {
		color: var(--neg);
	}
	.invalid {
		align-items: flex-start;
		gap: 16px;
	}
	.requested {
		font-size: var(--fs-xs);
		word-break: break-all;
	}
	@media (max-width: 720px) {
		.identity {
			position: static;
		}
		.stages {
			overflow-x: visible;
			flex-wrap: wrap;
		}
	}
</style>
