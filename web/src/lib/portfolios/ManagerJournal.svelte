<script lang="ts">
	/**
	 * Portfolio journal (ADR 0091): append-only, newest first, with "Load more"
	 * for older pages. The Manager tab owns loading; this renders the entries.
	 */
	import {
		journalActorText,
		journalKindLabel,
		utcMinute,
		type JournalEntry
	} from '$lib/portfolios';

	let {
		entries,
		nextCursor,
		loading,
		error,
		onloadmore
	}: {
		entries: JournalEntry[];
		nextCursor: string | null;
		loading: boolean;
		error: string | null;
		onloadmore: () => void;
	} = $props();
</script>

<section class="card" aria-label="Portfolio journal">
	<div class="journal-head">
		<h2>Journal</h2>
		<span class="muted small">Append-only · newest first</span>
	</div>
	{#if error}
		<p class="problem small body-pad" role="alert">{error}</p>
	{:else if entries.length === 0 && !loading}
		<p class="muted body-pad">Nothing recorded yet.</p>
	{/if}
	<ol class="journal" data-testid="portfolio-journal">
		{#each entries as entry (entry.entry_id)}
			<li class="entry" data-kind={entry.kind}>
				<div class="when mono">{utcMinute(entry.occurred_at)}</div>
				<div class="what">
					<div class="kind">{journalKindLabel(entry.kind)}</div>
					<div>{entry.summary}</div>
					<div class="faint small">{journalActorText(entry)} · revision {entry.revision}</div>
				</div>
			</li>
		{/each}
	</ol>
	{#if nextCursor !== null}
		<div class="body-pad">
			<button type="button" class="btn ghost" disabled={loading} onclick={onloadmore}
				>{loading ? 'Loading…' : 'Load more'}</button
			>
		</div>
	{/if}
</section>

<style>
	.body-pad {
		padding: 12px 18px;
	}
	.journal-head {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 14px 18px;
		border-bottom: 1px solid var(--line);
	}
	.journal-head .muted {
		margin-left: auto;
	}
	.journal {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.entry {
		display: flex;
		gap: 14px;
		padding: 12px 18px;
		border-bottom: 1px solid var(--line);
	}
	.when {
		flex: 0 0 132px;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.what {
		flex: 1;
		min-width: 0;
	}
	.kind {
		font-weight: 600;
	}
	.mono {
		font-family: var(--font-mono);
	}
	.muted {
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.problem {
		color: var(--neg);
	}
	@media (max-width: 900px) {
		.entry {
			flex-direction: column;
			gap: 4px;
		}
	}
</style>
