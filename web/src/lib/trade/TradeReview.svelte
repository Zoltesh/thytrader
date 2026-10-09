<script lang="ts">
	/**
	 * Trade ticket Review aside: mode, market, side, entry, size, max loss at the
	 * stop, reward : risk, the risk policy that will check the order, the ticket
	 * warnings, and the submit button (paper places the order; live opens the
	 * live confirmation).
	 */
	import type { RiskPolicySnapshot } from '$lib/strategy-workspace';
	import type { TradeReview } from '$lib/trade-review';

	let {
		isLive,
		market,
		side,
		review,
		sizeText,
		riskPolicy,
		submitting,
		onsubmit
	}: {
		isLive: boolean;
		market: string;
		side: 'long' | 'short';
		review: TradeReview;
		sizeText: string;
		riskPolicy: RiskPolicySnapshot | 'unknown' | null;
		submitting: boolean;
		onsubmit: () => void;
	} = $props();

	const riskPolicyText = $derived.by((): { text: string; attention: boolean } => {
		if (riskPolicy === null) return { text: 'Reading…', attention: false };
		if (riskPolicy === 'unknown') return { text: 'Unknown', attention: false };
		if (riskPolicy.source === 'published') {
			return {
				text: `Published v${riskPolicy.version} · checked again on submit`,
				attention: false
			};
		}
		return isLive
			? {
					text: 'Compiled default · live orders are refused until a policy is published',
					attention: true
				}
			: { text: 'Compiled default · checked on submit', attention: false };
	});
</script>

<aside
	class="card review"
	class:live={isLive}
	aria-labelledby="review-title"
	data-testid="trade-review"
>
	<h2 id="review-title">Review</h2>
	<div class="check"><span>Mode</span><b>{isLive ? 'LIVE · real money' : 'Paper'}</b></div>
	<div class="check"><span>Market</span><b>{market}</b></div>
	<div class="check">
		<span>Side</span><b>{side === 'long' ? 'Buy / long' : 'Sell / short'}</b>
	</div>
	<div class="check"><span>Entry</span><b data-testid="review-entry">{review.entry}</b></div>
	<div class="check"><span>Size</span><b>{sizeText}</b></div>
	<div class="check">
		<span>Max loss at stop</span><b data-testid="review-max-loss">{review.maxLoss}</b>
	</div>
	<div class="check">
		<span>Reward : risk</span><b data-testid="review-reward-risk">{review.rewardRisk}</b>
	</div>
	<div class="check">
		<span>Risk policy</span><b
			class:attention={riskPolicyText.attention}
			data-testid="review-risk-policy">{riskPolicyText.text}</b
		>
	</div>
	{#each review.warnings as warning, index (index)}
		<p class="warning" role="status">{warning}</p>
	{/each}
	<p class="hint">
		Fees and slippage are not included. Sizing, caps, and data health are checked by the server.
	</p>
	{#if isLive}
		<button type="button" class="btn live submit" disabled={submitting} onclick={onsubmit}
			>Review live order…</button
		>
	{:else}
		<button type="button" class="btn primary submit" disabled={submitting} onclick={onsubmit}
			>{submitting
				? 'Submitting…'
				: side === 'short'
					? 'Place paper short'
					: 'Place paper long'}</button
		>
	{/if}
</aside>

<style>
	.hint {
		margin: 8px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.review {
		position: sticky;
		top: calc(var(--topbar-height) + 50px);
		padding: 16px;
	}
	.review.live {
		border: 2px solid var(--live);
	}
	.check {
		display: flex;
		gap: 10px;
		padding: 9px 0;
		border-bottom: 1px solid var(--line);
	}
	.check span {
		flex: 1;
		color: var(--muted);
	}
	.check b {
		font-weight: 500;
		text-align: right;
	}
	.check b.attention,
	.warning {
		color: var(--warn);
	}
	.warning {
		margin: 8px 0 0;
		font-size: var(--fs-sm);
	}
	.submit {
		width: 100%;
		margin-top: 14px;
	}
	@media (max-width: 1000px) {
		.review {
			position: static;
		}
	}
</style>
