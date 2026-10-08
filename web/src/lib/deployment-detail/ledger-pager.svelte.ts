import type { DeploymentLedgerPage } from '$lib/deployments';

/**
 * One cursor-paginated ledger list (orders or fills) of the bot detail page,
 * with its own error, loading, and page-history state.
 *
 * `load(cursor, pageIndex)` fetches one page; a failure keeps the rows already
 * shown and records the message (or `unavailable` for a non-Error throw). The
 * cursor history lets Previous/Retry re-request an earlier page.
 */
export class LedgerPager<T> {
	page = $state<DeploymentLedgerPage<T>>({ rows: [], nextCursor: null });
	error = $state<string | null>(null);
	loading = $state(false);
	cursors = $state<(string | undefined)[]>([undefined]);
	pageIndex = $state(0);

	readonly #fetchPage: (cursor?: string) => Promise<DeploymentLedgerPage<T>>;
	readonly #unavailable: string;

	constructor(
		fetchPage: (cursor?: string) => Promise<DeploymentLedgerPage<T>>,
		unavailable: string
	) {
		this.#fetchPage = fetchPage;
		this.#unavailable = unavailable;
	}

	async load(cursor?: string, pageIndex = 0): Promise<void> {
		this.loading = true;
		this.error = null;
		try {
			const page = await this.#fetchPage(cursor);
			this.page = page;
			this.pageIndex = pageIndex;
			this.cursors = [...this.cursors.slice(0, pageIndex), cursor];
		} catch (caught) {
			this.error = caught instanceof Error ? caught.message : this.#unavailable;
		} finally {
			this.loading = false;
		}
	}
}
