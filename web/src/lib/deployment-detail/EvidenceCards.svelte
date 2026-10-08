<script lang="ts">
	/**
	 * Evidence links for the exact rules this bot runs (bounded, never claimed
	 * complete) beside the other deployments of the same strategy, whose
	 * different rules snapshots are listed but never mixed into this page.
	 */
	import { resolve } from '$app/paths';
	import { EVIDENCE_BOUNDED_NOTE, marketLabel, type EvidenceLink } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';

	let {
		deploymentId,
		evidenceLinks,
		otherVersions
	}: {
		deploymentId: string;
		evidenceLinks: EvidenceLink[];
		otherVersions: Deployment[];
	} = $props();
</script>

<div class="grid2 even">
	<section class="card" aria-labelledby="evidence-title">
		<div class="card-head"><h2 id="evidence-title">Evidence for this strategy</h2></div>
		<div class="pad">
			<p class="evidence-links">
				{#if evidenceLinks.length === 0}
					<span class="quiet">No strategy evidence for a discretionary deployment.</span>
				{:else}
					{#each evidenceLinks as link (link.href)}
						<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- dynamic cross-route link with query -->
						<a data-testid="evidence-link" href={link.href}>{link.label}</a>
					{/each}
				{/if}
				<a href={resolve('/journals')}>Trade journals</a>
				<a href={resolve('/audit')}>Audit log</a>
				<a
					data-testid="execution-quality-link"
					href={resolve(`/deployments/${deploymentId}/execution-quality`)}
				>
					Execution quality
				</a>
			</p>
			<p class="quiet small">{EVIDENCE_BOUNDED_NOTE}</p>
		</div>
	</section>
	<section class="card" aria-label="Other deployments for this strategy">
		<div class="card-head"><h2>Other deployments for this strategy</h2></div>
		<div class="pad">
			{#if otherVersions.length === 0}
				<p class="quiet">None on this workstation.</p>
			{:else}
				<ul class="other-list" role="list">
					{#each otherVersions as other (other.id)}
						<li>
							<a href={resolve(`/deployments/${encodeURIComponent(other.id)}`)}>
								{other.mode} · {other.status} · {marketLabel(other.product_id)} · fingerprint
								{other.strategy_fingerprint?.slice(0, 18) ?? 'unknown'}…
							</a>
						</li>
					{/each}
				</ul>
				<p class="quiet small">
					Different rules snapshots; their evidence is not mixed into this page.
				</p>
			{/if}
		</div>
	</section>
</div>

<style>
	.grid2 {
		display: grid;
		grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
		gap: 16px;
		margin-bottom: 16px;
	}
	.grid2.even {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}
	.card {
		margin-bottom: 16px;
	}
	.grid2 > .card {
		margin-bottom: 0;
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-head h2 {
		margin-right: auto;
	}
	.pad {
		margin: 0;
		padding: 14px 16px;
	}
	.quiet {
		color: var(--muted);
	}
	.small {
		font-size: var(--fs-sm);
	}
	.evidence-links {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 16px;
		margin: 0 0 8px;
	}
	.other-list {
		display: grid;
		gap: 8px;
		margin: 0 0 8px;
		padding: 0;
		list-style: none;
	}
	@media (max-width: 1100px) {
		.grid2,
		.grid2.even {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
