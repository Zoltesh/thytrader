import { describe, expect, it } from 'vitest';
import type { TradeReasonRecord } from './memory';
import {
	sortTradeReasons,
	tradeReasonKindLabel,
	tradeReasonNotesText,
	tradeReasonReconcileText,
	tradeReasonTone
} from './trade-reasons';

function reason(overrides: Partial<TradeReasonRecord> = {}): TradeReasonRecord {
	return {
		schema_version: 'thytrader-trade-reason-v1',
		id: 'r1',
		created_at: '2026-09-28T19:00:05Z',
		origin: 'runtime',
		intent_id: 'i1',
		deployment_id: 'd1',
		deployment_kind: 'strategy',
		mode: 'live',
		product_id: 'ETH-USDC',
		purpose: 'entry',
		side: 'buy',
		strategy: { strategy_id: 's', name: 'RSI Reversion', version: 2 },
		signal: {
			kind: 'strategy_entry',
			last_signal: 'matched',
			candle_starts_at: '2026-09-28T18:00:00Z',
			timeframe: '1h'
		},
		risk: {
			decision: 'allow',
			reason_code: 'ALLOWED',
			detail: '',
			policy_fingerprint: 'sha256:x',
			policy_source: 'published'
		},
		notes: [],
		reconcile: {
			order_id: 'abcdef1234567890',
			order_status: 'filled',
			filled_quantity: '0.0381',
			reject_reason: null,
			unknown_timeout: false,
			ledger_available: true,
			fills: [{ fill_id: 'f', price: '2611.4', quantity: '0.0381', fee: '0.1', filled_at: 'x' }]
		},
		...overrides
	} as TradeReasonRecord;
}

describe('trade reason wording', () => {
	it('labels kinds and tones without relying on color', () => {
		expect(tradeReasonKindLabel(reason())).toBe('Entry');
		expect(
			tradeReasonKindLabel(reason({ signal: { ...reason().signal, kind: 'take_profit' } }))
		).toBe('Take profit');
		expect(
			tradeReasonKindLabel(reason({ signal: { ...reason().signal, kind: 'odd_kind_x' } }))
		).toBe('odd kind x');
		expect(tradeReasonTone(reason({ signal: { ...reason().signal, kind: 'stop' } }))).toBe('neg');
		expect(tradeReasonTone(reason({ risk: { ...reason().risk, decision: 'deny' } }))).toBe('muted');
	});

	it('reconciles only what the payload proves', () => {
		expect(tradeReasonReconcileText(reason())).toBe('Order abcdef12 · filled · 1 fill');
		expect(
			tradeReasonReconcileText(
				reason({ reconcile: { ...reason().reconcile, ledger_available: false } })
			)
		).toMatch(/ledger unavailable/);
		expect(
			tradeReasonReconcileText(
				reason({ reconcile: { ...reason().reconcile, unknown_timeout: true } })
			)
		).toMatch(/timed out/);
		expect(
			tradeReasonReconcileText(
				reason({ reconcile: { ...reason().reconcile, order_id: null, order_status: null } })
			)
		).toBe('Intent recorded; no venue-visible order found.');
	});

	it('states an empty note explicitly and sorts newest first', () => {
		expect(tradeReasonNotesText(reason())).toBe('No operator note.');
		const sorted = sortTradeReasons([
			reason({ id: 'old', created_at: '2026-09-01T00:00:00Z' }),
			reason({ id: 'new', created_at: '2026-09-29T00:00:00Z' })
		]);
		expect(sorted.map((item) => item.id)).toEqual(['new', 'old']);
	});
});
