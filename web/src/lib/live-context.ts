/**
 * Global live chrome (slice 3 of the UI redesign).
 *
 * A route declares a "live context" while the operator is looking at live
 * exposure or composing a live order. The shell then shows the amber live
 * strip under the top bar and an inset live frame around the main column.
 * This module holds the pure wording and a tiny registry; the rune-backed
 * singleton the shell reads lives in `live-context.svelte.ts`.
 *
 * Wording never relies on color alone: the strip always starts with the
 * literal `LIVE:` label next to a warning icon.
 */
import { marketLabel } from './deployment-detail';

export type LiveContextKind = 'bot' | 'order' | 'arm';

export type LiveContext = {
	/** What real-money surface is on screen. */
	kind: LiveContextKind;
	/** Canonical Coinbase product id (`ETH-USDC`), or null when not chosen yet. */
	productId: string | null;
	/** Capital cap already labelled with its quote (`100.00 USDC`), or null when unknown. */
	cap: string | null;
};

const LEADS: Record<LiveContextKind, string> = {
	bot: 'this bot places real Coinbase orders',
	order: 'this order will be sent to Coinbase with real money',
	arm: 'arming this strategy places real Coinbase orders'
};

/**
 * Sentence after the `LIVE:` label.
 *
 * Market reads the product's own quote (`ETH / USDC`); an unknown cap is
 * omitted rather than guessed.
 */
export function liveStripText(context: LiveContext): string {
	const parts = [LEADS[context.kind]];
	if (context.productId !== null && context.productId.trim() !== '') {
		parts.push(marketLabel(context.productId.trim()));
	}
	if (context.cap !== null && context.cap.trim() !== '') {
		parts.push(`allocated ${context.cap.trim()}`);
	}
	return parts.join(' · ');
}

/**
 * Last-declared-wins registry of live contexts.
 *
 * Each declaration returns a release function; releasing an older
 * declaration never clears a newer one, so a route that re-declares on every
 * reactive change cannot race its own cleanup.
 */
export class LiveContextRegistry {
	#entries: { id: number; context: LiveContext }[] = [];
	#next = 1;

	declare(context: LiveContext): () => void {
		const id = this.#next++;
		this.#entries = [...this.#entries, { id, context }];
		return () => {
			this.#entries = this.#entries.filter((entry) => entry.id !== id);
		};
	}

	get current(): LiveContext | null {
		return this.#entries.at(-1)?.context ?? null;
	}
}
