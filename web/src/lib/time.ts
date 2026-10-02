/**
 * Deterministic UTC display for evidence timestamps (audit and backtests).
 */

export function formatUtcTimestamp(value: string): string {
	/** Render one instant as `YYYY-MM-DD HH:MM:SS UTC` from its ISO value. */
	return `${new Date(value).toISOString().slice(0, 19).replace('T', ' ')} UTC`;
}

/** Render one instant as `YYYY-MM-DD HH:MM UTC` (minute precision, for approximate times). */
export function formatUtcMinute(value: string | Date): string {
	const instant = typeof value === 'string' ? new Date(value) : value;
	return `${instant.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}
