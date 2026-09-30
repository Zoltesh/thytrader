<script lang="ts">
	/**
	 * "What changed": the semantic diff from the rules a run or bot used
	 * (its snapshot) to the strategy's current definition. Reuses the shared
	 * strategy-diff helper; nothing is inferred when a side cannot be loaded.
	 */
	import { diffSnapshotAgainst } from '$lib/snapshot-models';
	import type { BuilderModel } from '$lib/strategies';

	let { fingerprint, current }: { fingerprint: string; current: BuilderModel | null } = $props();
</script>

<div class="snapshot-diff" data-testid="snapshot-diff">
	{#if current === null}
		<p class="muted">
			The current definition is not valid, so it cannot be compared yet. Fix it in Build first.
		</p>
	{:else}
		{#await diffSnapshotAgainst(fingerprint, current)}
			<p class="muted">Comparing with the current rules…</p>
		{:then view}
			{#if view.status === 'unavailable'}
				<p class="problem" role="alert">Could not load the earlier rules: {view.reason}</p>
			{:else if view.diff.changes.length === 0}
				<p class="muted">No rule changes: only identity or metadata differ.</p>
			{:else}
				<p class="muted">{view.diff.summary}</p>
				<table aria-label="What changed since these rules">
					<thead>
						<tr>
							<th scope="col">Field</th>
							<th scope="col">Earlier edit</th>
							<th scope="col">Current rules</th>
						</tr>
					</thead>
					<tbody>
						{#each view.diff.changes as change (change.path + change.kind)}
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
</div>

<style>
	.snapshot-diff {
		display: grid;
		gap: 8px;
		padding: 10px 0;
	}
	.snapshot-diff p {
		margin: 0;
	}
	.muted {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.problem {
		color: var(--neg);
		font-size: var(--fs-sm);
	}
	th,
	td {
		padding: 6px 10px 6px 0;
		text-align: left;
		vertical-align: top;
		font-size: var(--fs-sm);
	}
	code {
		word-break: break-word;
	}
</style>
