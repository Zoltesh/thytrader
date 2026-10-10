/**
 * Home futures card data (ADR 0127): the newest read-only CFM futures account snapshot
 * from `GET /api/v1/operator/futures-account`.
 *
 * Every amount is USD and is never added to USDC. Coinbase counts the USDC spot balance
 * as futures collateral, so futures buying power is shared with USDC spot capital, not
 * extra money. Unknown values render as an em dash with the reason, never as zero.
 */
import { formatQuoteAmount } from '$lib/deployment-portfolio';
import { formatUsd } from '$lib/portfolio';

export type FuturesEnablement = 'enabled' | 'not_enabled' | 'unknown';

export type FuturesPositionRow = {
	product_id: string;
	side: 'long' | 'short' | 'unknown';
	number_of_contracts: string;
};

export type FuturesAccountPayload = {
	observed_at: string | null;
	age_seconds: number | null;
	stale: boolean | null;
	enablement: FuturesEnablement | null;
	read_failures: string[];
	balance: {
		currency: 'USD';
		futures_buying_power: string | null;
		available_margin: string | null;
		liquidation_threshold: string | null;
		liquidation_buffer_amount: string | null;
		liquidation_buffer_percentage: string | null;
		funding_pnl: string | null;
		unrealized_pnl: string | null;
	} | null;
	positions: FuturesPositionRow[] | null;
	margin_ratio?: string | null;
	collateral_note?: string;
};

export type FuturesAccountReport = {
	overall_status: 'healthy' | 'degraded' | 'failed';
	components: { name: string; status: string; reason_code: string; detail: string }[];
	payload: FuturesAccountPayload;
};

export type FuturesFact = { label: string; value: string; hint: string | null };

export type FuturesCardView =
	| { kind: 'hidden' }
	| {
			kind: 'account';
			enablement: FuturesEnablement;
			facts: FuturesFact[];
			positions: string;
			stale: boolean;
			failures: string[];
			sharedNote: string;
	  };

export const SHARED_BUYING_POWER_NOTE =
	'Buying power is shared with your USDC spot balance: Coinbase counts USDC as futures ' +
	'collateral, so it is not extra money.';

/** Reasons that mean futures were never observed on this install (demo, no database). */
const NOT_OBSERVED = new Set(['STORE_DISABLED', 'FUTURES_MIRROR_NOT_RUN']);

export async function fetchFuturesAccount(): Promise<FuturesAccountReport> {
	const response = await fetch('/api/v1/operator/futures-account', {
		headers: { Accept: 'application/json' }
	});
	if (!response.ok) throw new Error('The futures account report is unavailable.');
	return (await response.json()) as FuturesAccountReport;
}

function usd(value: string | null | undefined): string {
	return value ? formatUsd(value) : '—';
}

function signedUsd(value: string | null | undefined): string {
	if (!value) return '—';
	return value.startsWith('-') ? `−${formatUsd(value.slice(1))}` : `+${formatUsd(value)}`;
}

/** The card's view: hidden when futures were never observed here. */
export function futuresCardView(report: FuturesAccountReport): FuturesCardView {
	const payload = report.payload;
	if (
		payload.enablement === null ||
		report.components.some((component) => NOT_OBSERVED.has(component.reason_code))
	) {
		return { kind: 'hidden' };
	}
	const balance = payload.balance;
	const positions = payload.positions;
	const flat = positions !== null && positions.length === 0;
	const ratio = payload.margin_ratio ?? null;
	const facts: FuturesFact[] = [
		{
			label: 'Buying power',
			value: usd(balance?.futures_buying_power),
			hint: 'Shared with USDC spot'
		},
		{
			label: 'Margin ratio',
			value: ratio ? formatQuoteAmount(ratio) : '—',
			hint: ratio ? 'Available margin ÷ liquidation threshold' : flat ? 'No open positions' : null
		},
		{
			label: 'Liquidation buffer',
			value: usd(balance?.liquidation_buffer_amount),
			hint: balance?.liquidation_buffer_percentage
				? `${formatQuoteAmount(balance.liquidation_buffer_percentage)}%`
				: null
		},
		{
			label: 'Funding PnL',
			value: signedUsd(balance?.funding_pnl),
			hint: 'As reported by Coinbase, USD'
		}
	];
	return {
		kind: 'account',
		enablement: payload.enablement,
		facts,
		positions: positionsLine(positions),
		stale: payload.stale === true,
		failures: payload.read_failures,
		sharedNote: SHARED_BUYING_POWER_NOTE
	};
}

function positionsLine(positions: FuturesPositionRow[] | null): string {
	if (positions === null) return 'Open positions: unknown (the position read failed)';
	if (positions.length === 0) return 'No open futures positions';
	const rows = positions.map(
		(row) => `${row.product_id} ${row.side} ${row.number_of_contracts} contract(s)`
	);
	return `External, not managed by any bot: ${rows.join(' · ')}`;
}
