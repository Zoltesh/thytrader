/** Futures books envelopes can fail even when transport succeeds; keep last-good rows. */
import type { FuturesBooksReport } from '../futures-book';
import { fetchFuturesBooks } from '../futures-book-api';
import { errorMessage, type Load } from './load';

/** A failed/partial inventory is never the healthy empty inventory. */
export function booksReportFailure(report: FuturesBooksReport): string | null {
	const failures = report.components.filter((component) => component.status !== 'healthy');
	if (report.overall_status === 'healthy' && failures.length === 0) return null;
	return (
		failures.map((component) => `${component.reason_code}: ${component.detail}`).join('; ') ||
		`Futures books report is ${report.overall_status}`
	);
}

export class FuturesBooksResource {
	state: Load<FuturesBooksReport> = $state.raw({ status: 'loading', data: null });
	#loader: () => Promise<FuturesBooksReport>;
	#generation = 0;
	refreshing = $state(false);
	#lastGood: FuturesBooksReport | null = null;

	constructor(loader = fetchFuturesBooks) {
		this.#loader = loader;
	}

	/** Preserve complete rows on failure; first-load partial rows remain explicitly unverified. */
	async reload(): Promise<void> {
		const generation = ++this.#generation;
		this.refreshing = true;
		// A request is not recovery: retain failed/partial/stale evidence until success.
		if (this.state.status !== 'error') {
			this.state = { status: 'loading', data: this.state.data };
		}
		try {
			const data = await this.#loader();
			if (generation !== this.#generation) return;
			const error = booksReportFailure(data);
			if (error !== null) {
				this.state = { status: 'error', data: this.#lastGood ?? data, error };
			} else {
				this.#lastGood = data;
				this.state = { status: 'ready', data };
			}
		} catch (caught) {
			if (generation === this.#generation) {
				this.state = { status: 'error', data: this.state.data, error: errorMessage(caught) };
			}
		} finally {
			if (generation === this.#generation) this.refreshing = false;
		}
	}
}
