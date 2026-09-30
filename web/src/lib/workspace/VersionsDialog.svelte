<script lang="ts">
	/**
	 * Version history for one strategy identity (moved from the old library
	 * drawer's Versions tab): published versions, export, semantic diff, and
	 * "Edit into next draft". Navigation links open the workspace stage for an
	 * exact fingerprint; nothing here starts a runtime.
	 */
	import { resolve } from '$app/paths';
	import { tick } from 'svelte';
	import { formatPercent } from '$lib/backtests';
	import {
		fetchStrategySource,
		formatUtcInputValue,
		reviseStrategy,
		toBuilderModel,
		type BuilderModel,
		type StrategyVersionHistory
	} from '$lib/strategies';
	import { semanticDiff, type SemanticDiff } from '$lib/strategy-diff';
	import { shortStrategyFingerprint, workspaceHref } from '$lib/strategy-workspace';

	let {
		open,
		strategyId,
		name,
		history,
		onclose,
		onrevised
	}: {
		open: boolean;
		strategyId: string;
		name: string;
		history: StrategyVersionHistory | null;
		onclose: () => void;
		/** Called after a new draft was created from a published version. */
		onrevised: () => void;
	} = $props();

	let dialog: HTMLDialogElement | undefined = $state();
	let closeButton: HTMLButtonElement | undefined = $state();
	let returnFocus: HTMLElement | null = null;
	let revising = $state<string | null>(null);
	let exporting = $state(false);
	let problem = $state<string | null>(null);
	let diffSelection = $state<{ from: number; to: number }>({ from: 0, to: 0 });
	let diffCache: Record<string, BuilderModel> = {};

	$effect(() => {
		const element = dialog;
		if (element === undefined) return;
		if (open && !element.open) {
			returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
			problem = null;
			const versions = history?.versions ?? [];
			diffSelection =
				versions.length >= 2
					? {
							from: versions[versions.length - 2].version,
							to: versions[versions.length - 1].version
						}
					: { from: versions[0]?.version ?? 0, to: versions[0]?.version ?? 0 };
			element.showModal();
			void tick().then(() => closeButton?.focus());
		} else if (!open && element.open) {
			element.close();
			if (returnFocus?.isConnected) returnFocus.focus();
			returnFocus = null;
		}
	});

	function onCancelEvent(event: Event): void {
		event.preventDefault();
		if (revising === null) onclose();
	}

	async function modelFor(fingerprint: string): Promise<BuilderModel> {
		const cached = diffCache[fingerprint];
		if (cached !== undefined) return cached;
		const model = toBuilderModel(await fetchStrategySource(fingerprint), 0);
		diffCache = { ...diffCache, [fingerprint]: model };
		return model;
	}

	type DiffView =
		| { status: 'same-version' }
		| { status: 'unavailable' }
		| { status: 'ready'; diff: SemanticDiff };

	async function currentDiff(selection: { from: number; to: number }): Promise<DiffView> {
		const versions = history?.versions ?? [];
		const from = versions.find((version) => version.version === Number(selection.from));
		const to = versions.find((version) => version.version === Number(selection.to));
		if (from === undefined || to === undefined) return { status: 'unavailable' };
		if (from.version === to.version) return { status: 'same-version' };
		try {
			const [before, after] = await Promise.all([
				modelFor(from.strategy_fingerprint),
				modelFor(to.strategy_fingerprint)
			]);
			return { status: 'ready', diff: semanticDiff(before, after) };
		} catch {
			return { status: 'unavailable' };
		}
	}

	async function revise(fingerprint: string): Promise<void> {
		if (revising !== null) return;
		revising = fingerprint;
		problem = null;
		try {
			await reviseStrategy(strategyId, fingerprint);
			onrevised();
		} catch (caught) {
			problem = caught instanceof Error ? caught.message : 'Could not create a new draft.';
		} finally {
			revising = null;
		}
	}

	async function exportVersion(fingerprint: string, version: number): Promise<void> {
		if (exporting) return;
		exporting = true;
		problem = null;
		try {
			const source = await fetchStrategySource(fingerprint);
			const blob = new Blob([JSON.stringify(source, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = `${name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}-v${version}.json`;
			anchor.click();
			URL.revokeObjectURL(url);
		} catch (caught) {
			problem =
				caught instanceof Error ? caught.message : 'Could not export the strategy definition.';
		} finally {
			exporting = false;
		}
	}
</script>

<dialog
	bind:this={dialog}
	class="versions-dialog"
	aria-labelledby="versions-title"
	oncancel={onCancelEvent}
>
	{#if open}
		<div class="head">
			<div>
				<h2 id="versions-title">Versions</h2>
				<p class="faint">{name}</p>
			</div>
			<button
				bind:this={closeButton}
				class="btn ghost"
				type="button"
				disabled={revising !== null}
				onclick={onclose}>Close</button
			>
		</div>
		<div class="body">
			{#if problem}<p class="problem" role="alert">{problem}</p>{/if}
			{#if history === null}
				<p class="muted">Version history is unavailable.</p>
			{:else}
				<h3>Published versions</h3>
				{#if history.versions.length === 0}
					<p class="muted">No immutable published versions yet.</p>
				{:else}
					<div class="table-wrap">
						<table class="versions-table" aria-label="Published version history">
							<thead>
								<tr>
									<th scope="col">Version</th>
									<th scope="col">Fingerprint</th>
									<th scope="col">Status</th>
									<th scope="col">Latest backtest</th>
									<th scope="col">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each history.versions as version (version.version)}
									{@const latest = history.versions[history.versions.length - 1]}
									<tr>
										<td>V{version.version}</td>
										<td
											><code title={version.strategy_fingerprint}
												>{shortStrategyFingerprint(version.strategy_fingerprint)}</code
											></td
										>
										<td>
											{version.archived
												? `archived${version.archived_at ? ` · ${formatUtcInputValue(new Date(version.archived_at)).slice(0, 10)}` : ''}`
												: 'active'}
										</td>
										<td>
											{#if version.backtest}
												<a
													href={resolve(
														workspaceHref(strategyId, 'test', {
															version: version.strategy_fingerprint,
															result: version.backtest.result_fingerprint
														})
													)}>{formatPercent(version.backtest.summary.total_return_fraction)}</a
												>
											{:else}
												<span class="muted">None</span>
											{/if}
										</td>
										<td>
											<div class="actions">
												<button
													class="btn"
													type="button"
													disabled={revising !== null}
													onclick={() => void revise(version.strategy_fingerprint)}
													>{revising === version.strategy_fingerprint
														? 'Creating…'
														: 'Edit into next draft'}</button
												>
												<button
													class="btn"
													type="button"
													disabled={exporting}
													onclick={() =>
														void exportVersion(version.strategy_fingerprint, version.version)}
													>{exporting ? 'Exporting…' : 'Export'}</button
												>
												<button
													class="btn"
													type="button"
													disabled={version.strategy_fingerprint === latest.strategy_fingerprint}
													onclick={() =>
														(diffSelection = { from: version.version, to: latest.version })}
													>Compare to latest</button
												>
												<a
													class="btn"
													href={resolve(
														workspaceHref(strategyId, 'test', {
															version: version.strategy_fingerprint
														})
													)}
													onclick={onclose}
													aria-label="Open version {version.version} in the workspace"
													>Open v{version.version}</a
												>
											</div>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
				{#if history.draft}
					<p class="muted">
						Draft v{history.draft.strategy.version} is open for this strategy.
						<a href={resolve(workspaceHref(strategyId, 'build'))} onclick={onclose}
							>Open draft v{history.draft.strategy.version}</a
						>
					</p>
				{/if}
				{#if history.versions.length >= 2}
					<h3>Semantic diff</h3>
					<div class="diff-pickers">
						<label
							>From version
							<select bind:value={diffSelection.from}>
								{#each history.versions as version (version.version)}
									<option value={version.version}>V{version.version}</option>
								{/each}
							</select></label
						>
						<label
							>To version
							<select bind:value={diffSelection.to}>
								{#each history.versions as version (version.version)}
									<option value={version.version}>V{version.version}</option>
								{/each}
							</select></label
						>
					</div>
					{#await currentDiff({ ...diffSelection })}
						<p class="muted">Comparing versions…</p>
					{:then result}
						{#if result.status === 'same-version'}
							<p class="muted">Select two different versions to compare.</p>
						{:else if result.status === 'unavailable'}
							<p class="problem" role="alert">
								Could not load the selected versions for comparison.
							</p>
						{:else if result.diff.changes.length === 0}
							<p class="muted">These versions are semantically equivalent.</p>
						{:else}
							<p class="muted">{result.diff.summary}</p>
							<table class="versions-table" aria-label="Semantic diff">
								<thead>
									<tr>
										<th scope="col">Field</th>
										<th scope="col">From</th>
										<th scope="col">To</th>
									</tr>
								</thead>
								<tbody>
									{#each result.diff.changes as change (change.path + change.kind)}
										<tr>
											<td>{change.label}</td>
											<td><code>{change.from === '' ? '—' : change.from}</code></td>
											<td><code>{change.to === '' ? '—' : change.to}</code></td>
										</tr>
									{/each}
								</tbody>
							</table>
						{/if}
					{/await}
				{/if}
			{/if}
		</div>
	{/if}
</dialog>

<style>
	.versions-dialog {
		width: min(920px, calc(100vw - 32px));
		max-height: 86vh;
		padding: 0;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-xl);
		background: var(--surface);
		color: var(--text);
		box-shadow: var(--shadow);
	}
	.versions-dialog::backdrop {
		background: var(--scrim);
	}
	.head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 12px;
		padding: 18px 20px;
		border-bottom: 1px solid var(--line);
	}
	.head h2 {
		font-size: var(--fs-lg);
	}
	.head p {
		margin: 2px 0 0;
	}
	.body {
		display: grid;
		gap: 12px;
		padding: 18px 20px;
	}
	h3 {
		margin: 6px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
		font-weight: 500;
		letter-spacing: 0.05em;
		text-transform: uppercase;
	}
	.body p {
		margin: 0;
	}
	.faint {
		color: var(--faint);
	}
	.muted {
		color: var(--muted);
	}
	.problem {
		color: var(--neg);
	}
	.versions-table th,
	.versions-table td {
		padding: 8px 10px 8px 0;
		text-align: left;
		vertical-align: middle;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.actions .btn {
		min-height: 30px;
		font-size: var(--fs-sm);
	}
	.diff-pickers {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 10px;
	}
	.diff-pickers label {
		display: grid;
		gap: 4px;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	select {
		min-height: 34px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		color: var(--text);
		padding: 0 8px;
	}
</style>
