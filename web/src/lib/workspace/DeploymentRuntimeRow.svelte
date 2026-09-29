<script lang="ts">
	/**
	 * One deployment of the selected version inside a Run-stage card: status,
	 * instruction and entry eligibility (kept separate), fill-ledger
	 * performance, exposure and protection, and the lifecycle triggers. The
	 * triggers only open the accessible lifecycle dialog; they never mutate.
	 * Missing lifecycle-contract fields hide the triggers (fail closed).
	 */
	import { resolve } from '$app/paths';
	import {
		canOfferFlatten,
		eligibility,
		ledgerPerformanceText,
		workingOrderCount,
		type LifecycleAction
	} from '$lib/deployment-detail';
	import { canonicalPositions, type Deployment } from '$lib/deployments';
	import { lifecycleContractNote, lifecycleControlsAvailable } from '$lib/lifecycle-contract';

	let {
		deployment,
		disabled,
		onaction
	}: {
		deployment: Deployment;
		/** True while a mutation is pending or its outcome is unknown. */
		disabled: boolean;
		onaction: (action: LifecycleAction, deployment: Deployment) => void;
	} = $props();

	const summary = $derived(eligibility(deployment));
	const positions = $derived(canonicalPositions(deployment));
	const controls = $derived(lifecycleControlsAvailable(deployment));
	const contractNote = $derived(lifecycleContractNote(deployment));
	const modeLabel = $derived(deployment.mode === 'live' ? 'Live' : 'Paper');
</script>

<article class="runtime" data-testid="runtime-row" data-deployment-id={deployment.id}>
	<div class="runtime-head">
		<h3>
			{summary.status === 'running'
				? 'Running'
				: summary.status === 'paused'
					? 'Paused'
					: summary.status === 'stopped'
						? 'Stopped'
						: summary.status} since {deployment.created_at.slice(0, 10)}
		</h3>
		<a
			class="btn ghost"
			href={resolve(`/deployments/${encodeURIComponent(deployment.id)}`)}
			aria-label="Open {modeLabel.toLowerCase()} bot {deployment.id}">Open bot →</a
		>
	</div>
	<dl class="facts">
		<div>
			<dt>Instruction</dt>
			<dd>{summary.instruction}</dd>
		</div>
		<div>
			<dt>Entries</dt>
			<dd>{summary.eligibility}</dd>
		</div>
		<div>
			<dt>{modeLabel} performance</dt>
			<dd>{ledgerPerformanceText(deployment)}</dd>
		</div>
		<div>
			<dt>Exposure</dt>
			<dd>
				{#if positions.length === 0}
					No open position · {workingOrderCount(deployment)} working order{workingOrderCount(
						deployment
					) === 1
						? ''
						: 's'}
				{:else}
					{#each positions as position (position.product_id)}
						<span class="position"
							>{position.side ?? 'long'}
							{position.quantity} @ {position.entry_price} · stop {position.stop_price} · target {position.target_price}
							· protection {position.protection_status ?? 'unknown'}</span
						>
					{/each}
				{/if}
			</dd>
		</div>
		{#if deployment.mode === 'paper' && deployment.maker_fee_rate && deployment.taker_fee_rate}
			<div>
				<dt>Fee assumptions</dt>
				<dd>
					maker {deployment.maker_fee_rate} · taker {deployment.taker_fee_rate} (not real Coinbase fees)
				</dd>
			</div>
		{/if}
	</dl>
	{#if deployment.mismatch_detail}
		<p class="problem" role="alert">{deployment.mismatch_detail}</p>
	{/if}
	{#if !controls}
		<p class="note" data-testid="lifecycle-contract-note">{contractNote}</p>
	{:else}
		<div class="controls">
			{#if deployment.status === 'running'}
				<button class="btn" type="button" {disabled} onclick={() => onaction('pause', deployment)}
					>Pause entries…</button
				>
			{/if}
			{#if deployment.status === 'paused'}
				<button class="btn" type="button" {disabled} onclick={() => onaction('resume', deployment)}
					>Resume entries…</button
				>
			{/if}
			{#if deployment.status === 'running' || deployment.status === 'paused'}
				<button class="btn" type="button" {disabled} onclick={() => onaction('stop', deployment)}
					>Stop…</button
				>
			{/if}
			{#if canOfferFlatten(deployment)}
				<button class="btn" type="button" {disabled} onclick={() => onaction('flatten', deployment)}
					>Flatten remaining exposure…</button
				>
			{/if}
		</div>
	{/if}
</article>

<style>
	.runtime {
		display: grid;
		gap: 10px;
		padding: 12px 0;
		border-bottom: 1px solid var(--line);
	}
	.runtime:last-child {
		border-bottom: 0;
	}
	.runtime-head {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.runtime-head h3 {
		margin: 0 auto 0 0;
		font-size: var(--fs-md);
		text-transform: none;
	}
	.facts {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 10px 16px;
		margin: 0;
	}
	.facts dt {
		color: var(--muted);
		font-size: var(--fs-sm);
	}
	.facts dd {
		margin: 2px 0 0;
		color: var(--text);
	}
	.position {
		display: block;
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.note {
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.problem {
		margin: 0;
		color: var(--neg);
	}
</style>
