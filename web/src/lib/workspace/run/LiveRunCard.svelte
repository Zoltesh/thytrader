<script lang="ts">
	/**
	 * Run stage Live card (ADR 0082): this strategy's live deployments with their
	 * lifecycle controls, the live preflight, and "Arm live trading…".
	 *
	 * The preflight lists what existing endpoints report, with `Unknown` where a
	 * source cannot be read; it is never a readiness verdict. A futures strategy
	 * cannot be armed: there is no live futures path (FUTURES_LIVE_UNSUPPORTED).
	 */
	import type { LifecycleAction } from '$lib/deployment-detail';
	import type { Deployment } from '$lib/deployments';
	import type { BuilderModel } from '$lib/strategies';
	import { paperEvidenceText, type PreflightItem } from '$lib/strategy-workspace';
	import DeploymentRuntimeRow from '$lib/workspace/DeploymentRuntimeRow.svelte';

	let {
		live,
		paper,
		preflight,
		controlsBlocked,
		canStart,
		currentFingerprint,
		model,
		futures = false,
		onaction,
		onupdated,
		onarm
	}: {
		live: Deployment[];
		paper: Deployment[];
		preflight: PreflightItem[] | null;
		controlsBlocked: boolean;
		canStart: boolean;
		currentFingerprint: string | null;
		model: BuilderModel | null;
		/** The strategy trades a futures contract, which runs as a paper book only. */
		futures?: boolean;
		onaction: (action: LifecycleAction, deployment: Deployment) => void;
		onupdated: (result: { stopped: Deployment; started: Deployment | null }) => void;
		onarm: () => void;
	} = $props();

	const activeLive = $derived(live.filter((deployment) => deployment.status !== 'stopped'));
</script>

<section class="card run-card live-card" aria-labelledby="live-title" data-testid="live-card">
	<div class="card-head">
		<span class="chip live">LIVE</span>
		<h2 id="live-title">
			{activeLive.length > 0 ? `${activeLive.length} running or paused` : 'Not running'}
		</h2>
	</div>
	<div class="card-body">
		{#each live as deployment (deployment.id)}
			<DeploymentRuntimeRow
				{deployment}
				disabled={controlsBlocked}
				{currentFingerprint}
				current={model}
				{onaction}
				{onupdated}
			/>
		{/each}
		{#if futures}
			<p class="refused" role="status" data-testid="live-futures-unsupported">
				Live futures are not supported (FUTURES_LIVE_UNSUPPORTED) — run this strategy as a paper
				book.
			</p>
		{:else}
			<h3 class="sub">Live preflight</h3>
			<p class="note">
				What existing endpoints report right now. Items are independent facts, not a readiness
				verdict; arming stays your decision.
			</p>
			<ul class="checks" aria-label="Live preflight" aria-busy={preflight === null}>
				{#if preflight === null}
					<li class="check"><span class="faint">…</span>Reading preflight sources…</li>
				{:else}
					{#each preflight as item (item.id)}
						<li class="check" data-preflight={item.id} data-state={item.state}>
							<span class="mark {item.state}" aria-hidden="true"
								>{item.state === 'ok' ? '✓' : item.state === 'attention' ? '!' : '?'}</span
							>
							<span
								><span class="sr-only"
									>{item.state === 'ok'
										? 'Reported:'
										: item.state === 'attention'
											? 'Needs attention:'
											: 'Unknown:'}</span
								>
								{item.label}</span
							>
						</li>
					{/each}
				{/if}
				<li class="check" data-preflight="paper-evidence">
					<span class="mark info" aria-hidden="true">i</span>
					<span class="muted">{paperEvidenceText(paper)}</span>
				</li>
			</ul>
			<button
				class="btn live arm"
				type="button"
				disabled={controlsBlocked || !canStart}
				aria-describedby={canStart ? undefined : 'arm-blocked'}
				onclick={onarm}>Arm live trading…</button
			>
			{#if !canStart}<p class="note" id="arm-blocked">
					Blocked until the saved definition is valid.
				</p>{/if}
		{/if}
	</div>
</section>

<style>
	.live-card {
		border-color: var(--live);
	}
	.card-head {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.card-body {
		display: grid;
		gap: 10px;
		padding: 16px;
	}
	.sub {
		margin: 8px 0 0;
		color: var(--faint);
		font-size: var(--fs-sm);
		font-weight: 500;
		letter-spacing: 0.05em;
		text-transform: uppercase;
	}
	.note {
		margin: 0;
		color: var(--faint);
		font-size: var(--fs-sm);
	}
	.muted {
		margin: 0;
		color: var(--muted);
	}
	.faint {
		color: var(--faint);
	}
	.checks {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.check {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 9px 0;
		border-bottom: 1px solid var(--line);
	}
	.check:last-child {
		border-bottom: 0;
	}
	.mark {
		flex: none;
		width: 16px;
		font-weight: 700;
		text-align: center;
	}
	.mark.ok {
		color: var(--pos);
	}
	.mark.attention {
		color: var(--warn);
	}
	.mark.unknown,
	.mark.info {
		color: var(--faint);
	}
	.refused {
		margin: 0;
		color: var(--warn);
	}
	.arm {
		width: 100%;
		margin-top: 6px;
	}
</style>
