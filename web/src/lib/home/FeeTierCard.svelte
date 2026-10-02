<script lang="ts">
	/**
	 * Home fee tier (ADR 0084): one compact line from `GET /api/v1/fees` — tier,
	 * maker and taker rates, 30-day volume, and when Coinbase reported it. Rates
	 * use the exact decimal percent formatter. Demo installs say so.
	 */
	import { formatFeeProfileAsOf, type FeeProfile } from '$lib/fees';
	import { formatPercent, formatUsd } from '$lib/portfolio';
	import type { Load } from './load';

	let {
		fees,
		demo,
		onretry
	}: {
		fees: Load<FeeProfile>;
		/** True when the portfolio is demo data (no Coinbase credentials). */
		demo: boolean;
		onretry: () => void;
	} = $props();

	const profile = $derived(fees.data);
	const asOf = $derived(profile === null ? null : formatFeeProfileAsOf(profile.as_of));
</script>

<section class="card fee-line" aria-labelledby="fees-title" data-testid="fee-tier">
	<h2 id="fees-title">Fee tier</h2>
	{#if profile !== null}
		<span class="tier">{profile.fee_tier}</span>
		<span class="fact">Maker <strong>{formatPercent(profile.maker_fee_rate)}</strong></span>
		<span class="fact">Taker <strong>{formatPercent(profile.taker_fee_rate)}</strong></span>
		<span class="fact">30-day volume <strong>{formatUsd(profile.usd_volume_30d)}</strong></span>
		{#if asOf !== null}
			<span class="fact as-of">As of <time datetime={profile.as_of}>{asOf}</time></span>
		{/if}
		{#if demo}<span class="chip">Demo</span>{/if}
		{#if fees.status === 'error'}
			<span class="warn" role="status">Couldn't refresh: {fees.error}</span>
		{/if}
	{:else if fees.status === 'error'}
		<span class="unavailable">Fee profile is temporarily unavailable.</span>
		<button type="button" class="btn ghost retry" onclick={onretry}>Retry</button>
	{:else}
		<span class="skeleton line-skeleton" aria-hidden="true"></span>
		<span class="sr-only">Loading the fee tier…</span>
	{/if}
</section>

<style>
	.fee-line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 18px;
		padding: 12px 16px;
	}
	h2 {
		font-size: var(--fs-base);
	}
	.tier {
		padding: 2px 8px;
		border: 1px solid var(--accent-line);
		border-radius: var(--radius-sm);
		background: var(--accent-soft);
		color: var(--accent);
		font-size: var(--fs-sm);
		font-weight: 600;
	}
	.fact {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.fact strong {
		color: var(--text);
		font-family: var(--font-mono);
		font-weight: 500;
	}
	.as-of {
		color: var(--faint);
	}
	.unavailable {
		color: var(--muted);
	}
	.warn {
		color: var(--warn);
		font-size: var(--fs-sm);
	}
	.retry {
		min-height: 28px;
		padding: 0 8px;
	}
	.line-skeleton {
		display: inline-block;
		width: min(420px, 60%);
		height: 14px;
		margin: 0;
	}
</style>
