<script lang="ts">
	/**
	 * Lightweight landing for the old standalone `/research`, `/deploy`, and
	 * `/backtests` routes: those surfaces now live inside each strategy's
	 * workspace, so without a strategy in the link the page points there.
	 */
	import { resolve } from '$app/paths';

	let { title, stage }: { title: string; stage: 'Test' | 'Run' } = $props();
</script>

<section class="card chooser" data-testid="stage-chooser">
	<h1>{title}</h1>
	<p>
		{title} now lives inside each strategy's workspace, on its <strong>{stage}</strong> stage. Open
		a strategy to {stage === 'Test'
			? 'run backtests and studies against the current rules'
			: 'start, pause, or stop paper and live deployments of its current rules'}.
	</p>
	<a class="btn primary" href={resolve('/strategies')}>Open a strategy</a>
</section>

<style>
	.chooser {
		display: grid;
		justify-items: start;
		gap: 10px;
		margin-bottom: var(--space-5);
		padding: 22px 24px;
	}
	.chooser p {
		max-width: 70ch;
		margin: 0;
		color: var(--muted);
	}
</style>
