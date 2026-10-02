/**
 * Portfolio page e2e fixtures (ADR 0088): realistic Core LIVE / Lab Paper portfolios, a
 * completed portfolio backtest, and a stateful mock of `/api/v1/portfolios` plus the
 * strategies, deployments, and fees routes the page reads.
 */
import type { Page, Route } from '@playwright/test';

export type Json = Record<string, unknown>;

export const CORE = '01a0f000-0000-7000-8000-000000000c0e';
export const LAB = '01a0f000-0000-7000-8000-000000001ab0';
export const EMA = '01a0f000-0000-7000-8000-0000000000e1';
export const RSI = '01a0f000-0000-7000-8000-0000000000e2';
export const SOL = '01a0f000-0000-7000-8000-0000000000e3';
export const USD = '01a0f000-0000-7000-8000-0000000000e4';
export const RESULT = 'sha256:' + 'f'.repeat(64);

export function sleeve(
	id: string,
	strategyId: string,
	name: string,
	product: string,
	weight: string
): Json {
	return {
		sleeve_id: id,
		strategy_id: strategyId,
		strategy_name: name,
		product_id: product,
		covered_product_ids: [product],
		timeframe: '1h',
		quote_currency: 'USDC',
		strategy_valid: true,
		current_fingerprint: 'sha256:' + 'a'.repeat(64),
		weight_fraction: weight,
		capital_quote: '0',
		note: null,
		issues: [],
		created_at: '2026-09-30T10:00:00Z',
		updated_at: '2026-09-30T10:00:00Z'
	};
}

/** Each sleeve's capital is its weight of the portfolio's capital, as the API computes it. */
function withSleeveCapital(portfolio: Json): Json {
	const capital = Number(portfolio.capital_quote);
	const sleeves = (portfolio.sleeves as Json[]).map((item) => ({
		...item,
		capital_quote: String(Math.round(Number(item.weight_fraction) * capital * 1e8) / 1e8)
	}));
	return { ...portfolio, sleeves };
}

export function portfolioFixture(overrides: Json = {}): Json {
	return withSleeveCapital({
		portfolio_id: CORE,
		name: 'Core',
		mode: 'live',
		quote_currency: 'USDC',
		capital_quote: '300',
		cash_reserve_fraction: '0.17',
		revision: 3,
		created_at: '2026-09-30T10:00:00Z',
		updated_at: '2026-09-30T10:00:00Z',
		limits: {
			max_total_exposure_fraction: '0.83',
			max_per_asset_fraction: '0.6',
			daily_loss_quote: '15',
			max_drawdown_fraction: '0.2'
		},
		manager: {
			mandate: 'Grow Core steadily with low drawdown.',
			permissions: {
				may_rebalance: true,
				max_weight_change_per_week: '0.1',
				may_pause_sleeves: true,
				may_propose_sleeves: false
			}
		},
		sleeves: [
			sleeve('5eee0000-0000-7000-8000-000000000001', EMA, 'EMA Trend Pullback', 'BTC-USDC', '0.5'),
			sleeve('5eee0000-0000-7000-8000-000000000002', RSI, 'RSI Reversion', 'ETH-USDC', '0.33')
		],
		allocation: {
			allocated_fraction: '0.83',
			cash_reserve_fraction: '0.17',
			unallocated_fraction: '0',
			allocated_quote: '249',
			cash_reserve_quote: '51',
			unallocated_quote: '0',
			assets: [
				{
					asset: 'BTC',
					weight_fraction: '0.5',
					sleeve_ids: ['5eee0000-0000-7000-8000-000000000001']
				},
				{
					asset: 'ETH',
					weight_fraction: '0.33',
					sleeve_ids: ['5eee0000-0000-7000-8000-000000000002']
				}
			],
			largest_asset: {
				asset: 'BTC',
				weight_fraction: '0.5',
				sleeve_ids: ['5eee0000-0000-7000-8000-000000000001']
			},
			largest_asset_within_limit: true
		},
		deployable: true,
		...overrides
	});
}

export function labFixture(): Json {
	return portfolioFixture({
		portfolio_id: LAB,
		name: 'Lab',
		mode: 'paper',
		capital_quote: '2000',
		cash_reserve_fraction: '0',
		revision: 1,
		sleeves: [
			sleeve('5eee0000-0000-7000-8000-000000000003', SOL, 'SOL Breakout', 'SOL-USDC', '0.25')
		],
		allocation: {
			allocated_fraction: '0.25',
			cash_reserve_fraction: '0',
			unallocated_fraction: '0.75',
			allocated_quote: '500',
			cash_reserve_quote: '0',
			unallocated_quote: '1500',
			assets: [{ asset: 'SOL', weight_fraction: '0.25', sleeve_ids: [] }],
			largest_asset: { asset: 'SOL', weight_fraction: '0.25', sleeve_ids: [] },
			largest_asset_within_limit: true
		}
	});
}

export function resultFixture(): Json {
	const curve = Array.from({ length: 30 }, (_, index) => ({
		at: `2026-09-${String(index + 1).padStart(2, '0')}T00:00:00Z`,
		equity: String(300 + index * 1.5 - (index % 5 === 4 ? 6 : 0)),
		basket_equity: String(300 + index - (index % 3 === 2 ? 4 : 0))
	}));
	const sleeveResult = (
		id: string,
		strategyId: string,
		name: string,
		product: string,
		weight: string,
		ret: string,
		contribution: string,
		trades: number,
		rest: string
	) => ({
		sleeve_id: id,
		strategy_id: strategyId,
		strategy_name: name,
		strategy_fingerprint: 'sha256:' + 'a'.repeat(64),
		product_id: product,
		covered_product_ids: [product],
		timeframe: '1h',
		weight_fraction: weight,
		capital_quote: '150',
		run_fingerprint: 'sha256:' + 'b'.repeat(64),
		result_fingerprint: 'sha256:' + 'c'.repeat(64),
		dataset_fingerprint: 'sha256:' + 'd'.repeat(64),
		final_equity: '177.6',
		total_return_fraction: ret,
		maximum_drawdown_fraction: '0.072',
		trade_count: trades,
		win_rate: '0.55',
		contribution_fraction: contribution,
		correlation_to_rest: rest,
		long_fraction: '0.4'
	});
	return {
		schema_version: '1.0',
		contract: 'thytrader-portfolio-backtest-v1',
		engine: 'thytrader-backtest',
		portfolio_id: CORE,
		portfolio_revision: 3,
		portfolio_name: 'Core',
		mode: 'live',
		quote_currency: 'USDC',
		capital_quote: '300',
		cash_reserve_fraction: '0.17',
		evaluation_start: '2026-09-01T00:00:00Z',
		evaluation_end: '2026-09-30T00:00:00Z',
		costs: {
			maker_fee_rate: '0.004',
			taker_fee_rate: '0.006',
			fixed_slippage_bps: '5',
			spread_bps: '0'
		},
		sleeves: [
			sleeveResult(
				'5eee0000-0000-7000-8000-000000000001',
				EMA,
				'EMA Trend Pullback',
				'BTC-USDC',
				'0.5',
				'0.184',
				'0.092',
				41,
				'0.41'
			),
			sleeveResult(
				'5eee0000-0000-7000-8000-000000000002',
				RSI,
				'RSI Reversion',
				'ETH-USDC',
				'0.33',
				'0.079',
				'0.026',
				63,
				'0.18'
			)
		],
		summary: {
			initial_equity: '300',
			final_equity: '335.4',
			total_net_pnl: '35.4',
			total_return_fraction: '0.118',
			maximum_drawdown: '14.4',
			maximum_drawdown_fraction: '0.048',
			idle_capital_fraction: '0.38',
			allocated_fraction: '0.83',
			cash_quote: '51',
			trade_count: 104,
			best_sleeve_return_fraction: '0.184',
			best_sleeve_maximum_drawdown_fraction: '0.072',
			grid_points: 30
		},
		metrics: {
			risk_free_rate: '0',
			annualization: 'union_grid_median_spacing',
			bar_seconds: '86400',
			bars_per_year: '365',
			sharpe: '1.4321',
			sortino: '2.1',
			calmar: '3.2',
			cagr: '0.31',
			annualized_volatility: '0.2'
		},
		correlation: {
			return_clock_seconds: 3600,
			observations: 696,
			pairs: [
				{
					sleeve_ids: [
						'5eee0000-0000-7000-8000-000000000001',
						'5eee0000-0000-7000-8000-000000000002'
					],
					coefficient: '0.2'
				}
			]
		},
		overlap: {
			same_asset_fraction: '0',
			long_together_fraction: '0.31',
			pairs: [],
			excluded_sleeve_ids: []
		},
		basket: {
			legs: [
				{
					product_id: 'BTC-USDC',
					dataset_fingerprint: 'sha256:' + 'd'.repeat(64),
					timeframe: '1h',
					entry_price: '60000',
					exit_price: '63000',
					quantity: '0.002',
					return_fraction: '0.05'
				},
				{
					product_id: 'ETH-USDC',
					dataset_fingerprint: 'sha256:' + 'e'.repeat(64),
					timeframe: '1h',
					entry_price: '2600',
					exit_price: '2500',
					quantity: '0.04',
					return_fraction: '-0.04'
				}
			],
			invested_quote: '249',
			cash_quote: '51',
			final_equity: '301.1',
			total_return_fraction: '0.0037',
			maximum_drawdown_fraction: '0.061',
			total_fees: '2.9'
		},
		equity_curve: curve,
		disclosures: [
			'Sleeves simulated independently on fixed capital slices; portfolio-level caps and cross-sleeve interactions are not simulated.',
			'The cash reserve and any unallocated capital are held as cash and earn nothing.'
		]
	};
}

export type MockState = {
	portfolios: Json[];
	unavailable: boolean;
	calls: { method: string; path: string; body: unknown }[];
	jobPolls: number;
	results: boolean;
	conflictOnWeights: boolean;
	rejectBacktest: boolean;
	/** Sleeve bot status by sleeve id ('running' | 'paused' | 'stopped'); absent = never started. */
	bots: Record<string, string>;
	breakerLatched: boolean;
	proposals: Json[];
	/** Open books by sleeve id for running sleeve bots (ADR 0098). */
	books: Record<string, Json[]>;
	/** Rows for GET /fill-comparisons (ADR 0098). */
	fillComparisons: Json[];
};

export function newPortfolioState(portfolios: Json[]): MockState {
	return {
		portfolios,
		unavailable: false,
		calls: [],
		jobPolls: 0,
		results: false,
		conflictOnWeights: false,
		rejectBacktest: false,
		bots: {},
		breakerLatched: false,
		proposals: [],
		books: {},
		fillComparisons: []
	};
}

export const PROPOSAL = '0d000000-0000-7000-8000-000000000001';

/** A pending manager proposal to move weight between the two Core sleeves. */
export function proposalFixture(overrides: Json = {}): Json {
	return {
		proposal_id: PROPOSAL,
		portfolio_id: CORE,
		kind: 'rebalance',
		status: 'pending',
		summary: 'Rebalance: EMA Trend Pullback 50% → 60%; RSI Reversion 33% → 23%.',
		rationale:
			"EMA Trend is running at 1.3x its backtested return, while RSI Reversion's win rate (38%) is below its walk-forward range (44-52%).",
		change: {
			kind: 'rebalance',
			weights: [
				{ sleeve_id: '5eee0000-0000-7000-8000-000000000001', weight_fraction: '0.6' },
				{ sleeve_id: '5eee0000-0000-7000-8000-000000000002', weight_fraction: '0.23' }
			]
		},
		evidence: [{ kind: 'study', ref: 'sha256:' + 'b'.repeat(64) }],
		base_revision: 3,
		submitted_by: 'manager',
		channel: 'api',
		approval_reason: 'This is a live portfolio: a rebalance moves real capital and needs approval.',
		weight_moved: '0.1',
		created_at: '2026-10-02T14:05:00Z',
		expires_at: '2026-10-09T14:05:00Z',
		...overrides
	};
}

function sleeveBot(
	portfolio: Json,
	item: Json,
	status: string,
	latched: boolean,
	books: Json[] = []
): Json {
	const paperCash = portfolio.mode === 'paper' ? item.capital_quote : null;
	return {
		deployment_id: `de${String(item.sleeve_id).slice(2)}`,
		strategy_id: item.strategy_id,
		strategy_name: item.strategy_name,
		status,
		phase: 'flat',
		lifecycle_command: status === 'paused' ? 'stop_new_entries' : 'none',
		mismatch_detail:
			status === 'paused' && latched
				? 'PORTFOLIO_DRAWDOWN_STOP: Portfolio equity is 21% below its peak.'
				: null,
		allocated_capital: item.capital_quote,
		paper_starting_cash: paperCash,
		performance_equity: null,
		net_pnl: '0',
		return_fraction: '0',
		drawdown_fraction: '0',
		exposure_quote: '0',
		open_books: books.length,
		books,
		position_state: books.length > 0 ? 'open_protected' : 'flat',
		strategy_fingerprint: 'sha256:' + 'a'.repeat(64),
		running_current_rules: true,
		created_at: '2026-10-02T12:00:00Z',
		updated_at: '2026-10-02T12:00:00Z'
	};
}

/** The GET /deployment body for one portfolio, from the mocked sleeve bot statuses. */
/** A mock-only product of two decimal strings, rounded to cents. */
function times(left: unknown, right: unknown): string {
	return String(Math.round(Number(left) * Number(right) * 100) / 100);
}

export function deploymentFixture(state: MockState, id: string): Json {
	const portfolio = find(state, id);
	const limits = portfolio.limits as Json;
	const sleeves = (portfolio.sleeves as Json[]).map((item) => {
		const status = state.bots[String(item.sleeve_id)];
		return {
			sleeve_id: item.sleeve_id,
			strategy_id: item.strategy_id,
			strategy_name: item.strategy_name,
			product_id: item.product_id,
			timeframe: item.timeframe,
			weight_fraction: item.weight_fraction,
			target_capital_quote: item.capital_quote,
			issues: item.issues,
			deployment:
				status === undefined
					? null
					: sleeveBot(
							portfolio,
							item,
							status,
							state.breakerLatched,
							state.books[String(item.sleeve_id)] ?? []
						)
		};
	});
	const statuses = sleeves.map((item) => (item.deployment as Json | null)?.status ?? null);
	const running = statuses.filter((value) => value === 'running').length;
	const paused = statuses.filter((value) => value === 'paused').length;
	let deploymentState = 'not_deployed';
	if (statuses.some((value) => value !== null)) {
		if (running === sleeves.length) deploymentState = 'running';
		else if (running > 0) deploymentState = 'partially_running';
		else if (paused > 0) deploymentState = 'paused';
		else deploymentState = 'stopped';
	}
	return {
		portfolio_id: id,
		name: portfolio.name,
		mode: portfolio.mode,
		quote_currency: portfolio.quote_currency,
		capital_quote: portfolio.capital_quote,
		revision: portfolio.revision,
		state: deploymentState,
		sleeves,
		detached: [],
		breaker: {
			latched: state.breakerLatched,
			reason_code: state.breakerLatched ? 'PORTFOLIO_DRAWDOWN_STOP' : null,
			detail: state.breakerLatched
				? 'Portfolio equity is 21% below its peak of 300.00 USDC; its drawdown stop is 20%.'
				: null,
			latched_at: state.breakerLatched ? '2026-10-02T15:00:00Z' : null,
			daily_loss_quote: limits.daily_loss_quote,
			max_drawdown_fraction: limits.max_drawdown_fraction,
			run_started_at: deploymentState === 'not_deployed' ? null : '2026-10-02T12:00:00Z',
			equity: state.breakerLatched ? '237' : portfolio.capital_quote,
			day_open_equity:
				deploymentState === 'not_deployed'
					? null
					: state.breakerLatched
						? '249'
						: portfolio.capital_quote,
			daily_pnl: state.breakerLatched ? '-12' : '0',
			high_water_mark_equity: deploymentState === 'not_deployed' ? null : portfolio.capital_quote,
			drawdown_fraction: state.breakerLatched ? '0.21' : '0',
			evaluated_at: null
		},
		exposure: {
			total_quote: '0',
			fraction_of_capital: '0',
			cap_quote: times(portfolio.capital_quote, limits.max_total_exposure_fraction),
			asset_cap_quote: times(portfolio.capital_quote, limits.max_per_asset_fraction),
			assets: []
		},
		pending_proposals: state.proposals.filter((item) => item.status === 'pending').length
	};
}

const NEXT_STATUS: Record<string, string> = {
	start: 'running',
	pause: 'paused',
	resume: 'running',
	stop: 'stopped'
};

/** Apply one start/pause/resume/stop to every sleeve (or one) and answer like the API. */
function portfolioAction(state: MockState, id: string, action: string, sleeveId?: string): Json {
	const portfolio = find(state, id);
	const outcomes = (portfolio.sleeves as Json[])
		.filter((item) => sleeveId === undefined || item.sleeve_id === sleeveId)
		.map((item) => {
			const key = String(item.sleeve_id);
			const before = state.bots[key];
			const target = NEXT_STATUS[action];
			const unchanged =
				(action === 'start' && (before === 'running' || before === 'paused')) ||
				(action !== 'start' && (before === undefined || before === 'stopped' || before === target));
			if (!unchanged) state.bots[key] = target;
			return {
				sleeve_id: key,
				strategy_id: item.strategy_id,
				strategy_name: item.strategy_name,
				outcome: unchanged
					? action === 'start'
						? 'attached'
						: 'unchanged'
					: { start: 'started', pause: 'paused', resume: 'resumed', stop: 'stopped' }[action],
				deployment_id: `de${key.slice(2)}`,
				message: null
			};
		});
	portfolio.deployment_state = deploymentFixture(state, id).state;
	return { action, outcomes, deployment: deploymentFixture(state, id) };
}

export function job(status: string, progress: number): Json {
	return {
		job_id: '0b000000-0000-7000-8000-000000000001',
		portfolio_id: CORE,
		portfolio_revision: 3,
		status,
		created_at: '2026-10-02T10:00:00Z',
		updated_at: '2026-10-02T10:00:00Z',
		expires_at: '2026-10-03T10:00:00Z',
		evaluation_start: '2026-09-01T00:00:00Z',
		evaluation_end: '2026-09-30T00:00:00Z',
		sleeve_count: 2,
		progress_current: progress,
		progress_total: 3,
		error_message: null,
		failed_detail: null,
		result_fingerprint: status === 'completed' ? RESULT : null
	};
}

export function find(state: MockState, id: string): Json {
	const found = state.portfolios.find((item) => item.portfolio_id === id);
	if (found === undefined) throw new Error(`unknown portfolio ${id}`);
	return found;
}

export function bump(state: MockState, id: string, patch: Json): Json {
	const current = find(state, id);
	const next = withSleeveCapital({ ...current, ...patch, revision: Number(current.revision) + 1 });
	state.portfolios = state.portfolios.map((item) => (item.portfolio_id === id ? next : item));
	return next;
}

async function handle(route: Route, state: MockState): Promise<void> {
	const request = route.request();
	const url = new URL(request.url());
	const method = request.method();
	const body =
		request.postData() === null ? null : (JSON.parse(request.postData() ?? 'null') as unknown);
	const path = url.pathname;
	state.calls.push({ method, path, body });
	if (state.unavailable) {
		return route.fulfill({
			status: 503,
			json: {
				detail: {
					code: 'portfolio_storage_unavailable',
					message: 'Portfolio storage is unavailable.'
				}
			}
		});
	}
	const parts = path
		.replace(/^\/api\/v1\/portfolios\/?/, '')
		.split('/')
		.filter(Boolean);
	if (parts.length === 0) {
		if (method === 'POST') {
			const input = body as Json;
			const created = portfolioFixture({
				...input,
				portfolio_id: '01a0f000-0000-7000-8000-00000000beef',
				revision: 1,
				sleeves: [],
				limits: {
					max_total_exposure_fraction: '1',
					max_per_asset_fraction: '1',
					daily_loss_quote: null,
					max_drawdown_fraction: null
				},
				allocation: {
					allocated_fraction: '0',
					cash_reserve_fraction: input.cash_reserve_fraction,
					unallocated_fraction: '0.9',
					allocated_quote: '0',
					cash_reserve_quote: '100',
					unallocated_quote: '900',
					assets: [],
					largest_asset: null,
					largest_asset_within_limit: null
				}
			});
			state.portfolios.push(created);
			return route.fulfill({ status: 201, json: created });
		}
		return route.fulfill({
			json: {
				portfolios: state.portfolios,
				limit: 100,
				returned: state.portfolios.length,
				total: state.portfolios.length,
				has_more: false,
				next_cursor: null
			}
		});
	}
	const [id, section, child, grandchild] = parts;
	if (section === 'deployment') return route.fulfill({ json: deploymentFixture(state, id) });
	if (section === 'fill-comparisons') {
		return route.fulfill({
			json: { portfolio_id: id, comparisons: state.fillComparisons, warnings: [] }
		});
	}
	if (['start', 'pause', 'resume', 'stop'].includes(section ?? '')) {
		const portfolio = find(state, id);
		const live = portfolio.mode === 'live';
		const acknowledged = (body as Json | null)?.i_understand_live === true;
		if (live && (section === 'start' || section === 'resume') && !acknowledged) {
			return route.fulfill({
				status: 428,
				json: { detail: 'live_acknowledgement_required: Live trading spends real money.' }
			});
		}
		return route.fulfill({ json: portfolioAction(state, id, section as string) });
	}
	if (section === 'sleeves' && grandchild !== undefined && method === 'POST') {
		return route.fulfill({ json: portfolioAction(state, id, grandchild, child) });
	}
	if (section === 'breaker' && child === 'reset') {
		state.breakerLatched = false;
		return route.fulfill({ json: deploymentFixture(state, id) });
	}
	if (section === 'proposals') {
		if (child === undefined) {
			const rows = state.proposals.filter((item) => item.portfolio_id === id);
			return route.fulfill({
				json: {
					proposals: rows,
					limit: 20,
					returned: rows.length,
					total: rows.length,
					has_more: false,
					next_cursor: null
				}
			});
		}
		const proposal = state.proposals.find((item) => item.proposal_id === child);
		if (proposal === undefined) return route.fulfill({ status: 404, json: { detail: 'none' } });
		proposal.status = grandchild === 'approve' ? 'applied' : 'declined';
		proposal.decided_by = 'operator';
		proposal.decided_at = '2026-10-02T15:30:00Z';
		const current = find(state, id);
		return route.fulfill({
			json: { proposal, portfolio_revision: Number(current.revision) }
		});
	}
	if (section === undefined) {
		if (method === 'PATCH') return route.fulfill({ json: bump(state, id, body as Json) });
		return route.fulfill({ json: find(state, id) });
	}
	if (section === 'sleeves' && method === 'POST') {
		const input = body as Json;
		const current = find(state, id);
		const added = sleeve(
			'5eee0000-0000-7000-8000-000000000009',
			String(input.strategy_id),
			'SOL Breakout',
			'SOL-USDC',
			String(input.weight_fraction)
		);
		return route.fulfill({
			status: 201,
			json: bump(state, id, { sleeves: [...(current.sleeves as Json[]), added] })
		});
	}
	if (section === 'sleeves' && method === 'DELETE') {
		const current = find(state, id);
		const sleeves = (current.sleeves as Json[]).filter((item) => item.sleeve_id !== child);
		return route.fulfill({ json: bump(state, id, { sleeves }) });
	}
	if (section === 'weights') {
		if (state.conflictOnWeights) {
			state.conflictOnWeights = false;
			bump(state, id, {});
			return route.fulfill({
				status: 409,
				json: {
					detail: {
						code: 'portfolio_revision_conflict',
						message: 'Changed elsewhere.',
						current_revision: 4
					}
				}
			});
		}
		const input = body as { weights: { sleeve_id: string; weight_fraction: string }[] };
		const current = find(state, id);
		const sleeves = (current.sleeves as Json[]).map((item) => ({
			...item,
			weight_fraction:
				input.weights.find((w) => w.sleeve_id === item.sleeve_id)?.weight_fraction ??
				item.weight_fraction
		}));
		return route.fulfill({ json: bump(state, id, { sleeves }) });
	}
	if (section === 'journal') {
		return route.fulfill({
			json: {
				entries: [
					{
						entry_id: '0e000000-0000-7000-8000-000000000002',
						portfolio_id: id,
						occurred_at: '2026-10-01T09:00:00Z',
						kind: 'weights_changed',
						actor: 'operator',
						channel: 'browser',
						summary: 'Changed weights: EMA Trend Pullback 40% → 50%.',
						detail: {},
						revision: 3
					},
					{
						entry_id: '0e000000-0000-7000-8000-000000000001',
						portfolio_id: id,
						occurred_at: '2026-09-30T10:00:00Z',
						kind: 'created',
						actor: 'operator',
						channel: 'api',
						summary: 'Created live portfolio “Core” with 300 USDC and a 17% cash reserve.',
						detail: {},
						revision: 1
					}
				],
				limit: 20,
				returned: 2,
				total: 2,
				has_more: false,
				next_cursor: null
			}
		});
	}
	if (section === 'backtests') {
		if (method === 'POST') {
			if (state.rejectBacktest) {
				return route.fulfill({
					status: 422,
					json: {
						detail: {
							code: 'portfolio_backtest_rejected',
							message: 'Some sleeves have no usable datasets.',
							problems: [
								{
									code: 'dataset_missing',
									message: 'No verified complete dataset for ETH-USDC 1h (decision clock).',
									sleeve_id: null,
									strategy_id: RSI,
									strategy_name: 'RSI Reversion'
								}
							]
						}
					}
				});
			}
			return route.fulfill({ status: 202, json: { job: job('queued', 0), sleeves: [] } });
		}
		if (child === 'jobs' && grandchild !== undefined) {
			state.jobPolls += 1;
			const done = state.jobPolls >= 2;
			if (done) state.results = true;
			return route.fulfill({ json: done ? job('completed', 3) : job('running', 1) });
		}
		if (child === 'jobs') return route.fulfill({ json: { jobs: [], limit: 5, returned: 0 } });
		if (child !== undefined) {
			return route.fulfill({
				json: {
					result_fingerprint: RESULT,
					result: resultFixture(),
					equity_curve_points: 30,
					equity_curve_downsampled: false
				}
			});
		}
		const listing = {
			result_fingerprint: RESULT,
			portfolio_id: id,
			portfolio_revision: 3,
			published_at: '2026-10-02T10:01:00Z',
			evaluation_start: '2026-09-01T00:00:00Z',
			evaluation_end: '2026-09-30T00:00:00Z',
			sleeve_count: 2,
			total_return_fraction: '0.118',
			maximum_drawdown_fraction: '0.048',
			idle_capital_fraction: '0.38',
			basket_total_return_fraction: '0.0037',
			trade_count: 104
		};
		return route.fulfill({
			json: {
				entries: state.results ? [listing] : [],
				limit: 10,
				offset: 0,
				returned: state.results ? 1 : 0,
				has_more: false,
				next_cursor: null
			}
		});
	}
	return route.fulfill({ status: 404, json: { detail: 'not mocked' } });
}

export async function mockPortfolioApi(page: Page, state: MockState): Promise<void> {
	await page.route(
		(url) => url.pathname.startsWith('/api/v1/portfolios'),
		(route) => handle(route, state)
	);
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) => route.fulfill({ json: { deployments: [], limit: 50, offset: 0, returned: 0 } })
	);
	await page.route(
		(url) => url.pathname === '/api/v1/strategies',
		(route) =>
			route.fulfill({
				json: {
					strategies: [
						{
							strategy_id: EMA,
							name: 'EMA Trend Pullback',
							product_id: 'BTC-USDC',
							timeframe: '1h',
							valid: true
						},
						{
							strategy_id: RSI,
							name: 'RSI Reversion',
							product_id: 'ETH-USDC',
							timeframe: '1h',
							valid: true
						},
						{
							strategy_id: SOL,
							name: 'SOL Breakout',
							product_id: 'SOL-USDC',
							timeframe: '4h',
							valid: true
						},
						{
							strategy_id: USD,
							name: 'BTC dollar trend',
							product_id: 'BTC-USD',
							timeframe: '1h',
							valid: true
						}
					].map((entry) => ({
						...entry,
						revision: 1,
						current_fingerprint: 'sha256:' + 'a'.repeat(64),
						summary: '',
						backtest: null,
						paper_live: { paper: 'none', live: 'none' },
						active_deployment_count: 0,
						created_at: '2026-09-01T00:00:00Z',
						updated_at: '2026-09-01T00:00:00Z'
					})),
					total: 4,
					has_more: false,
					next_cursor: null
				}
			})
	);
	await page.route(
		(url) => url.pathname === '/api/v1/fees',
		(route) =>
			route.fulfill({
				json: {
					taker_fee_rate: '0.006',
					maker_fee_rate: '0.004',
					usd_volume_30d: '0',
					fee_tier: 'Intro 1',
					as_of: '2026-10-02T09:00:00Z',
					source: 'coinbase',
					suggested_maker_fee_rate: '0.004',
					suggested_taker_fee_rate: '0.006',
					suggestion_source: 'coinbase_account',
					suggestion_fee_tier: 'Intro 1',
					suggestion_schedule_tier_id: 'intro-1',
					suggestion_schedule_version: '2026-09',
					suggestion_schedule_as_of: '2026-09-01',
					schedule_maker_fee_rate: '0.004',
					schedule_taker_fee_rate: '0.006',
					suggestion_fetched_at: '2026-10-02T09:00:00Z'
				}
			})
	);
}

/** One open long book for a running sleeve bot, marked at the last bar (ADR 0098). */
export function openBookFixture(overrides: Json = {}): Json {
	return {
		product_id: 'BTC-USDC',
		side: 'long',
		quantity: '0.0083',
		entry_price: '60125.5',
		stop_price: '58900',
		target_price: '63800',
		entered_bar: '2026-10-02T09:00:00Z',
		position_state: 'open_protected',
		mark_price: '61040.25',
		marked_at: '2026-10-02T13:00:00Z',
		unrealized_pnl: '7.59',
		...overrides
	};
}

/** A paper twin (outside the portfolio) of one live Core sleeve bot (ADR 0097). */
export function fillComparisonFixture(liveDeploymentId: string, overrides: Json = {}): Json {
	const digest = (deployment: string, portfolio: string | null, extra: Json): Json => ({
		deployment_id: deployment,
		portfolio_id: portfolio,
		status: 'running',
		entries_rested: 6,
		entries_filled: 4,
		entries_expired: 2,
		entries_rejected: 0,
		entries_working: 0,
		average_fill_vs_limit_bps: '0',
		average_seconds_to_fill: '5400',
		median_seconds_to_fill: '7195',
		...extra
	});
	return {
		strategy_fingerprint: 'sha256:' + 'a'.repeat(64),
		strategy_id: EMA,
		strategy_name: 'EMA Trend Pullback',
		product_id: 'BTC-USDC',
		paper: digest('9a9e0000-0000-7000-8000-000000000001', null, {}),
		live: digest(liveDeploymentId, CORE, {
			entries_rested: 5,
			entries_filled: 5,
			entries_expired: 0,
			average_fill_vs_limit_bps: '1.8',
			average_seconds_to_fill: '9',
			median_seconds_to_fill: '6'
		}),
		...overrides
	};
}
