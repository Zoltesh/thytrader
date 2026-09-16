export type DeploymentPosition = {
	quantity: string;
	entry_price: string;
	stop_price: string;
	target_price: string;
	entered_bar: string;
	side?: 'long' | 'short' | string;
	trail_extreme?: string | null;
};

export type DeploymentOrder = {
	id: string;
	client_order_id: string;
	venue_order_id: string | null;
	side: string;
	kind: string;
	quantity: string;
	price: string | null;
	stop_trigger_price?: string | null;
	take_profit_price?: string | null;
	filled_quantity: string;
	status: string;
	reject_reason: string | null;
	created_at: string;
	updated_at: string;
};

export type DeploymentFill = {
	id: string;
	order_id: string;
	venue_fill_id: string;
	price: string;
	quantity: string;
	fee: string;
	filled_at: string;
};

export type Deployment = {
	id: string;
	strategy_fingerprint: string | null;
	strategy_id: string | null;
	kind: 'strategy' | 'discretionary' | string;
	timeframe: string | null;
	product_id: string;
	mode: 'paper' | 'live';
	status: string;
	phase: string;
	cash: string;
	paper_starting_cash: string | null;
	maker_fee_rate?: string | null;
	taker_fee_rate?: string | null;
	last_evaluated_bar: string | null;
	last_signal: string | null;
	mismatch_detail: string | null;
	pending_entry_bars: number;
	bars_held: number;
	created_at: string;
	updated_at: string;
	position: DeploymentPosition | null;
	orders: DeploymentOrder[];
	fills: DeploymentFill[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(path, {
		...init,
		headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) }
	});
	if (!response.ok) {
		let message = `Request failed (${response.status})`;
		try {
			const body = (await response.json()) as { detail?: unknown };
			if (typeof body.detail === 'string') message = body.detail;
		} catch {
			/* keep fallback */
		}
		throw new Error(message);
	}
	return (await response.json()) as T;
}

export async function listDeployments(): Promise<Deployment[]> {
	return (await request<{ deployments: Deployment[] }>('/api/v1/deployments')).deployments;
}

export async function fetchDeployment(id: string): Promise<Deployment> {
	return request<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}`);
}

export async function createDeployment(input: {
	strategy_fingerprint: string;
	mode: 'paper' | 'live';
	paper_starting_cash?: string;
	maker_fee_rate?: string;
	taker_fee_rate?: string;
}): Promise<Deployment> {
	return request<Deployment>('/api/v1/deployments', {
		method: 'POST',
		body: JSON.stringify(input)
	});
}

export async function pauseDeployment(id: string): Promise<Deployment> {
	return request<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}/pause`, {
		method: 'POST'
	});
}

export async function resumeDeployment(id: string): Promise<Deployment> {
	return request<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}/resume`, {
		method: 'POST'
	});
}

export async function stopDeployment(id: string): Promise<Deployment> {
	return request<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}/stop`, {
		method: 'POST'
	});
}

export async function placeDiscretionaryOrder(input: {
	mode: 'paper' | 'live';
	product_id: string;
	stop_price: string;
	take_profit_price: string;
	idempotency_key: string;
	origin: 'human' | 'agent';
	entry_kind?: 'post_only_limit' | 'marketable';
	timeframe?: string;
	side?: 'long' | 'short';
	quantity?: string;
	quote_notional?: string;
	limit_price?: string;
	paper_starting_cash?: string;
	maker_fee_rate?: string;
	taker_fee_rate?: string;
	note?: string;
}): Promise<Deployment> {
	return request<Deployment>('/api/v1/discretionary-orders', {
		method: 'POST',
		body: JSON.stringify(input)
	});
}
