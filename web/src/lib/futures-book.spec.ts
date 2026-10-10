import { describe, expect, it } from 'vitest';
import {
	entryBlocksLine,
	futuresBookView,
	futuresBooksSection,
	percentText,
	sideLine,
	unknownLine,
	usdText,
	type FuturesBookPayload,
	type FuturesBooksReport
} from './futures-book';

function book(overrides: Partial<FuturesBookPayload> = {}): FuturesBookPayload {
	return {
		deployment_id: '01a0ad72-0000-0000-0000-00000000f001',
		strategy_name: 'BTC perp trend',
		status: 'running',
		mode: 'paper',
		product_id: 'BIP-20DEC30-CDE',
		currency: 'USD',
		contract_kind: 'perpetual_future',
		underlying: 'BTC',
		contract_size: '0.01',
		fee_per_contract: '0.15',
		maker_fee_rate: '0.0002',
		taker_fee_rate: '0.0005',
		catalog_fingerprint: 'sha256:abc',
		bound_at: '2026-10-09T12:00:00Z',
		side: 'long',
		contracts: '2',
		base_quantity: '0.02',
		entry_price: '62000',
		mark_price: '63000.5',
		marked_at: '2026-10-10T01:00:00Z',
		paper_starting_cash: '1000',
		cash: '980.30',
		equity: '1000.31',
		notional: '1260.01',
		leverage: '1.26',
		policy_max_leverage: '3',
		overnight_long_margin_rate: '0.25',
		overnight_short_margin_rate: '0.3',
		margin_observed_at: '2026-10-10T00:00:00Z',
		initial_margin: '315.0025',
		maintenance_margin: '315.0025',
		liquidation_buffer_fraction: '0.685093',
		min_liquidation_buffer_fraction: '0.2',
		liquidation_price: '28984.5',
		funding_total: '-0.42',
		funding_hours: 13,
		funding_overdue_since: null,
		recent_funding: [
			{
				funding_time: '2026-10-10T01:00:00Z',
				signed_quantity: '0.02',
				mark_price: '63000.5',
				rate: '0.0001',
				amount: '-0.0126'
			}
		],
		daily_loss_latched: false,
		entry_blocks: [],
		unknown: [],
		...overrides
	};
}

function factValue(view: ReturnType<typeof futuresBookView>, id: string): string | undefined {
	return view.groups.flatMap((group) => group.facts).find((fact) => fact.id === id)?.value;
}

function factHint(view: ReturnType<typeof futuresBookView>, id: string): string | null | undefined {
	return view.groups.flatMap((group) => group.facts).find((fact) => fact.id === id)?.hint;
}

describe('futuresBookView', () => {
	it('shows a known book in USD with policy context', () => {
		const view = futuresBookView(book());
		expect(view.groups.map((group) => group.id)).toEqual([
			'contract',
			'position',
			'risk',
			'funding'
		]);
		expect(factValue(view, 'product')).toBe('BIP-20DEC30-CDE');
		expect(factHint(view, 'product')).toBe('Perpetual');
		expect(factValue(view, 'contract-size')).toBe('0.01 BTC');
		expect(factValue(view, 'fee-per-contract')).toBe('0.15 USD');
		expect(factValue(view, 'bound-at')).toBe('2026-10-09 12:00 UTC');
		expect(factValue(view, 'side')).toBe('Long 2 contracts');
		expect(factHint(view, 'side')).toBe('0.02 BTC');
		expect(factValue(view, 'mark')).toBe('$63,000.50');
		expect(factValue(view, 'equity')).toBe('$1,000.31');
		expect(factHint(view, 'equity')).toBe('Started $1,000.00');
		expect(factValue(view, 'leverage')).toBe('1.26×');
		expect(factHint(view, 'leverage')).toBe('Policy max 3×');
		expect(factHint(view, 'initial-margin')).toBe('Overnight rate long 25.00% · short 30.00%');
		expect(factValue(view, 'liquidation-buffer')).toBe('68.51%');
		expect(factHint(view, 'liquidation-buffer')).toBe('Policy minimum 20.00%');
		expect(factValue(view, 'liquidation-price')).toBe('$28,984.50');
		expect(factValue(view, 'funding-total')).toBe('-$0.42');
		expect(factValue(view, 'funding-hours')).toBe('13');
		expect(view.fundingRows).toEqual([
			{
				key: '2026-10-10T01:00:00Z',
				time: '2026-10-10 01:00 UTC',
				quantity: '0.02',
				mark: '$63,000.50',
				rate: '0.0001',
				amount: '-0.0126 USD',
				tone: 'neg'
			}
		]);
		expect(view.entryBlocks).toBeNull();
		expect(view.unknown).toBeNull();
	});

	it('renders unknown evidence as Unknown, never zero, and names the entry blocks', () => {
		const view = futuresBookView(
			book({
				contract_kind: null,
				fee_per_contract: null,
				bound_at: null,
				mark_price: null,
				marked_at: null,
				equity: null,
				notional: null,
				leverage: null,
				initial_margin: null,
				maintenance_margin: null,
				liquidation_buffer_fraction: null,
				liquidation_price: null,
				overnight_long_margin_rate: null,
				overnight_short_margin_rate: null,
				entry_blocks: ['FUTURES_CONTRACT_UNBOUND', 'FUTURES_MARGIN_UNKNOWN'],
				unknown: ['binding', 'mark', 'margin_rates']
			})
		);
		for (const id of ['mark', 'equity', 'notional', 'leverage', 'initial-margin']) {
			expect(factValue(view, id)).toBe('Unknown');
		}
		expect(factValue(view, 'liquidation-buffer')).toBe('Unknown');
		expect(factValue(view, 'fee-per-contract')).toBe('Unknown');
		expect(factHint(view, 'product')).toBe('Unknown');
		expect(view.entryBlocks).toBe(
			'New entries denied: no contract is bound to this book (FUTURES_CONTRACT_UNBOUND); ' +
				'the overnight margin rates were never observed (FUTURES_MARGIN_UNKNOWN). ' +
				'Exits are never blocked.'
		);
		expect(view.unknown).toContain('Unknown, not zero: contract binding, mark price, margin rates');
	});

	it('shows a flat book without an entry or liquidation price', () => {
		const view = futuresBookView(
			book({ side: 'flat', contracts: '0', entry_price: null, liquidation_price: null })
		);
		expect(factValue(view, 'side')).toBe('Flat');
		expect(factValue(view, 'entry')).toBe('—');
		expect(factValue(view, 'liquidation-price')).toBe('—');
	});
});

describe('futures text helpers', () => {
	it('formats sides, percentages, USD and lines', () => {
		expect(sideLine({ side: 'short', contracts: '1' })).toBe('Short 1 contract');
		expect(sideLine({ side: 'long', contracts: null })).toBe('Long, contracts unknown');
		expect(percentText(null)).toBe('Unknown');
		expect(usdText(null)).toBe('Unknown');
		expect(usdText('1E+2')).toBe('1E+2 USD');
		expect(entryBlocksLine([])).toBeNull();
		expect(entryBlocksLine(['SOMETHING_NEW'])).toContain('SOMETHING_NEW');
		expect(unknownLine([])).toBeNull();
	});
});

describe('futuresBooksSection', () => {
	function report(
		books: FuturesBookPayload[],
		capital: string | null = '5000'
	): FuturesBooksReport {
		return {
			report_kind: 'futures_books',
			overall_status: 'healthy',
			components: [{ name: 'futures_books', status: 'healthy', reason_code: 'OK', detail: '' }],
			payload: {
				paper_capital_usd: capital,
				committed_paper_cash_usd: '1000',
				futures_policy_set: true,
				books,
				collateral_note: 'Shared with USDC.',
				live_supported: false
			}
		};
	}

	it('lists books that are not stopped with a warning chip for unknown evidence', () => {
		const section = futuresBooksSection(
			report([
				book(),
				book({ deployment_id: 'b2', status: 'stopped' }),
				book({ deployment_id: 'b3', unknown: ['mark'], equity: null, status: 'paused' })
			])
		);
		expect(section?.rows.map((row) => row.id)).toEqual([
			'01a0ad72-0000-0000-0000-00000000f001',
			'b3'
		]);
		expect(section?.rows[0]).toMatchObject({
			name: 'BTC perp trend',
			position: 'Long 2 contracts',
			equity: '$1,000.31',
			buffer: '68.51%',
			warning: null
		});
		expect(section?.rows[1]).toMatchObject({ equity: 'Unknown', warning: 'Unknown evidence' });
		expect(section?.committed).toBe('$1,000.00');
		expect(section?.capital).toBe('$5,000.00');
	});

	it('is null without active books and keeps unknown capital unknown', () => {
		expect(futuresBooksSection(report([]))).toBeNull();
		expect(futuresBooksSection(report([book({ status: 'stopped' })]))).toBeNull();
		expect(futuresBooksSection(report([book()], null))?.capital).toBe('Unknown');
		expect(
			futuresBooksSection(report([book({ entry_blocks: ['FUTURES_POLICY_UNSET'] })]))?.rows[0]
				.warning
		).toBe('Entries denied');
	});
});
