<script lang="ts">
	/** Strategy library paging: rows per page, the page number, and Previous / Next (cursor paging). */
	let {
		pageSize,
		pageIndex,
		loading,
		nextCursor,
		onpagesize,
		onprevious,
		onnext
	}: {
		pageSize: 10 | 25 | 50 | 100;
		pageIndex: number;
		loading: boolean;
		/** The server's cursor to the next page; null on the last page. */
		nextCursor: string | null;
		onpagesize: (event: Event) => void;
		onprevious: () => void;
		onnext: () => void;
	} = $props();
</script>

<div class="library-pager" aria-label="Strategy pagination">
	<label
		>Rows per page
		<select data-testid="strategy-page-size" value={pageSize} onchange={onpagesize}>
			{#each [10, 25, 50, 100] as size (size)}<option value={size}>{size}</option>{/each}
		</select>
	</label>
	<span data-testid="strategy-page-range">Page {pageIndex + 1}</span>
	<button
		class="btn"
		type="button"
		onclick={() => onprevious()}
		disabled={loading || pageIndex === 0}
		aria-label="Previous strategy page">Previous</button
	>
	<button
		class="btn"
		type="button"
		onclick={() => onnext()}
		disabled={loading || nextCursor === null}
		aria-label="Next strategy page">Next</button
	>
</div>

<style>
	.library-pager select {
		min-height: 34px;
		padding: 0 8px;
		border: 1px solid var(--line-2);
		border-radius: var(--radius-md);
		background: var(--surface);
		color: var(--text);
	}
	.library-pager {
		display: flex;
		justify-content: flex-end;
		align-items: center;
		flex-wrap: wrap;
		gap: 12px;
		color: var(--muted);
	}
	.library-pager select {
		margin-left: 8px;
	}
</style>
