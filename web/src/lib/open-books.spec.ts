import { describe, expect, it } from 'vitest';
import {
	bookSizeText,
	bookStateChip,
	bookStateTitle,
	heldText,
	markTitle,
	unrealizedText,
	type OpenBook
} from './open-books';

const book: OpenBook = {
	product_id: 'BTC-USDC',
	side: 'long',
	quantity: '0.01',
	entry_price: '60000',
	stop_price: '58000',
	target_price: '64000',
	entered_bar: '2026-10-01T08:00:00Z',
	position_state: 'open_protected',
	mark_price: '61000',
	marked_at: '2026-10-01T12:00:00Z',
	unrealized_pnl: '10'
};

describe('open book readings (ADR 0098)', () => {
	it('names the state in words with a tone', () => {
		expect(bookStateChip('open_protected')).toEqual({ text: 'Protected', tone: 'ok' });
		expect(bookStateChip('open_unprotected')).toEqual({ text: 'Unprotected', tone: 'bad' });
		expect(bookStateChip('exiting').text).toBe('Exiting');
		expect(bookStateChip(undefined)).toEqual({ text: 'Open', tone: 'muted' });
		expect(bookStateTitle(book)).toBe('Open · protected (TP/SL resting)');
		expect(bookStateTitle({ ...book, target_price: null })).toBe('Open · protected (stop resting)');
	});

	it('formats time held since the entry bar', () => {
		const entered = Date.parse(book.entered_bar);
		expect(heldText(book.entered_bar, entered + 45 * 60_000)).toBe('45m');
		expect(heldText(book.entered_bar, entered + 192 * 60_000)).toBe('3h 12m');
		expect(heldText(book.entered_bar, entered + 3 * 3_600_000)).toBe('3h');
		expect(heldText(book.entered_bar, entered + 52 * 3_600_000)).toBe('2d 4h');
		expect(heldText(book.entered_bar, entered - 1)).toBe('—');
		expect(heldText('not a time', entered)).toBe('—');
	});

	it('signs unrealized PnL and stays empty without a mark', () => {
		expect(unrealizedText(book, 'USDC')).toEqual({ text: '+10.00 USDC', tone: 'pos' });
		expect(unrealizedText({ ...book, unrealized_pnl: '-5.5' }, 'USDC')).toEqual({
			text: '-5.50 USDC',
			tone: 'neg'
		});
		expect(unrealizedText({ ...book, unrealized_pnl: null }, 'USDC')).toBeNull();
		expect(markTitle(book)).toBe(
			'Marked at 61000 at the 12:00 UTC bar close; gross, before exit fees.'
		);
		expect(markTitle({ ...book, mark_price: null })).toMatch(/No evaluated bar close/);
		expect(bookSizeText({ ...book, side: 'short' })).toBe('Short 0.01');
	});
});
