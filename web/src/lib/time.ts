/**
 * Deterministic UTC display for evidence timestamps (audit and backtests).
 */

export function formatUtcTimestamp(value: string): string {
	/** Render one instant as `YYYY-MM-DD HH:MM:SS UTC` from its ISO value. */
	return `${new Date(value).toISOString().slice(0, 19).replace('T', ' ')} UTC`;
}
