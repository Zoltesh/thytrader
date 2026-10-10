import { describe, expect, it } from 'vitest';
import {
	capitalBreakdown,
	displayQuoteOf,
	ledgerPerformanceText,
	marketLabel,
	productIdQuote,
	quoteAmountLabel
} from './deployment-detail';
import type { Deployment } from './deployments';

const FUTURES = 'BIP-20DEC30-CDE';
const SPOT_IDS = ['BTC-USDC', 'UNI-USD', 'ETH-USDT', 'WEIRD', 'A-B-C', '', 'BTC-'];

function bot(productId: string): Deployment {
	return {
		id: 'dep-1',
		strategy_fingerprint: 'sha256:' + 'a'.repeat(64),
		strategy_id: 'strat-1',
		kind: 'strategy',
		timeframe: '1h',
		product_id: productId,
		mode: 'paper',
		status: 'running',
		phase: 'flat',
		cash: '980.30',
		paper_starting_cash: '1000',
		last_evaluated_bar: null,
		last_signal: null,
		mismatch_detail: null,
		pending_entry_bars: 0,
		bars_held: 0,
		lifecycle_command: 'none',
		daily_loss_latched: false,
		drawdown_latched: false,
		revision: 1,
		worker_lease_held: true,
		created_at: '2026-10-09T00:00:00Z',
		updated_at: '2026-10-10T00:00:00Z',
		position: null,
		ledger: {
			trade_count: 2,
			total_net_pnl: '-19.70',
			total_return_fraction: '-0.0197',
			mark_complete: true,
			marked_exposure: null
		},
		capital: { allocated_capital: '1000', performance_equity: '980.30' },
		orders: [],
		fills: []
	} as unknown as Deployment;
}

describe('futures quote labels on the bot detail', () => {
	it('labels a futures book USD, never the contract tail', () => {
		expect(productIdQuote(FUTURES)).toBe('20DEC30-CDE');
		expect(displayQuoteOf(FUTURES)).toBe('USD');
		expect(displayQuoteOf(' bip-20dec30-cde ')).toBe('USD');
		expect(quoteAmountLabel('1000', FUTURES)).toBe('1000 USD');
		expect(marketLabel(FUTURES)).toBe(FUTURES);
		expect(ledgerPerformanceText(bot(FUTURES))).toBe(
			'2 closed trades · net P&L -19.70 USD · return -1.97%'
		);
		const rows = capitalBreakdown(bot(FUTURES)) ?? [];
		expect(rows.find((row) => row.label === 'Allocated capital')?.value).toBe('1000 USD');
		expect(rows.find((row) => row.label === 'Ledger equity')?.value).toBe('980.30 USD');
		for (const row of rows) expect(row.value).not.toContain('CDE');
	});

	it('keeps every spot label byte-identical to the spot parser', () => {
		for (const id of SPOT_IDS) {
			expect(displayQuoteOf(id)).toBe(productIdQuote(id));
			const quote = productIdQuote(id);
			expect(marketLabel(id)).toBe(
				quote === null ? id : `${id.slice(0, id.length - quote.length - 1)} / ${quote}`
			);
			expect(quoteAmountLabel('5', id)).toBe(`5 ${quote ?? 'unknown quote'}`);
		}
		expect(marketLabel('UNI-USDC')).toBe('UNI / USDC');
		expect(quoteAmountLabel('10000', 'UNI-USD')).toBe('10000 USD');
		expect(ledgerPerformanceText(bot('BTC-USDC'))).toBe(
			'2 closed trades · net P&L -19.70 USDC · return -1.97%'
		);
		const rows = capitalBreakdown(bot('BTC-USDC')) ?? [];
		expect(rows.find((row) => row.label === 'Allocated capital')?.value).toBe('1000 USDC');
	});
});
