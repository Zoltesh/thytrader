<script lang="ts">
	/**
	 * Advisory readiness preflight (ADR 0114) for Home and Portfolio.
	 *
	 * Reads `GET /api/v1/operator/readiness` only. It never tightens policy.
	 * Unknown amounts stay as an em dash.
	 */
	import { onMount } from 'svelte';
	import { fetchReadiness, readinessHeadline, type ReadinessReport } from '$lib/preflight';

	let report = $state<ReadinessReport | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);

	const headline = $derived(report === null ? null : readinessHeadline(report));
	const account = $derived(report?.payload.account ?? null);
	const optimistic = $derived(report?.payload.fee_evidence.optimistic_books.length ?? 0);

	async function load(): Promise<void> {
		loading = true;
		error = null;
		try {
			report = await fetchReadiness();
		} catch (caught) {
			error = caught instanceof Error ? caught.message : 'Readiness preflight is unavailable.';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
	});

	function amount(value: string | null | undefined, quote: string): string {
		return value ? `${value} ${quote}` : '—';
	}
</script>

<section class="card preflight" aria-labelledby="preflight-title" data-testid="preflight-panel">
	<div class="head">
		<h2 id="preflight-title">Readiness</h2>
		{#if headline}
			<span class="chip tone-{headline.tone}">{headline.tone === 'ok' ? 'Clear' : 'Check'}</span>
		{/if}
		<button type="button" class="btn ghost retry" onclick={() => void load()}>Refresh</button>
	</div>
	{#if loading && report === null}
		<p class="muted">Loading the advisory preflight…</p>
	{:else if error !== null && report === null}
		<p class="warn" role="status">{error}</p>
	{:else if report && account}
		<p class="summary" data-testid="preflight-summary">{headline?.summary}</p>
		<dl>
			<div>
				<dt>Venue available</dt>
				<dd>{amount(account.venue_available_quote, account.quote_currency)}</dd>
			</div>
			<div>
				<dt>Account cap</dt>
				<dd>{amount(account.effective_exposure_cap, account.quote_currency)}</dd>
			</div>
			<div>
				<dt>Current exposure</dt>
				<dd>{amount(account.current_exposure, account.quote_currency)}</dd>
			</div>
			<div>
				<dt>Entry capacity</dt>
				<dd>{amount(account.remaining_entry_capacity, account.quote_currency)}</dd>
			</div>
		</dl>
		<p class="note">
			Advisory only. Allocations above the cap are not the same as exposure already over the cap.
			{#if optimistic > 0}
				{optimistic} paper book(s) assume cheaper fees than the account reports.
			{/if}
		</p>
	{:else if report}
		<p class="summary">{headline?.summary ?? report.payload.note}</p>
	{/if}
</section>

<style>
	.preflight {
		padding: 12px 16px;
	}
	.head {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	h2 {
		margin: 0;
		font-size: var(--fs-base);
	}
	.retry {
		margin-left: auto;
	}
	.summary,
	.note,
	.muted,
	.warn {
		margin: 8px 0 0;
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.warn {
		color: var(--warn);
	}
	dl {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 8px 12px;
		margin: 12px 0 0;
	}
	dt {
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	dd {
		margin: 2px 0 0;
		font-family: var(--font-mono);
	}
	.chip {
		padding: 2px 8px;
		border-radius: var(--radius-sm);
		font-size: var(--fs-sm);
	}
	.tone-ok {
		color: var(--pos);
	}
	.tone-warn,
	.tone-error {
		color: var(--warn);
	}
	@media (max-width: 800px) {
		dl {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
