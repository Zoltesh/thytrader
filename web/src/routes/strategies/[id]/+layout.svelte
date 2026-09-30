<script lang="ts">
	/**
	 * Strategy workspace shell (ADR 0080, ADR 0082): sticky identity bar plus the
	 * Build · Test · Run · Why stage navigation. Stages are ordinary links
	 * with `aria-current="page"`, not an ARIA tablist.
	 *
	 * A strategy is one mutable object: the bar shows its saved validation state
	 * and the fingerprint its next snapshot gets. Old deep links degrade
	 * gracefully: `?version=` is dropped, and a snapshot fingerprint in place of
	 * the strategy id resolves to the owning strategy.
	 */
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { untrack, type Snippet } from 'svelte';
	import { marketLabel } from '$lib/deployment-detail';
	import { cloneStrategy } from '$lib/strategies';
	import {
		WORKSPACE_STAGES,
		isStrategyFingerprint,
		shortStrategyFingerprint,
		stageFromRouteId,
		workspaceHref,
		type WorkspaceStage
	} from '$lib/strategy-workspace';
	import { resolveStrategyOwner } from '$lib/workspace-data';
	import { StrategyWorkspace, provideWorkspace } from '$lib/workspace/workspace.svelte';

	let { children }: { children: Snippet } = $props();

	const workspace = new StrategyWorkspace();
	provideWorkspace(workspace);

	const routeId = $derived(page.params.id ?? '');
	const stage = $derived<WorkspaceStage>(stageFromRouteId(page.route.id) ?? 'build');
	const record = $derived(workspace.record);
	const model = $derived(workspace.model);

	let actionPending = $state(false);
	let actionError = $state<string | null>(null);
	let copyStatus = $state('');
	/** Set when an old fingerprint link could not be resolved to a live strategy. */
	let legacyProblem = $state<string | null>(null);

	$effect(() => {
		const id = routeId;
		if (id === '') return;
		untrack(() => {
			if (isStrategyFingerprint(id)) {
				void redirectFingerprint(id);
				return;
			}
			legacyProblem = null;
			if (page.url.searchParams.has('version')) {
				// Old `?version=` links: a strategy has one current definition now.
				const url = new URL(page.url);
				url.searchParams.delete('version');
				// eslint-disable-next-line svelte/no-navigation-without-resolve -- same route, param dropped
				void goto(`${url.pathname}${url.search}${url.hash}`, { replaceState: true });
			}
			void workspace.load(id);
		});
	});

	async function redirectFingerprint(fingerprint: string): Promise<void> {
		legacyProblem = null;
		const owner = await resolveStrategyOwner(fingerprint);
		if (owner.kind === 'owned') {
			await goto(resolve(workspaceHref(owner.strategyId, stage)), { replaceState: true });
			return;
		}
		workspace.loading = false;
		legacyProblem =
			owner.kind === 'deleted'
				? `These rules belonged to ${owner.strategyName ?? 'a strategy'} (deleted strategy). Its live history is kept on the Portfolio page.`
				: 'This link names strategy rules that no longer exist on this workstation.';
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
		if (record === null || actionPending) return;
		actionPending = true;
		actionError = null;
		try {
			const created = await cloneStrategy(record.strategy_id);
			await goto(resolve(workspaceHref(created.strategy_id, 'build')));
		} catch (caught) {
			actionError = caught instanceof Error ? caught.message : 'Could not clone the strategy.';
		} finally {
			actionPending = false;
		}
	}

	function exportJson(): void {
		if (record === null) return;
		const blob = new Blob([JSON.stringify(record.document, null, 2)], {
			type: 'application/json'
		});
		const url = URL.createObjectURL(blob);
		const anchor = document.createElement('a');
		anchor.href = url;
		anchor.download = `${record.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase() || 'strategy'}.json`;
		anchor.click();
		URL.revokeObjectURL(url);
	}
</script>

<main class="workspace">
	{#if legacyProblem}
		<div class="error-banner" role="alert" data-testid="workspace-legacy-link">
			<div>
				<strong>Strategy not found</strong>
				<p>{legacyProblem}</p>
			</div>
		</div>
		<a href={resolve('/strategies')}>← Back to strategies</a>
	{:else if workspace.loading && record === null}
		<div class="loading-card" aria-busy="true"><div class="skeleton wide"></div></div>
	{:else if workspace.error && record === null}
		<div class="error-banner" role="alert">
			<div>
				<strong>Strategy workspace unavailable</strong>
				<p>{workspace.error}</p>
			</div>
			<button type="button" onclick={() => void workspace.load(routeId)}>Retry</button>
		</div>
		<a href={resolve('/strategies')}>← Back to strategies</a>
	{:else if record}
		<section class="card identity" aria-label="Strategy identity">
			<div class="idbar">
				<a class="btn ghost back" href={resolve('/strategies')} aria-label="Back to strategies">←</a
				>
				<div class="id-main">
					<h1 data-testid="workspace-name">{workspace.name ?? 'Strategy'}</h1>
					<div class="id-row">
						<span class="pill" data-testid="workspace-validity" data-valid={workspace.valid}>
							{#if workspace.valid}
								<span class="dot" aria-hidden="true"></span>Valid rules
							{:else}
								<span class="dot problem" aria-hidden="true"></span>{workspace.issues.length || 1} problem{workspace
									.issues.length === 1
									? ''
									: 's'}
							{/if}
						</span>
						{#if record.current_fingerprint}
							<span
								class="pill mono"
								title="Fingerprint the next backtest or bot records: {record.current_fingerprint}"
								data-testid="workspace-fingerprint"
								>{shortStrategyFingerprint(record.current_fingerprint)}</span
							>
							<button
								class="btn ghost small"
								type="button"
								onclick={() => void copyFingerprint(record.current_fingerprint!)}
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
					<p class="save-state" data-testid="workspace-save-state">
						Revision {record.revision}{stage === 'build'
							? workspace.dirty
								? ' · unsaved changes'
								: ' · all edits saved'
							: ''}
					</p>
				</div>
				<div class="id-actions">
					<button class="btn" type="button" onclick={exportJson}>Export JSON</button>
					<button class="btn" type="button" disabled={actionPending} onclick={() => void clone()}
						>{actionPending ? 'Cloning…' : 'Clone'}</button
					>
				</div>
			</div>
			<nav class="stages" aria-label="Strategy stages">
				{#each WORKSPACE_STAGES as item (item.id)}
					<a
						class="stage"
						class:on={stage === item.id}
						href={resolve(workspaceHref(record.strategy_id, item.id))}
						aria-current={stage === item.id ? 'page' : undefined}>{item.label}</a
					>
				{/each}
			</nav>
			<p class="sr-only" aria-live="polite">{copyStatus}</p>
		</section>
		{#if actionError}<p class="problem-text" role="alert">{actionError}</p>{/if}
		{@render children()}
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
	.dot.problem {
		background: var(--warn);
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
	.save-state {
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
	.problem-text {
		color: var(--neg);
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
