<script lang="ts">
	import { resolve } from '$app/paths';
	import { page as pageState } from '$app/state';
	import ExecutionQualityReportView from '$lib/ExecutionQualityReport.svelte';
	import {
		fetchExecutionQuality,
		fetchExecutionTwinComparison,
		type ExecutionQualityReport,
		type ExecutionTwinComparison
	} from '$lib/executionQuality';

	const id = $derived(pageState.params.id ?? '');
	let report = $state<ExecutionQualityReport | null>(null);
	let comparison = $state<ExecutionTwinComparison | null>(null);
	let error = $state<string | null>(null);
	let comparisonError = $state<string | null>(null);
	let loading = $state(true);

	$effect(() => {
		const deploymentId = id;
		if (deploymentId === '') return;
		const controller = new AbortController();
		loading = true;
		error = null;
		report = null;
		comparison = null;
		comparisonError = null;
		void fetchExecutionQuality(deploymentId, controller.signal)
			.then((loaded) => {
				report = loaded;
			})
			.catch((caught: unknown) => {
				error =
					caught instanceof Error ? caught.message : 'Execution-quality evidence is unavailable.';
			})
			.finally(() => {
				loading = false;
			});
		void fetchExecutionTwinComparison(deploymentId, controller.signal)
			.then((loaded) => {
				comparison = loaded;
			})
			.catch((caught: unknown) => {
				comparison = null;
				comparisonError =
					caught instanceof Error ? caught.message : 'No twin comparison is available.';
			});
		return () => controller.abort();
	});
</script>

<section class="page" aria-label="Execution quality">
	<p><a href={resolve(`/deployments/${id}`)}>← Bot</a></p>
	<h1>Execution quality</h1>
	{#if loading}<p>Loading recorded fills…</p>
	{:else if error}<p data-testid="execution-quality-error">{error}</p>
	{:else if report}
		<ExecutionQualityReportView {report} {comparison} {comparisonError} />
	{/if}
</section>

<style>
	.page {
		display: grid;
		gap: 16px;
		padding: 24px;
	}
	h1 {
		margin: 0;
		font-size: 1.4rem;
	}
</style>
