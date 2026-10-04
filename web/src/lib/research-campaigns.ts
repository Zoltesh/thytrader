import { ensureBrowserCsrfSession, mutationHeaders } from './security';

export type CampaignRecord = {
	manifest: {
		campaign_id: string;
		name: string;
		kind: 'historical' | 'prospective';
		frozen_at: string;
		deadline: string;
		gates: {
			minimum_trades: number;
			minimum_net_return_fraction: string;
			maximum_drawdown_fraction: string;
		};
	};
	manifest_fingerprint: string;
	cases: { key: string; status: string; job_id: string | null; detail: string }[];
};

export type EconomicPreflight = {
	net_target_quote_pnl: string | null;
	net_target_return_fraction: string | null;
	net_stop_quote_pnl: string;
	maker_break_even_price: string;
	target_clears_costs: boolean | null;
};

/** Read research evidence or make a browser-confirmed mutation through the CSRF boundary. */
export async function researchRequest<T>(
	path: string,
	payload?: unknown,
	mutation = false
): Promise<T> {
	if (mutation || payload !== undefined) await ensureBrowserCsrfSession();
	const response = await fetch(`/api/v1/research/${path}`, {
		method: payload === undefined ? 'GET' : 'POST',
		headers: {
			'Content-Type': 'application/json',
			...(mutation || payload !== undefined ? mutationHeaders() : {})
		},
		...(payload === undefined ? {} : { body: JSON.stringify(payload) })
	});
	if (!response.ok) {
		const error: { detail?: { message?: string } | string } = await response
			.json()
			.catch(() => ({}));
		throw new Error(
			typeof error.detail === 'object'
				? (error.detail.message ?? 'Research request failed.')
				: (error.detail ?? 'Research request failed.')
		);
	}
	return (await response.json()) as T;
}
