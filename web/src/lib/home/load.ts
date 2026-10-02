/**
 * Load state of one independently fetched Home source.
 *
 * `data` survives a reload and a failed reload, so a card that already showed
 * a value keeps it (and says the refresh failed) instead of going blank.
 */
export type Load<T> =
	| { status: 'loading'; data: T | null }
	| { status: 'ready'; data: T }
	| { status: 'error'; data: T | null; error: string };

/** A failed HTTP read with its status code, so callers can tell 503 from other failures. */
export class HttpError extends Error {
	readonly status: number;

	constructor(status: number, message: string) {
		super(message);
		this.name = 'HttpError';
		this.status = status;
	}
}

/** Human message for anything a loader threw. */
export function errorMessage(caught: unknown, fallback = 'The request failed.'): string {
	if (caught instanceof Error && caught.message.trim() !== '') return caught.message;
	return fallback;
}
