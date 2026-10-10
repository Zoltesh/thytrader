<script lang="ts">
	/**
	 * Home fleet entry banner (ADR 0130): a prominent notice whenever the entry gate would
	 * refuse every new entry in a mode and quote scope, naming the exact books and records
	 * responsible, from `GET /api/v1/operator/fleet-health`. Hidden while every occupied
	 * scope admits entries. A failed read says the fleet was not checked; it never hides.
	 * Read-only: repairs and breaker resets happen elsewhere.
	 */
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { fetchFleetHealth, fleetBanner } from './fleet-health';
	import { Resource } from './resource.svelte';

	const report = new Resource(fetchFleetHealth);
	const banner = $derived(report.state.data === null ? null : fleetBanner(report.state.data));

	onMount(() => {
		void report.reload();
	});
</script>

{#if banner !== null}
	<section
		class="fleet-banner tone-{banner.tone}"
		role="alert"
		aria-labelledby="fleet-banner-title"
		data-testid="fleet-entry-banner"
	>
		<h2 id="fleet-banner-title">{banner.title}</h2>
		{#each banner.scopes as scope (scope.key)}
			<div class="scope" data-testid="fleet-entry-scope">
				<p class="scope-title"><strong>{scope.title}</strong> · {scope.reasons}</p>
				{#if scope.books.length > 0}
					<ul>
						{#each scope.books as book (book.deploymentId + book.detail)}
							<li>
								<a href={resolve(`/deployments/${encodeURIComponent(book.deploymentId)}`)}
									>{book.label}</a
								>: <span class="detail">{book.detail}</span>
							</li>
						{/each}
					</ul>
				{/if}
				{#if scope.clears !== ''}<p class="clears">{scope.clears}</p>{/if}
			</div>
		{/each}
		{#if banner.systemic.length > 0}
			<ul class="systemic" data-testid="fleet-systemic">
				{#each banner.systemic as line (line)}<li>{line}</li>{/each}
			</ul>
		{/if}
		<p class="hint">
			Exits and protection continue. Full report: <code>thytrader-operator fleet-health</code>.
		</p>
	</section>
{:else if report.state.status === 'error' && report.state.data === null}
	<p class="fleet-unchecked" role="status" data-testid="fleet-entry-unchecked">
		Fleet entry health could not be checked ({report.state.error}).
		<button type="button" class="btn ghost" onclick={() => void report.reload()}>Retry</button>
	</p>
{/if}

<style>
	.fleet-banner {
		display: grid;
		gap: 8px;
		margin-bottom: 18px;
		padding: 14px 17px;
		border-radius: var(--radius-md);
	}
	.tone-danger {
		border: 1px solid var(--danger-line);
		background: var(--danger-soft);
	}
	.tone-warn {
		border: 1px solid var(--warn-line);
		background: var(--warn-soft);
	}
	h2 {
		margin: 0;
		font-size: var(--fs-base);
	}
	.tone-danger h2 {
		color: var(--neg);
	}
	.tone-warn h2 {
		color: var(--warn);
	}
	.scope {
		display: grid;
		gap: 4px;
	}
	p,
	ul {
		margin: 0;
		font-size: var(--fs-sm);
	}
	ul {
		padding-left: 18px;
	}
	.detail {
		font-family: var(--font-mono);
		overflow-wrap: anywhere;
	}
	.clears,
	.hint {
		color: var(--muted);
	}
	.fleet-unchecked {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin: 0 0 18px;
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.fleet-unchecked .btn {
		min-height: 26px;
		padding: 0 8px;
	}
</style>
