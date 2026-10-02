/**
 * Display helpers shared by Home cards. Display only: amounts stay exact
 * decimal strings everywhere else.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `Sep 29 09:05 UTC`, or the raw text when it is not a timestamp. */
export function formatShortUtc(iso: string): string {
	const time = Date.parse(iso);
	if (!Number.isFinite(time)) return iso;
	const date = new Date(time);
	const hours = String(date.getUTCHours()).padStart(2, '0');
	const minutes = String(date.getUTCMinutes()).padStart(2, '0');
	return `${MONTHS[date.getUTCMonth()]} ${date.getUTCDate()} ${hours}:${minutes} UTC`;
}

/** Compact elapsed time: `42s`, `7 min`, `5 h`, `3 days`. Negative clock skew reads as `0s`. */
export function formatAge(milliseconds: number): string {
	const seconds = Math.max(0, Math.floor(milliseconds / 1000));
	if (seconds < 60) return `${seconds}s`;
	const minutes = Math.floor(seconds / 60);
	if (minutes < 60) return `${minutes} min`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return `${hours} h`;
	return `${Math.floor(hours / 24)} days`;
}

/** Typographic minus for display (`-$12.00` → `−$12.00`, `-1.20%` → `−1.20%`). */
export function displayMinus(text: string): string {
	return text.startsWith('-') ? `−${text.slice(1)}` : text;
}

/** Shorten free text from the API for one supporting line. */
export function truncate(text: string, limit: number): string {
	const clean = text.replace(/\s+/g, ' ').trim();
	return clean.length <= limit ? clean : `${clean.slice(0, limit - 1).trimEnd()}…`;
}
