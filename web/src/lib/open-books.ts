/**
 * Compact readings of one open book (ADR 0098): its state, entry, stop,
 * target, unrealized PnL at the last evaluated bar, and time since entry.
 * Shared by bot detail and Portfolio sleeve rows so both read the same way.
 */
import { formatQuoteAmount } from './deployment-portfolio';
import { positionStateLabel } from './deployments';
import { compareDecimalStrings } from './portfolio';

/** The fields of an open book that bot detail and sleeve rows both carry. */
export type OpenBook = {
	product_id: string;
	side?: 'long' | 'short' | string;
	quantity: string;
	entry_price: string;
	stop_price: string;
	/** Null when the strategy declares no take-profit (ADR 0090). */
	target_price: string | null;
	/** UTC start of the bar the book was entered on. */
	entered_bar: string;
	position_state?: string | null;
	/** Close of the newest bar the bot evaluated for this product; null without one. */
	mark_price?: string | null;
	marked_at?: string | null;
	/** Gross unrealized PnL at `mark_price`, before exit fees. */
	unrealized_pnl?: string | null;
	/** Recorded entry fees allocated to the held quantity; null if evidence is unknown. */
	entry_fees?: string | null;
	/** Gross PnL minus entry fees; future exit fees are excluded. */
	unrealized_pnl_net?: string | null;
};

export type BookTone = 'ok' | 'warn' | 'bad' | 'muted';

const DECIMAL = /^-?\d+(\.\d+)?$/;
const MINUTE = 60_000;

/** One-word state for a chip, with a tone; color is never the only signal. */
export function bookStateChip(state: string | null | undefined): { text: string; tone: BookTone } {
	switch (state) {
		case 'open_protected':
			return { text: 'Protected', tone: 'ok' };
		case 'open_unprotected':
			return { text: 'Unprotected', tone: 'bad' };
		case 'open_unverified':
			return { text: 'Unverified', tone: 'warn' };
		case 'exiting':
			return { text: 'Exiting', tone: 'warn' };
		case 'entering':
			return { text: 'Entering', tone: 'muted' };
		default:
			return { text: 'Open', tone: 'muted' };
	}
}

/** Full state sentence for a tooltip or screen reader (`Open · protected (TP/SL resting)`). */
export function bookStateTitle(book: OpenBook): string {
	return (
		positionStateLabel(book.position_state, { hasTarget: book.target_price !== null }) ??
		'Open · state not reported'
	);
}

/** `45m`, `3h 12m`, `2d 4h` since the entry bar; `—` for an unreadable or future time. */
export function heldText(enteredBar: string, now: number = Date.now()): string {
	const started = Date.parse(enteredBar);
	if (!Number.isFinite(started) || now < started) return '—';
	const minutes = Math.floor((now - started) / MINUTE);
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return minutes % 60 === 0 ? `${hours}h` : `${hours}h ${minutes % 60}m`;
	const days = Math.floor(hours / 24);
	return hours % 24 === 0 ? `${days}d` : `${days}d ${hours % 24}h`;
}

/** Signed unrealized PnL (`+12.30 USDC`) with a tone, or null when the book has no mark. */
export function unrealizedText(
	book: OpenBook,
	quote: string
): { text: string; tone: 'pos' | 'neg' | 'muted' } | null {
	const net = book.unrealized_pnl_net;
	const hasNet = net !== null && net !== undefined && DECIMAL.test(net);
	const value = hasNet ? net : book.unrealized_pnl;
	if (value === null || value === undefined || !DECIMAL.test(value)) return null;
	const sign = compareDecimalStrings(value, '0');
	const text = `${sign > 0 ? '+' : ''}${formatQuoteAmount(value)} ${quote} (${hasNet ? 'net' : 'gross'})`;
	return { text, tone: sign > 0 ? 'pos' : sign < 0 ? 'neg' : 'muted' };
}

/** Where the unrealized PnL was marked (`at the 12:00 UTC close of 61000`). */
export function markTitle(book: OpenBook): string {
	if (!book.mark_price) return 'No evaluated bar close for this product yet.';
	const at = book.marked_at ? ` at the ${book.marked_at.slice(11, 16)} UTC bar close` : '';
	const net = book.unrealized_pnl_net;
	const basis =
		net !== null && net !== undefined && DECIMAL.test(net)
			? 'net after recorded entry fees; future exit fees excluded'
			: 'gross before entry and exit fees; entry-fee evidence unavailable';
	return `Marked at ${book.mark_price}${at}; ${basis}.`;
}

/** `Long 0.01` or `Short 2`. */
export function bookSizeText(book: OpenBook): string {
	const side = book.side === 'short' ? 'Short' : 'Long';
	return `${side} ${book.quantity}`;
}
