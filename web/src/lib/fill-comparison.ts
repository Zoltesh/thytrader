/**
 * Paper vs live entry fills for explicitly linked twin books on the same strategy snapshot
 * (ADR 0097, ADR 0098, ADR 0102). A paper entry fills only when a later closed candle
 * trades through the limit; the live twin fills when the venue matches it.
 * These helpers read one comparison row for the Portfolio page.
 */
import { compareDecimalStrings } from './portfolio';

export type EntryFillDigest = {
	deployment_id: string;
	portfolio_id: string | null;
	/** Each side keeps its pinned snapshot identity, including equivalent clones. */
	strategy_fingerprint?: string | null;
	status: string;
	entries_rested: number;
	entries_filled: number;
	entries_expired: number;
	entries_rejected: number;
	entries_working: number;
	/** Positive is worse than the posted limit; null with no fill. */
	average_fill_vs_limit_bps: string | null;
	average_seconds_to_fill: string | null;
	median_seconds_to_fill: string | null;
};

export type PaperLiveFillComparison = {
	strategy_fingerprint: string;
	strategy_id: string | null;
	strategy_name: string | null;
	product_id: string;
	paper: EntryFillDigest;
	live: EntryFillDigest;
};

export type PortfolioFillComparisons = {
	portfolio_id: string;
	comparisons: PaperLiveFillComparison[];
	warnings: string[];
};

const DECIMAL = /^-?\d+(\.\d+)?$/;

/** Filled share of rested entries in [0, 1], or null before any entry rested. */
export function fillShare(digest: EntryFillDigest): number | null {
	if (digest.entries_rested <= 0) return null;
	return Math.min(1, digest.entries_filled / digest.entries_rested);
}

/** `3 of 4 filled`, or `No entries yet`. */
export function fillCountText(digest: EntryFillDigest): string {
	if (digest.entries_rested === 0) return 'No entries yet';
	return `${digest.entries_filled} of ${digest.entries_rested} filled`;
}

/** Expired, rejected, and working counts that are not zero (`1 expired · 1 working`). */
export function outcomeText(digest: EntryFillDigest): string {
	const parts: string[] = [];
	if (digest.entries_expired > 0) parts.push(`${digest.entries_expired} expired`);
	if (digest.entries_rejected > 0) parts.push(`${digest.entries_rejected} rejected`);
	if (digest.entries_working > 0) parts.push(`${digest.entries_working} working`);
	return parts.join(' · ');
}

/**
 * Average fill against the posted limit (`+1.2 bps`); positive is worse than
 * the limit. Tone reads worse as `neg`, better as `pos`, at the limit `muted`.
 */
export function slippageText(
	digest: EntryFillDigest
): { text: string; tone: 'pos' | 'neg' | 'muted' } | null {
	const value = digest.average_fill_vs_limit_bps;
	if (value === null || !DECIMAL.test(value)) return null;
	const sign = compareDecimalStrings(value, '0');
	const number = Number(value);
	const shown = Math.abs(number) >= 10 ? number.toFixed(0) : number.toFixed(1);
	return {
		text: `${sign > 0 ? '+' : ''}${sign === 0 ? '0' : shown} bps`,
		tone: sign > 0 ? 'neg' : sign < 0 ? 'pos' : 'muted'
	};
}

/** `5s`, `2m 10s`, `1h 59m`, or `2d 3h`; `—` without a value. */
export function waitText(seconds: string | null): string {
	if (seconds === null || !DECIMAL.test(seconds)) return '—';
	const total = Math.max(0, Math.round(Number(seconds)));
	if (total < 60) return `${total}s`;
	const minutes = Math.floor(total / 60);
	if (minutes < 60) return total % 60 === 0 ? `${minutes}m` : `${minutes}m ${total % 60}s`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return minutes % 60 === 0 ? `${hours}h` : `${hours}h ${minutes % 60}m`;
	const days = Math.floor(hours / 24);
	return hours % 24 === 0 ? `${days}d` : `${days}d ${hours % 24}h`;
}

/** One sentence on the gap between the twins' median waits, or null when either is unknown. */
export function waitGapText(row: PaperLiveFillComparison): string | null {
	const paper = row.paper.median_seconds_to_fill;
	const live = row.live.median_seconds_to_fill;
	if (paper === null || live === null || !DECIMAL.test(paper) || !DECIMAL.test(live)) return null;
	const gap = Number(paper) - Number(live);
	if (Math.abs(gap) < 1) return 'Paper and live fill at about the same time.';
	const faster = gap > 0 ? 'Live' : 'Paper';
	return `${faster} fills ${waitText(String(Math.abs(gap)))} sooner (median).`;
}

/** Which side of a twin belongs to this portfolio: `paper`, `live`, or `both`. */
export function portfolioSide(
	row: PaperLiveFillComparison,
	portfolioId: string
): 'paper' | 'live' | 'both' | null {
	const paper = row.paper.portfolio_id === portfolioId;
	const live = row.live.portfolio_id === portfolioId;
	if (paper && live) return 'both';
	if (paper) return 'paper';
	if (live) return 'live';
	return null;
}
