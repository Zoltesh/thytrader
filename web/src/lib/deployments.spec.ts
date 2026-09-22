import { describe, expect, it } from 'vitest';

import {
	bookTotalsReconcile,
	capitalSummary,
	canonicalBooks,
	canonicalPositions,
	fillProductId,
	orderProductId,
	type Deployment
} from './deployments';

function deployment(overrides: Partial<Deployment> = {}): Deployment {
	return {
		id: 'dep-1',
		strategy_fingerprint: 'sha256:' + 'a'.repeat(64),
		strategy_id: 'strat-1',
		kind: 'strategy',
		timeframe: '1h',
		product_id: 'BTC-USD',
		mode: 'paper',
		status: 'running',
		phase: 'flat',
		cash: '10000',
		paper_starting_cash: '10000',
		last_evaluated_bar: null,
		last_signal: null,
		mismatch_detail: null,
		pending_entry_bars: 0,
		bars_held: 0,
		lifecycle_command: 'none',
		daily_loss_latched: false,
		drawdown_latched: false,
		revision: 1,
		worker_lease_held: false,
		created_at: '2026-09-16T00:00:00+00:00',
		updated_at: '2026-09-16T00:00:00+00:00',
		position: null,
		orders: [],
		fills: [],
		...overrides
	};
}

describe('canonicalPositions', () => {
	it('keeps two open books distinct, including primary flat via the collection', () => {
		const body = deployment({
			phase: 'open',
			position: {
				product_id: 'ETH-USD',
				quantity: '0.5',
				entry_price: '3000',
				stop_price: '2800',
				target_price: '3300',
				entered_bar: '2026-09-16T01:00:00+00:00',
				side: 'short',
				protection_status: 'unprotected',
				compatibility_focus: true
			},
			positions: [
				{
					product_id: 'ETH-USD',
					quantity: '0.5',
					entry_price: '3000',
					stop_price: '2800',
					target_price: '3300',
					entered_bar: '2026-09-16T01:00:00+00:00',
					side: 'short',
					protection_status: 'unprotected',
					compatibility_focus: true
				}
			]
		});
		const books = canonicalPositions(body);
		expect(books).toHaveLength(1);
		expect(books[0]?.product_id).toBe('ETH-USD');
		expect(books[0]?.side).toBe('short');
		expect(books[0]?.quantity).toBe('0.5');
	});

	it('falls back to compatibility position with the deployment product id', () => {
		const body = deployment({
			position: {
				product_id: '',
				quantity: '0.01',
				entry_price: '100',
				stop_price: '90',
				target_price: '120',
				entered_bar: '2026-09-16T01:00:00+00:00'
			}
		});
		expect(canonicalPositions(body)[0]?.product_id).toBe('BTC-USD');
	});
});

describe('canonicalBooks', () => {
	it('lists every overlay including a flat primary beside an open secondary', () => {
		const body = deployment({
			instrument_runtimes: [
				{
					product_id: 'ETH-USD',
					phase: 'open',
					last_evaluated_bar: null,
					last_signal: 'matched',
					pending_entry_bars: 0,
					bars_held: 1,
					cooldown_bars_remaining: 0
				},
				{
					product_id: 'BTC-USD',
					phase: 'flat',
					last_evaluated_bar: null,
					last_signal: null,
					pending_entry_bars: 0,
					bars_held: 0,
					cooldown_bars_remaining: 0
				}
			]
		});
		expect(canonicalBooks(body).map((item) => item.product_id)).toEqual(['BTC-USD', 'ETH-USD']);
		expect(canonicalBooks(body).map((item) => item.phase)).toEqual(['flat', 'open']);
	});
});

describe('capitalSummary', () => {
	it('labels live venue quote separately from ledger cash', () => {
		const body = deployment({
			mode: 'live',
			cash: '0',
			capital: {
				allocated_capital: '25000',
				venue_available_quote: '50000',
				performance_equity: '24800',
				inventory_cost: '800'
			}
		});
		expect(capitalSummary(body)).toContain('allocated 25000');
		expect(capitalSummary(body)).toContain('venue 50000');
		expect(capitalSummary(body)).toContain('equity 24800');
	});

	it('marks unknown live venue quote explicitly', () => {
		const body = deployment({
			mode: 'live',
			capital: {
				allocated_capital: '10000',
				venue_available_quote: null
			}
		});
		expect(capitalSummary(body)).toContain('venue unknown');
	});
});

describe('bookTotalsReconcile', () => {
	it('matches open books, working orders, and fills', () => {
		const body = deployment({
			positions: [
				{
					product_id: 'BTC-USD',
					quantity: '0.01',
					entry_price: '100',
					stop_price: '90',
					target_price: '120',
					entered_bar: '2026-09-16T01:00:00+00:00',
					side: 'long',
					protection_status: 'unprotected'
				},
				{
					product_id: 'ETH-USD',
					quantity: '0.5',
					entry_price: '3000',
					stop_price: '3200',
					target_price: '2700',
					entered_bar: '2026-09-16T01:00:00+00:00',
					side: 'short',
					protection_status: 'unprotected'
				}
			],
			book_totals: { open_books: 2, working_orders: 1, fill_count: 1 },
			orders: [
				{
					id: 'o1',
					client_order_id: 'c1',
					venue_order_id: null,
					product_id: 'ETH-USD',
					side: 'sell',
					kind: 'post_only_limit',
					quantity: '0.5',
					price: '3000',
					filled_quantity: '0',
					status: 'open',
					reject_reason: null,
					created_at: '2026-09-16T01:00:00+00:00',
					updated_at: '2026-09-16T01:00:00+00:00'
				},
				{
					id: 'o2',
					client_order_id: 'c2',
					venue_order_id: null,
					product_id: 'BTC-USD',
					side: 'buy',
					kind: 'post_only_limit',
					quantity: '0.01',
					price: '100',
					filled_quantity: '0.01',
					status: 'filled',
					reject_reason: null,
					created_at: '2026-09-16T01:00:00+00:00',
					updated_at: '2026-09-16T01:00:00+00:00'
				}
			],
			fills: [
				{
					id: 'f1',
					order_id: 'o2',
					product_id: 'BTC-USD',
					venue_fill_id: 'v1',
					price: '100',
					quantity: '0.01',
					fee: '0',
					filled_at: '2026-09-16T01:00:00+00:00'
				}
			]
		});
		expect(bookTotalsReconcile(body)).toBe(true);
		expect(orderProductId(body, body.orders[0]!)).toBe('ETH-USD');
		expect(fillProductId(body, body.fills[0]!)).toBe('BTC-USD');
	});
});
