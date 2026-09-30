<script lang="ts">
	/**
	 * "Current rules" / "Earlier edit" for one run or bot row, by comparing its
	 * snapshot fingerprint with the strategy's current fingerprint. An earlier
	 * edit offers a "What changed" disclosure with the semantic diff.
	 */
	import type { BuilderModel } from '$lib/strategies';
	import { rulesLabel, rulesState, shortStrategyFingerprint } from '$lib/strategy-workspace';
	import SnapshotDiff from './SnapshotDiff.svelte';

	let {
		fingerprint,
		currentFingerprint,
		current
	}: {
		fingerprint: string | null;
		currentFingerprint: string | null;
		current: BuilderModel | null;
	} = $props();

	const uid = $props.id();
	let open = $state(false);
	const rules = $derived(rulesState(fingerprint, currentFingerprint));
</script>

<span class="rules" data-testid="rules-badge" data-rules={rules}>
	<span class="badge {rules}" title={fingerprint ?? undefined}>{rulesLabel(rules)}</span>
	{#if rules === 'earlier' && fingerprint}
		<button
			class="link"
			type="button"
			aria-expanded={open}
			aria-controls="rules-diff-{uid}"
			onclick={() => (open = !open)}>What changed</button
		>
	{/if}
</span>
{#if open && fingerprint}
	<div id="rules-diff-{uid}" class="diff-panel">
		<p class="faint mono">Earlier edit {shortStrategyFingerprint(fingerprint)} → current rules</p>
		<SnapshotDiff {fingerprint} {current} />
	</div>
{/if}

<style>
	.rules {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}
	.badge {
		display: inline-flex;
		align-items: center;
		height: 22px;
		padding: 0 8px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--surface-2);
		font-size: var(--fs-xs);
	}
	.badge.current {
		border-color: var(--accent-line);
		color: var(--accent);
	}
	.badge.earlier {
		border-color: var(--warn-line);
		color: var(--warn);
	}
	.badge.unknown {
		color: var(--faint);
	}
	.link {
		padding: 0;
		border: 0;
		background: none;
		color: var(--accent);
		font-size: var(--fs-xs);
		text-decoration: underline;
		cursor: pointer;
	}
	.diff-panel {
		margin-top: 6px;
		padding: 8px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-md);
		background: var(--surface-2);
		white-space: normal;
	}
	.diff-panel p {
		margin: 0;
	}
	.faint {
		color: var(--faint);
		font-size: var(--fs-xs);
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
