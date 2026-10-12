<script lang="ts">
	import { onMount } from 'svelte';
	import { Resource } from './resource.svelte';

	type Position = {
		product_id: string;
		side: string;
		number_of_contracts: string;
		classification: 'managed_matched' | 'managed_mismatch' | 'external_unmanaged' | 'unknown';
	};
	type Section = { positions_source: string; positions: Position[] | null };
	const positions = new Resource(async (): Promise<Section | null> => {
		const response = await fetch('/api/v1/operator/venue-reconciliation');
		if (!response.ok) throw new Error('Futures ownership report unavailable');
		const report: { payload: { futures: Section | null } } = await response.json();
		return report.payload.futures;
	});
	const labels = {
		managed_matched: 'Managed matched',
		managed_mismatch: 'Managed mismatch',
		external_unmanaged: 'External unmanaged',
		unknown: 'Ownership/comparison unknown'
	};
	onMount(() => {
		void positions.reload();
	});
</script>

<div data-testid="futures-ownership">
	<h3>Venue positions: managed vs external</h3>
	{#if positions.state.status === 'error'}
		<p role="status">Couldn't refresh: {positions.state.error}. Ownership is unknown.</p>
	{:else if positions.state.data?.positions == null || positions.state.data.positions_source !== 'mirror_snapshot'}
		<p>Position ownership is unknown, not zero.</p>
	{:else}
		<p>
			Position evidence: {positions.state.data.positions_source}. Read-only; no adoption or
			flattening.
		</p>
		{#each positions.state.data.positions as row (row.product_id)}
			<p>
				{row.product_id} · {labels[row.classification] ?? labels.unknown} · {row.side}
				{row.number_of_contracts} contracts
			</p>
		{:else}
			<p>No positions in the complete venue observation.</p>
		{/each}
	{/if}
</div>
