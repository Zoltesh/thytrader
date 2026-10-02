/**
 * One independently loaded Home data source (ADR 0084).
 *
 * Every Home card reads its own sources, so a slow or failing endpoint (the
 * data catalog can take ~20 s; the Coinbase portfolio is slow) never blocks
 * the rest of the page. A reload keeps the last good value while it runs and
 * after it fails; a newer reload always wins over an older one that resolves
 * late (for example two quick chart-range clicks).
 */
import { errorMessage, type Load } from './load';

export class Resource<T> {
	/** Replaced wholesale on every transition, so it is raw (not deeply proxied). */
	state: Load<T> = $state.raw({ status: 'loading', data: null });
	#loader: () => Promise<T>;
	#generation = 0;

	constructor(loader: () => Promise<T>) {
		this.#loader = loader;
	}

	/** Fetch again; resolves once this generation settles (ready or error). */
	async reload(): Promise<void> {
		const generation = ++this.#generation;
		this.state = { status: 'loading', data: this.state.data };
		try {
			const data = await this.#loader();
			if (generation === this.#generation) this.state = { status: 'ready', data };
		} catch (caught) {
			if (generation === this.#generation) {
				this.state = { status: 'error', data: this.state.data, error: errorMessage(caught) };
			}
		}
	}
}
