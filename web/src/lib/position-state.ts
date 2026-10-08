/*
 * Operator position states, shared by the deployment client (`$lib/deployments`,
 * which re-exports them) and the protection badges (`$lib/protection-evidence`),
 * so neither of those modules has to import the other.
 */

/**
 * What a book is doing in operator terms (ADR 0097). The raw `phase` reads
 * `pending_exit` while a TP/SL bracket merely rests; this does not.
 */
export type PositionState =
	'flat' | 'entering' | 'open_protected' | 'open_unprotected' | 'open_unverified' | 'exiting';

/** Plain-language label for a position state, or null when the server sent none. */
export function positionStateLabel(
	state: string | null | undefined,
	options: { hasTarget?: boolean } = {}
): string | null {
	switch (state) {
		case 'flat':
			return 'Flat';
		case 'entering':
			return 'Entry resting';
		case 'open_protected':
			if (options.hasTarget === true) return 'Open · protected (TP/SL resting)';
			if (options.hasTarget === false) return 'Open · protected (stop resting)';
			return 'Open · protected';
		case 'open_unprotected':
			return 'Open · unprotected';
		case 'open_unverified':
			return 'Open · protection unverified';
		case 'exiting':
			return 'Exiting';
		default:
			return null;
	}
}
