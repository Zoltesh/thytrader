<script lang="ts">
	/**
	 * Bot detail header: title, mode chip, rules pill linking the strategy
	 * workspace, the status lede and facts, and the lifecycle buttons. Buttons
	 * render only when `controlsAvailable` (complete contract, fresh snapshot);
	 * each one asks the page to open its confirmation dialog.
	 */
	import { resolve } from '$app/paths';
	import {
		botTitle,
		canOfferFlatten,
		eligibility,
		leaseText,
		marketLabel,
		type LifecycleAction
	} from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';
	import { rulesLabel, shortStrategyFingerprint, type RulesState } from '$lib/strategy-workspace';

	let {
		current,
		strategyName,
		versionLink,
		botRules,
		controlsAvailable,
		mutating,
		onaction
	}: {
		current: Deployment;
		/** Name from the exact rules snapshot, when it loaded. */
		strategyName: string | null;
		versionLink: { href: `/strategies/${string}`; fingerprint: string } | null;
		botRules: RulesState;
		controlsAvailable: boolean;
		mutating: boolean;
		onaction: (action: LifecycleAction) => void;
	} = $props();

	const identity = $derived(eligibility(current));
	const isLive = $derived(current.mode === 'live');
</script>

<header class="bot-head">
	<div class="bot-id">
		<div class="title-row">
			<h1>{botTitle(current, strategyName)}</h1>
			<span class="chip" class:paper={!isLive} class:live={isLive} data-testid="mode-chip"
				>{isLive ? 'LIVE' : 'Paper'}</span
			>
			{#if versionLink}
				<a
					class="pill"
					href={resolve(versionLink.href)}
					data-testid="version-pill"
					data-rules={botRules}
					title="Rules snapshot {versionLink.fingerprint}. Open the strategy workspace."
					>{botRules === 'unknown'
						? shortStrategyFingerprint(versionLink.fingerprint)
						: rulesLabel(botRules)} →</a
				>
			{/if}
		</div>
		<p class="lede" data-testid="bot-lede">
			{marketLabel(current.product_id)} · {current.timeframe ?? 'clock unknown'} ·
			<span data-testid="deployment-status">{identity.status}</span> ·
			<span title="A held lease is coordination state, not proof that the worker is healthy"
				>{leaseText(current)}</span
			>
		</p>
		<p class="facts">
			<span>Instruction <b>{identity.instruction}</b></span>
			<span>Entries <b data-testid="deployment-eligibility">{identity.eligibility}</b></span>
			<span>Revision <b>{current.revision}</b></span>
		</p>
	</div>
	<div class="controls">
		{#if controlsAvailable}
			{#if current.status === 'running'}
				<button type="button" class="btn" disabled={mutating} onclick={() => onaction('pause')}
					>Pause entries…</button
				>
			{/if}
			{#if current.status === 'paused'}
				<button
					type="button"
					class="btn"
					class:live={isLive}
					disabled={mutating}
					onclick={() => onaction('resume')}>Resume entries…</button
				>
			{/if}
			{#if current.status !== 'stopped'}
				<button
					type="button"
					class="btn danger"
					disabled={mutating}
					onclick={() => onaction('stop')}>Stop…</button
				>
			{/if}
			{#if canOfferFlatten(current)}
				<button
					type="button"
					class="btn danger"
					disabled={mutating}
					onclick={() => onaction('flatten')}>Flatten remaining exposure…</button
				>
			{/if}
		{/if}
	</div>
</header>

<style>
	.bot-head {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: 12px 16px;
		margin-bottom: 14px;
	}
	.bot-id {
		min-width: 0;
		flex: 1;
	}
	.title-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
	}
	.title-row h1 {
		margin: 0;
	}
	.pill {
		display: inline-flex;
		align-items: center;
		height: 24px;
		padding: 0 9px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		color: var(--text);
		font-size: var(--fs-sm);
		text-decoration: none;
	}
	.pill:hover {
		border-color: var(--line-2);
	}
	.lede {
		margin-top: 4px;
	}
	.facts {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 18px;
		margin: 6px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.facts b {
		color: var(--text);
		font-weight: 500;
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.btn.danger {
		border-color: var(--danger-line);
		color: var(--neg);
	}
</style>
