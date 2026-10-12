/** Independently synthetic complete book DTO for retry lifecycle controls. */
import type { FuturesBookPayload } from '../lib/futures-book';
export function book(overrides: Partial<FuturesBookPayload> = {}): FuturesBookPayload {
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
