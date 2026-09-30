/**
 * Rune-backed live context the shell reads (see `live-context.ts`).
 *
 * Routes call `declareLiveContext()` from an `$effect` and return its
 * release function, so navigating away or leaving the live state clears the
 * chrome automatically:
 *
 * ```ts
 * $effect(() => {
 *   if (!isLive) return;
 *   return declareLiveContext({ kind: 'bot', productId, cap });
 * });
 * ```
 */
import { LiveContextRegistry, type LiveContext } from './live-context';

class LiveChrome {
	#registry = new LiveContextRegistry();
	current = $state<LiveContext | null>(null);

	declare(context: LiveContext): () => void {
		const release = this.#registry.declare(context);
		this.current = this.#registry.current;
		return () => {
			release();
			this.current = this.#registry.current;
		};
	}
}

export const liveChrome = new LiveChrome();

/** Declare a live context; returns the release function for `$effect` cleanup. */
export function declareLiveContext(context: LiveContext): () => void {
	return liveChrome.declare(context);
}
