<script lang="ts">
	/**
	 * Build stage right column: plain-English summary, checks (validation,
	 * warmup / required data, collapsible engine support), save state, and the
	 * Save action. Disabled actions say why in visible text
	 * wired through `aria-describedby`.
	 */
	import type { Snippet } from 'svelte';
	import EngineSupportMatrix from '$lib/EngineSupportMatrix.svelte';
	import type { BuilderModel } from '$lib/strategies';
	import { plainEnglishSummary, requiredDataText } from '$lib/strategy-insight';

	let {
		model,
		validationErrors,
		actions
	}: {
		model: BuilderModel;
		validationErrors: string[];
		/** Save controls and the saved validation state. */
		actions: Snippet;
	} = $props();
</script>

<aside class="inspector" aria-label="Strategy inspector">
	<section class="card block inspector-block" data-testid="plain-english">
		<h2>In plain English</h2>
		<p>{plainEnglishSummary(model)}</p>
	</section>
	<section class="card block" aria-labelledby="checks-title">
		<h2 id="checks-title">Checks</h2>
		<div class="check">
			{#if validationErrors.length === 0}
				<span class="ok" aria-hidden="true">✓</span><span>No definition problems detected.</span>
			{:else}
				<span class="bad" aria-hidden="true">!</span>
				<div>
					<span>{validationErrors.length} problem{validationErrors.length === 1 ? '' : 's'}</span>
					<ul class="problems">
						{#each validationErrors as problem (problem)}
							<li>{problem}</li>
						{/each}
					</ul>
				</div>
			{/if}
		</div>
		<div class="check">
			<span class="faint" aria-hidden="true">i</span><span
				>Warmup and data: {requiredDataText(model)}</span
			>
		</div>
		<details class="engine" open>
			<summary>Engine support</summary>
			<EngineSupportMatrix />
		</details>
		<div class="actions">
			{@render actions()}
		</div>
	</section>
</aside>

<style>
	.inspector {
		position: sticky;
		top: calc(var(--topbar-height) + 150px);
		display: grid;
		gap: var(--space-4);
		align-content: start;
		min-width: 0;
	}
	.block {
		display: grid;
		gap: 8px;
		min-width: 0;
		padding: 16px;
	}
	.block p {
		margin: 0;
		color: var(--muted);
	}
	.check {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
	}
	.ok {
		color: var(--pos);
	}
	.bad {
		color: var(--neg);
		font-weight: 700;
	}
	.faint {
		color: var(--faint);
	}
	.problems {
		display: grid;
		gap: 4px;
		margin: 6px 0 0;
		padding-left: 16px;
		color: var(--neg);
		font-size: var(--fs-sm);
	}
	.engine {
		min-width: 0;
		overflow-x: auto;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
	}
	.engine summary {
		cursor: pointer;
		color: var(--muted);
	}
	.actions {
		display: grid;
		gap: 8px;
		margin-top: 6px;
	}
	@media (max-width: 1100px) {
		.inspector {
			position: static;
		}
	}
</style>
