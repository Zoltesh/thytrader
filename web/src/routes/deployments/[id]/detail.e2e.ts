import type { Route } from '@playwright/test';
import {
	EARLIER_INTENT,
	ENTRY_INTENT,
	EXIT_INTENT,
	at,
	barDecision,
	comparison,
	decisionPageBody,
	everyOutcome,
	filterByRequest,
	mockTradeReasons,
	requestedOutcomes,
	tradeReason,
	type Json
} from '../../../e2e/decision-fixtures';
import { expect, test } from '../../../e2e/harness';

const deploymentId = '01a0ad72-0000-0000-0000-000000000000';
const fingerprintA = `sha256:${'a'.repeat(64)}`;
const fingerprintB = `sha256:${'b'.repeat(64)}`;

const strategyDraft = {
	schema_version: '1.0',
	strategy_id: '01a0ad42-0000-0000-0000-000000000000',
	name: 'UNI trend config',
	description: null,
	created_at: '2026-09-01T00:00:00Z',
	instrument: { product_id: 'UNI-USDC', base_currency: 'UNI', quote_currency: 'USDC' },
	timeframe: '2h',
	data_requirements: {
		warmup_bars: 50,
		required_fields: ['open', 'high', 'low', 'close', 'volume']
	},
	indicators: [
		{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 20 } },
		{ id: 'ema_slow', kind: 'ema', input: 'close', parameters: { period: 50 } }
	],
	entry: {
		side: 'long',
		when: {
			all: [
				{
					left: { indicator: 'ema_fast' },
					operator: 'crosses_above',
					right: { indicator: 'ema_slow' }
				}
			]
		},
		cooldown_bars: 3,
		max_open_positions: 1
	},
	sizing: {
		kind: 'risk_fraction',
		risk_fraction: '0.005',
		min_quote_notional: '10',
		max_quote_notional: '100'
	},
	portfolio_limits: { max_strategy_exposure_fraction: '0.10', max_concurrent_positions: 1 },
	exits: {
		initial_stop: { kind: 'atr_multiple', atr_indicator: 'atr_14', multiple: '2' },
		take_profit: { kind: 'reward_risk', multiple: '2' },
		trailing_stop: { enabled: false },
		time_exit: { max_bars_held: 96 }
	},
	execution: {
		entry_preference: 'maker_only',
		max_entry_wait_bars: 2,
		on_unfilled_entry: 'cancel'
	},
	metadata: { tags: [], notes: [] }
};

/** Another edit of the same rules (fast EMA 12 instead of 20). */
const earlierDraft = {
	...strategyDraft,
	indicators: [
		{ id: 'ema_fast', kind: 'ema', input: 'close', parameters: { period: 12 } },
		{ id: 'ema_slow', kind: 'ema', input: 'close', parameters: { period: 50 } }
	]
};

const performanceReport = {
	schema_version: 'thytrader-operator-report-v1',
	report_kind: 'performance',
	overall_status: 'healthy',
	partial_result_warnings: [],
	recommended_next_action: 'No action.',
	payload: {
		mode: 'paper',
		timeframe: '2h',
		currency: 'USDC',
		strategy_fingerprint: fingerprintA,
		deployment_id: deploymentId,
		trade_count: 2,
		total_net_pnl: '18.4',
		total_return_fraction: '0.0018',
		maximum_drawdown_fraction: '0.03',
		mark_complete: true,
		marked_exposure: null
	}
};

function snapshotBody(strategy: object = strategyDraft, fingerprint = fingerprintA) {
	return {
		strategy_fingerprint: fingerprint,
		strategy_id: strategyDraft.strategy_id,
		strategy_name: strategyDraft.name,
		strategy,
		created_at: '2026-09-20T00:00:00Z',
		is_current: true
	};
}

/** The owning strategy's current record; `current` is its current rules fingerprint. */
function strategyRecord(current: string | null = fingerprintA, strategy: object = strategyDraft) {
	return {
		strategy_id: strategyDraft.strategy_id,
		name: strategyDraft.name,
		revision: 3,
		created_at: strategyDraft.created_at,
		updated_at: '2026-09-25T00:00:00Z',
		document: strategy,
		strategy: current === null ? null : strategy,
		validation: { valid: current !== null, issues: [] },
		current_fingerprint: current,
		summary: null,
		product_id: 'UNI-USDC',
		timeframe: '2h'
	};
}

function detailDeployment(overrides: Record<string, unknown> = {}) {
	return {
		id: deploymentId,
		strategy_fingerprint: fingerprintA,
		strategy_id: '01a0ad42-0000-0000-0000-000000000000',
		strategy_name: 'UNI trend config',
		strategy_deleted: false,
		kind: 'strategy',
		timeframe: '2h',
		product_id: 'UNI-USDC',
		mode: 'paper',
		status: 'running',
		phase: 'flat',
		cash: '10000',
		paper_starting_cash: '10000',
		last_evaluated_bar: '2026-09-21T20:00:00+00:00',
		last_signal: 'not_matched',
		mismatch_detail: null,
		pending_entry_bars: 0,
		bars_held: 0,
		lifecycle_command: 'none',
		daily_loss_latched: false,
		drawdown_latched: false,
		revision: 12,
		worker_lease_held: true,
		created_at: '2026-09-20T00:00:00+00:00',
		updated_at: '2026-09-21T20:00:00+00:00',
		position: null,
		positions: [],
		instrument_runtimes: [],
		orders: [],
		fills: [],
		...overrides
	};
}

/**
 * Mock the detail page's read surfaces.
 *
 * Route library GETs with pathname predicates, not `**` globs that also match
 * `?limit=`/`?cursor=` queries of other endpoints.
 */
async function mockDetailRoutes(
	page: import('@playwright/test').Page,
	overrides: {
		deployment?: Record<string, unknown>;
		performance?: Record<string, unknown> | null;
		performanceStatus?: number;
		orders?: { orders: unknown[]; next_cursor: string | null };
		fills?: { fills: unknown[]; next_cursor: string | null };
		strategySource?: { status: number; body: unknown };
		/** Current record of the owning strategy (default: current rules = fingerprintA). */
		record?: unknown;
		inventory?: unknown[];
		/** Decision journal answer (default: an empty page with storage available). */
		decisions?: (route: Route) => Promise<void> | void;
	} = {}
) {
	await page.route(`**/api/v1/strategies/${strategyDraft.strategy_id}`, (route) =>
		route.fulfill({ json: overrides.record ?? strategyRecord() })
	);
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${deploymentId}/decisions`,
		(route) =>
			overrides.decisions
				? overrides.decisions(route)
				: route.fulfill({ json: { deployment_id: deploymentId, ...decisionPageBody([]) } })
	);
	// No trade reasons unless a test registers its own (later routes win).
	await page.route(
		(url) => url.pathname === '/api/v1/memory/trade-reasons',
		(route) => route.fulfill({ json: { trade_reasons: [] } })
	);
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${deploymentId}/twin`,
		(route) => route.fulfill({ json: { deployment_id: deploymentId, twin: null } })
	);
	await page.route(`**/api/v1/deployments/${deploymentId}`, (route) =>
		route.fulfill({ json: overrides.deployment ?? detailDeployment() })
	);
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) =>
			route.fulfill({
				json: {
					deployments: overrides.inventory ?? [],
					limit: 200,
					offset: 0,
					returned: (overrides.inventory ?? []).length
				}
			})
	);
	await page.route(
		(url) => url.pathname === '/api/v1/operator/performance',
		(route) => {
			if (overrides.performance === null) {
				return route.fulfill({
					status: overrides.performanceStatus ?? 503,
					json: { detail: 'Performance storage is unavailable.' }
				});
			}
			return route.fulfill({ json: { ...performanceReport, ...overrides.performance } });
		}
	);
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${deploymentId}/orders`,
		(route) => {
			const url = new URL(route.request().url());
			const page_ = url.searchParams.get('page') ?? '';
			void page_;
			const body = overrides.orders ?? { orders: [], next_cursor: null };
			return route.fulfill({ json: { ...body, limit: 50, returned: body.orders.length } });
		}
	);
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${deploymentId}/fills`,
		(route) => {
			const body = overrides.fills ?? { fills: [], next_cursor: null };
			return route.fulfill({ json: { ...body, limit: 50, returned: body.fills.length } });
		}
	);
	await page.route('**/api/v1/strategies/snapshots/*', (route) => {
		const requested = decodeURIComponent(route.request().url().split('/snapshots/')[1] ?? '');
		const source = overrides.strategySource ?? {
			status: 200,
			body: snapshotBody(requested === fingerprintA ? strategyDraft : earlierDraft, requested)
		};
		return route.fulfill({ status: source.status, json: source.body });
	});
}

test.describe('deployment detail', () => {
	test('shows exact-version identity, config summary, eligibility, and evaluation', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				ledger: {
					trade_count: 2,
					total_net_pnl: '18.4',
					total_return_fraction: '0.0018',
					mark_complete: true,
					marked_exposure: null
				}
			})
		});
		await page.goto(`/deployments/${deploymentId}`);

		// Header: strategy name, mode chip, rules pill linking the strategy workspace.
		await expect(page.getByRole('heading', { level: 1, name: 'UNI trend config' })).toBeVisible();
		await expect(page.getByTestId('breadcrumb')).toHaveText(/Portfolio\s*\/\s*Bot/);
		await expect(page.getByRole('link', { name: '← Portfolio' })).toHaveAttribute(
			'href',
			'/deployments'
		);
		await expect(page.getByTestId('mode-chip')).toHaveText('Paper');
		const pill = page.getByTestId('version-pill');
		await expect(pill).toHaveText('Current rules →');
		await expect(pill).toHaveAttribute('href', `/strategies/${strategyDraft.strategy_id}/run`);
		await expect(page.getByTestId('earlier-edit-notice')).toHaveCount(0);
		await expect(page.getByTestId('bot-lede')).toContainText('UNI / USDC · 2h · running');
		await expect(page.getByTestId('bot-lede')).toContainText('worker lease held');
		await expect(page.getByTestId('deployment-fingerprint')).toHaveText(fingerprintA);
		await expect(page.getByTestId('deployment-status')).toHaveText('running');
		await expect(page.getByTestId('deployment-eligibility')).toHaveText('Eligible');
		// Four KPI cards from existing data only.
		await expect(page.getByTestId('kpi-capital')).toContainText('No capital accounting');
		await expect(page.getByTestId('kpi-pnl')).toContainText('+18.4 USDC');
		await expect(page.getByTestId('kpi-position')).toContainText('Flat');
		await expect(page.getByTestId('kpi-latest-bar')).toContainText('No trade');
		await expect(page.getByText(/signal: not_matched/)).toBeVisible();
		// Paper is not live exposure: no live strip or frame.
		await expect(page.getByTestId('live-strip')).toHaveCount(0);
		await expect(page.locator('[data-live-frame="true"]')).toHaveCount(0);
		// The immutable rule/config from the source API sits behind a disclosure.
		await page.getByTestId('config-disclosure').locator('summary').click();
		const config = page.getByTestId('strategy-config-summary');
		await expect(config).toBeVisible();
		await expect(config).toContainText('UNI trend config: when ema_fast crosses above ema_slow');
		await expect(config).toContainText('ema_fast crosses above ema_slow');
		await expect(config.getByText(/atr_multiple/)).toBeVisible();
	});

	test('shows an explicit unavailable state when the snapshot cannot load the config', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			deployment: detailDeployment({ strategy_name: null }),
			strategySource: {
				status: 404,
				body: { detail: { code: 'strategy_snapshot_not_found', message: 'Not found.' } }
			}
		});
		await page.goto(`/deployments/${deploymentId}`);
		// Without the snapshot or a captured name the heading falls back to the market.
		await expect(page.getByRole('heading', { level: 1, name: 'UNI / USDC' })).toBeVisible();
		await page.getByTestId('config-disclosure').locator('summary').click();
		const unavailable = page.getByTestId('strategy-config-unavailable');
		await expect(unavailable).toBeVisible();
		await expect(unavailable).toContainText('Rules snapshot unavailable');
		await expect(unavailable).toContainText("the strategy's current edit was not substituted");
		// The fingerprint stays as the identity; nothing else was selected.
		await expect(page.getByTestId('deployment-fingerprint')).toHaveText(fingerprintA);
	});

	test('performance comes from the operator report with currency and drawdown caveats', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			performance: {
				payload: {
					...performanceReport.payload,
					maximum_drawdown_fraction: '0.03'
				}
			}
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('deployment-performance')).toContainText('18.4 USDC');
		await expect(page.getByTestId('deployment-performance')).toContainText('2 closed trades');
		await expect(
			page.getByTestId('performance-drawdown-caveat'),
			'runtime drawdown names the fill-event mark caveat'
		).toBeVisible();
	});

	test('an unknown currency stays unknown and an incomplete mark reads as not final', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			performance: {
				partial_result_warnings: [
					'Quote currency is unknown: the published strategy could not be loaded.'
				],
				payload: {
					...performanceReport.payload,
					currency: null,
					mark_complete: false
				}
			}
		});
		await page.goto(`/deployments/${deploymentId}`);
		const performance = page.getByTestId('deployment-performance');
		await expect(performance).toContainText('unknown quote');
		await expect(performance).toContainText('open inventory without a mark — not final');
		await expect(
			page.getByTestId('deployment-performance'),
			'never relabels an unknown currency as USDC'
		).not.toContainText('USDC');
	});

	test('an unavailable operator performance report falls back to the ledger honestly', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			performance: null,
			performanceStatus: 503,
			deployment: detailDeployment({
				ledger: {
					trade_count: 1,
					total_net_pnl: '2.5',
					total_return_fraction: '0.00025',
					mark_complete: true,
					marked_exposure: null
				}
			})
		});
		await page.goto(`/deployments/${deploymentId}`);
		const error = page.getByTestId('performance-error');
		await expect(error).toBeVisible();
		await expect(error).toContainText('Ledger summary:');
	});

	test('orders and fills are cursor-paginated with distinct empty and error states', async ({
		page
	}) => {
		const orders = Array.from({ length: 53 }, (_, index) => ({
			id: `o-${index}`,
			client_order_id: `c-${index}`,
			venue_order_id: null,
			product_id: 'UNI-USDC',
			side: 'buy',
			kind: 'post_only_limit',
			quantity: '1',
			price: '10',
			filled_quantity: '0',
			status: 'filled',
			reject_reason: null,
			created_at: '2026-09-21T00:00:00+00:00',
			updated_at: '2026-09-21T00:00:00+00:00'
		}));
		await page.route(`**/api/v1/deployments/${deploymentId}`, (route) =>
			route.fulfill({ json: detailDeployment() })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/deployments',
			(route) => route.fulfill({ json: { deployments: [], limit: 200, offset: 0, returned: 0 } })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/operator/performance',
			(route) => route.fulfill({ json: performanceReport })
		);
		await page.route('**/api/v1/strategies/snapshots/*', (route) =>
			route.fulfill({ json: snapshotBody() })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/orders`,
			async (route) => {
				const url = new URL(route.request().url());
				if (url.searchParams.has('cursor')) {
					return route.fulfill({
						json: { orders: orders.slice(50), limit: 50, returned: 3, next_cursor: null }
					});
				}
				return route.fulfill({
					json: { orders: orders.slice(0, 50), limit: 50, returned: 50, next_cursor: 'cursor-1' }
				});
			}
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/fills`,
			(route) => route.fulfill({ json: { fills: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.goto(`/deployments/${deploymentId}`);

		// Orders & fills share one card; Orders is selected first.
		const ledgerSwitch = page.getByTestId('ledger-switch');
		await expect(ledgerSwitch.getByRole('button', { name: 'Orders' })).toHaveAttribute(
			'aria-pressed',
			'true'
		);
		await expect(page.getByTestId('orders-pager').getByText('50 orders')).toBeVisible();
		// Fills shows its distinct empty state, then Orders keeps its own paging.
		await ledgerSwitch.getByRole('button', { name: 'Fills' }).click();
		await expect(page.getByTestId('fills-empty')).toHaveText(
			'No fills recorded for this deployment.'
		);
		await expect(page.getByTestId('orders-pager')).toHaveCount(0);
		await ledgerSwitch.getByRole('button', { name: 'Orders' }).click();
		await page.getByTestId('orders-pager').getByRole('button', { name: 'Next' }).click();
		await expect(page.getByTestId('orders-pager').getByText('3 orders')).toBeVisible();
		await expect(
			page.getByTestId('orders-pager').getByRole('button', { name: 'Next' })
		).toBeDisabled();
		await page.getByTestId('orders-pager').getByRole('button', { name: 'Previous' }).click();
		await expect(page.getByTestId('orders-pager').getByText('50 orders')).toBeVisible();
	});

	test('an empty ledger is not an error: empty and failed states are distinct', async ({
		page
	}) => {
		let ordersFail = true;
		await page.route(`**/api/v1/deployments/${deploymentId}`, (route) =>
			route.fulfill({ json: detailDeployment() })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/deployments',
			(route) => route.fulfill({ json: { deployments: [], limit: 200, offset: 0, returned: 0 } })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/operator/performance',
			(route) => route.fulfill({ json: performanceReport })
		);
		await page.route('**/api/v1/strategies/snapshots/*', (route) =>
			route.fulfill({ json: snapshotBody() })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/orders`,
			(route) => {
				if (ordersFail) {
					return route.fulfill({ status: 503, json: { detail: 'Store unavailable' } });
				}
				return route.fulfill({ json: { orders: [], limit: 50, returned: 0, next_cursor: null } });
			}
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/fills`,
			(route) => route.fulfill({ json: { fills: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.goto(`/deployments/${deploymentId}`);
		const error = page.getByTestId('orders-error');
		await expect(error).toBeVisible();
		await expect(error).toContainText('Store unavailable');
		await page.getByTestId('ledger-switch').getByRole('button', { name: 'Fills' }).click();
		await expect(page.getByTestId('fills-empty')).toBeVisible();
		await page.getByTestId('ledger-switch').getByRole('button', { name: 'Orders' }).click();

		// Retry succeeds into the true-empty state, distinct from the failure.
		ordersFail = false;
		await error.getByRole('button', { name: 'Retry' }).click();
		await expect(page.getByTestId('orders-empty')).toHaveText(
			'No orders recorded for this deployment.'
		);
	});

	test('evidence links point at the strategy and never claim completeness', async ({ page }) => {
		await mockDetailRoutes(page);
		await page.goto(`/deployments/${deploymentId}`);
		const links = page.getByTestId('evidence-link');
		await expect(links).toHaveCount(2);
		await expect(links.first()).toBeVisible();
		await expect(page.getByTestId('evidence-link').first()).toHaveAttribute(
			'href',
			`/strategies/${strategyDraft.strategy_id}/test`
		);
		await expect(page.getByTestId('evidence-link').nth(1)).toHaveAttribute(
			'href',
			`/strategies/${strategyDraft.strategy_id}/why`
		);
		await expect(page.getByText(/not a comprehensive record/)).toBeVisible();
	});

	test('renders the deployed positions with protection status', async ({ page }) => {
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				phase: 'open',
				positions: [
					{
						product_id: 'UNI-USDC',
						quantity: '5',
						entry_price: '10',
						stop_price: '9',
						target_price: '12',
						entered_bar: '2026-09-21T20:00:00+00:00',
						side: 'long',
						protection_status: 'protected'
					}
				]
			})
		});
		await page.goto(`/deployments/${deploymentId}`);
		const table = page.getByRole('table', { name: 'Open positions with protection status' });
		await expect(table.getByRole('cell', { name: 'protected' })).toBeVisible();
		await expect(table.getByRole('cell', { name: '5', exact: true })).toBeVisible();
	});

	test('each book shows its last-bar unrealized PnL and time held (ADR 0098)', async ({ page }) => {
		await page.clock.setFixedTime(new Date('2026-09-22T01:30:00Z'));
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				phase: 'pending_exit',
				position_state: 'open_protected',
				positions: [
					{
						product_id: 'UNI-USDC',
						quantity: '5',
						entry_price: '10',
						stop_price: '9',
						target_price: '12',
						entered_bar: '2026-09-21T20:00:00+00:00',
						side: 'long',
						protection_status: 'covered',
						position_state: 'open_protected',
						exit_in_flight: false,
						mark_price: '10.5',
						marked_at: '2026-09-22T01:00:00+00:00',
						unrealized_pnl: '2.5',
						entry_fees: '3',
						unrealized_pnl_net: '-0.5'
					}
				]
			})
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('position-upnl')).toHaveText('-0.50 USDC (net)');
		await expect(page.getByTestId('position-upnl')).toHaveAttribute(
			'title',
			'Marked at 10.5 at the 01:00 UTC bar close; net after recorded entry fees; future exit fees excluded.'
		);
		await expect(page.getByTestId('position-held')).toHaveText('5h 30m');
		await expect(page.getByTestId('kpi-position-pnl')).toHaveText(
			'uPnL -0.50 USDC (net) · held 5h 30m'
		);
	});

	test('protection evidence is not a green venue badge for a worker stop or a take-profit', async ({
		page
	}) => {
		const book = {
			product_id: 'ETH-USDC',
			quantity: '0.5',
			entry_price: '3000',
			stop_price: '2800',
			target_price: '3400',
			entered_bar: '2026-09-21T20:00:00+00:00',
			side: 'long',
			protection_status: 'covered',
			position_state: 'open_protected',
			exit_in_flight: false,
			protection: {
				required_quantity: '0.5',
				covered_quantity: '0.5',
				uncovered_quantity: '0',
				stop_side: 'sell',
				stop_side_valid: true,
				stop_geometry_valid: true,
				mechanism: 'synthetic',
				venue_resting: false,
				worker_dependent: true,
				observed_at: null,
				verified_at: null,
				observation_source: 'synthetic_worker',
				freshness: 'unknown',
				evaluated_at: '2026-09-21T20:05:00+00:00',
				freshness_max_age_seconds: 120,
				geometry_basis: 'working_target',
				reasons: ['synthetic_worker_dependent']
			}
		};
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				mode: 'paper',
				phase: 'pending_exit',
				position_state: 'open_protected',
				positions: [book]
			})
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('position-state')).toHaveText(/Worker stop/);
		await expect(page.getByTestId('protection-evidence')).toContainText('not venue-resting');
		await expect(page.getByTestId('position-state')).not.toHaveText('Venue TP/SL');
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				mode: 'live',
				phase: 'open',
				position_state: 'open_unprotected',
				positions: [
					{
						...book,
						protection_status: 'unprotected',
						position_state: 'open_unprotected',
						protection: {
							...book.protection,
							covered_quantity: '0',
							uncovered_quantity: '0.5',
							mechanism: 'none',
							worker_dependent: false,
							stop_side_valid: false,
							stop_geometry_valid: false,
							reasons: ['take_profit_only', 'no_resting_stop']
						}
					}
				]
			})
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('position-state')).toHaveText(/Unprotected/);
		await expect(page.getByTestId('position-state')).not.toHaveClass(/ok/);
	});

	test('matching persisted venue geometry stays covered but is not fresh venue verification', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				mode: 'live',
				phase: 'pending_exit',
				position_state: 'open_protected',
				positions: [
					{
						product_id: 'ADA-USDC',
						quantity: '20',
						entry_price: '0.40',
						stop_price: '0.36',
						target_price: '0.48',
						entered_bar: '2026-09-21T20:00:00+00:00',
						side: 'long',
						protection_status: 'covered',
						position_state: 'open_protected',
						protection: {
							required_quantity: '20',
							covered_quantity: '20',
							uncovered_quantity: '0',
							stop_side: 'sell',
							stop_side_valid: true,
							stop_geometry_valid: true,
							mechanism: 'venue',
							venue_resting: true,
							worker_dependent: false,
							observed_at: '2026-09-21T20:05:00+00:00',
							verified_at: null,
							observation_source: 'persisted_order',
							freshness: 'recent_local',
							evaluated_at: '2026-09-21T20:05:00+00:00',
							freshness_max_age_seconds: 120,
							geometry_basis: 'working_target',
							reasons: ['venue_stop_resting', 'local_observation_only']
						}
					}
				]
			})
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('position-state')).toHaveText(/Unverified/);
		await expect(page.getByTestId('position-state')).not.toHaveClass(/ok/);
		await expect(page.getByTestId('protection-evidence')).toContainText('20 of 20');
		await expect(page.getByTestId('protection-evidence')).toContainText(
			'venue verification unknown'
		);
	});

	test('a resting TP/SL reads as open and protected, not exiting (ADR 0097)', async ({ page }) => {
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				phase: 'pending_exit',
				position_state: 'open_protected',
				exit_in_flight: false,
				positions: [
					{
						product_id: 'UNI-USDC',
						quantity: '5',
						entry_price: '10',
						stop_price: '9',
						target_price: '12',
						entered_bar: '2026-09-21T20:00:00+00:00',
						side: 'long',
						protection_status: 'covered',
						position_state: 'open_protected',
						exit_in_flight: false
					}
				]
			})
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('position-state')).toHaveText('Protected · unverified');
		await expect(page.getByTestId('kpi-position')).toContainText('Protected · unverified');
		await expect(page.getByTestId('kpi-position')).not.toContainText('Exiting');
	});

	test('lifecycle controls disappear when the contract is incomplete', async ({ page }) => {
		const partial = detailDeployment();
		delete (partial as Record<string, unknown>).lifecycle_command;
		await mockDetailRoutes(page, { deployment: partial });
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByText(/Lifecycle controls are unavailable/)).toBeVisible();
		await expect(page.getByRole('button', { name: 'Pause entries…' })).toHaveCount(0);
	});

	test('managed stop posts /stop without flatten; the flatten choice posts ?flatten=true', async ({
		page
	}) => {
		const stopUrls: string[] = [];
		await page.route(`**/api/v1/deployments/${deploymentId}`, (route) =>
			route.fulfill({ json: detailDeployment() })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/deployments',
			(route) => route.fulfill({ json: { deployments: [], limit: 200, offset: 0, returned: 0 } })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/operator/performance',
			(route) => route.fulfill({ json: performanceReport })
		);
		await page.route('**/api/v1/strategies/snapshots/*', (route) =>
			route.fulfill({ json: snapshotBody() })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/orders`,
			(route) => route.fulfill({ json: { orders: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/fills`,
			(route) => route.fulfill({ json: { fills: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(`**/api/v1/deployments/${deploymentId}/stop`, async (route) => {
			stopUrls.push(route.request().url());
			await route.fulfill({
				json: detailDeployment({ status: 'stopped', lifecycle_command: 'managed_shutdown' })
			});
		});
		await page.goto(`/deployments/${deploymentId}`);
		await page.getByRole('button', { name: 'Stop…' }).click();

		const dialog = page.getByRole('dialog', { name: 'Stop paper deployment?' });
		await expect(dialog).toBeVisible();
		await expect(dialog.getByText(/cannot be resumed after it is stopped/)).toBeVisible();
		await expect(dialog.getByText(/Managed stop — keep protection/)).toBeVisible();
		await dialog.getByRole('button', { name: 'Stop with protection' }).click();

		await expect.poll(() => stopUrls.map((url) => new URL(url).search)).toEqual(['']);
		await expect(page.getByText(/Managed stop accepted/)).toBeVisible();
		await expect(page.getByRole('button', { name: 'Flatten remaining exposure…' })).toHaveCount(0);
	});

	test('a stopped managed deployment with residual exposure can request flatten', async ({
		page
	}) => {
		const position = {
			product_id: 'UNI-USDC',
			quantity: '5',
			entry_price: '10',
			stop_price: '9',
			target_price: '12',
			entered_bar: '2026-09-21T20:00:00+00:00',
			side: 'long',
			protection_status: 'protected'
		};
		const flattenUrls: string[] = [];
		await page.route(`**/api/v1/deployments/${deploymentId}`, (route) =>
			route.fulfill({
				json: detailDeployment({
					status: 'stopped',
					lifecycle_command: 'managed_shutdown',
					positions: [position]
				})
			})
		);
		await page.route(
			(url) => url.pathname === '/api/v1/deployments',
			(route) => route.fulfill({ json: { deployments: [], limit: 200, offset: 0, returned: 0 } })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/operator/performance',
			(route) => route.fulfill({ json: performanceReport })
		);
		await page.route('**/api/v1/strategies/snapshots/*', (route) =>
			route.fulfill({ json: snapshotBody() })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/orders`,
			(route) => route.fulfill({ json: { orders: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/fills`,
			(route) => route.fulfill({ json: { fills: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(`**/api/v1/deployments/${deploymentId}/stop?flatten=true`, async (route) => {
			flattenUrls.push(route.request().url());
			await route.fulfill({
				json: detailDeployment({ status: 'stopped', lifecycle_command: 'flatten' })
			});
		});
		await page.goto(`/deployments/${deploymentId}`);
		await page.getByRole('button', { name: 'Flatten remaining exposure…' }).click();
		const dialog = page.getByRole('dialog', { name: 'Flatten stopped deployment?' });
		await expect(dialog.getByText(/Exit price and fees are not guaranteed/)).toBeVisible();
		await dialog.getByRole('button', { name: 'Flatten remaining exposure' }).click();
		await expect.poll(() => flattenUrls).toHaveLength(1);
		await expect(new URL(flattenUrls[0]!).search).toBe('?flatten=true');
	});

	test('a stopped deployment never exposes resume', async ({ page }) => {
		await mockDetailRoutes(page, { deployment: detailDeployment({ status: 'stopped' }) });
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByRole('button', { name: /Resume/ })).toHaveCount(0);
		await expect(page.getByTestId('deployment-eligibility')).toHaveText('Stopped — no entries');
	});

	test('breaker reset requires its own confirmation before any POST', async ({ page }) => {
		let resetCalls = 0;
		let latched = true;
		await page.route(`**/api/v1/deployments/${deploymentId}`, (route) =>
			route.fulfill({
				json: detailDeployment({ status: 'paused', daily_loss_latched: latched })
			})
		);
		await page.route(
			(url) => url.pathname === '/api/v1/deployments',
			(route) => route.fulfill({ json: { deployments: [], limit: 200, offset: 0, returned: 0 } })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/operator/performance',
			(route) => route.fulfill({ json: performanceReport })
		);
		await page.route('**/api/v1/strategies/snapshots/*', (route) =>
			route.fulfill({ json: snapshotBody() })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/orders`,
			(route) => route.fulfill({ json: { orders: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/fills`,
			(route) => route.fulfill({ json: { fills: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(
			`**/api/v1/deployments/${deploymentId}/reset-breaker-latches`,
			async (route) => {
				resetCalls += 1;
				latched = false;
				await route.fulfill({
					json: detailDeployment({ status: 'paused', daily_loss_latched: false })
				});
			}
		);
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('deployment-eligibility')).toHaveText(
			'Blocked by daily-loss breaker'
		);

		// Opening the dialog sends nothing; cancel sends nothing.
		await page.getByTestId('reset-breakers-button').click();
		const dialog = page.getByRole('dialog', { name: 'Reset breaker latches?' });
		await expect(dialog).toBeVisible();
		await expect(dialog.getByText(/Daily-loss breaker latch is currently blocking/)).toBeVisible();
		await expect(dialog.getByText(/does not change the lifecycle state/)).toBeVisible();
		await expect(resetCalls).toBe(0);
		await dialog.getByRole('button', { name: 'Cancel' }).click();
		await expect(resetCalls).toBe(0);

		// Confirming is the only path to the POST.
		await page.getByTestId('reset-breakers-button').click();
		await page
			.getByRole('dialog', { name: 'Reset breaker latches?' })
			.getByRole('button', { name: 'Reset latches' })
			.click();
		await expect.poll(() => resetCalls).toBe(1);
		await expect(page.getByTestId('deployment-eligibility')).toHaveText('Blocked by pause');
	});

	test('mutation controls stay disabled until a successful refresh after an unknown outcome', async ({
		page
	}) => {
		let failDetail = false;
		let latched = true;
		await page.route(`**/api/v1/deployments/${deploymentId}`, (route) => {
			if (failDetail) {
				return route.abort('failed');
			}
			return route.fulfill({
				json: detailDeployment({ status: 'paused', daily_loss_latched: latched })
			});
		});
		await page.route(
			(url) => url.pathname === '/api/v1/deployments',
			(route) => route.fulfill({ json: { deployments: [], limit: 200, offset: 0, returned: 0 } })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/operator/performance',
			(route) => route.fulfill({ json: performanceReport })
		);
		await page.route('**/api/v1/strategies/snapshots/*', (route) =>
			route.fulfill({ json: snapshotBody() })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/orders`,
			(route) => route.fulfill({ json: { orders: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/fills`,
			(route) => route.fulfill({ json: { fills: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(`**/api/v1/deployments/${deploymentId}/reset-breaker-latches`, (route) => {
			latched = false;
			return route.fulfill({
				json: detailDeployment({ status: 'paused', daily_loss_latched: false })
			});
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('deployment-eligibility')).toHaveText(
			'Blocked by daily-loss breaker'
		);

		// The reset succeeds, then the follow-up refresh fails: outcome is stale.
		failDetail = true;
		await page.getByTestId('reset-breakers-button').click();
		await page
			.getByRole('dialog', { name: 'Reset breaker latches?' })
			.getByRole('button', { name: 'Reset latches' })
			.click();
		const stale = page.getByTestId('stale-banner');
		await expect(stale).toBeVisible();

		// Controls stay unavailable while the snapshot is stale: the whole control
		// block is replaced by the disabled note, so no mutation can be sent.
		await expect(page.getByTestId('controls-blocked-note')).toContainText(
			'disabled until this deployment is refreshed successfully'
		);
		await expect(page.getByRole('button', { name: 'Resume entries…' })).toHaveCount(0);
		await expect(page.getByTestId('reset-breakers-button')).toHaveCount(0);

		// A successful refresh re-enables them (the latch itself is now cleared,
		// so the breaker row is gone entirely — the point is controls unblock).
		failDetail = false;
		await stale.getByRole('button', { name: 'Retry refresh' }).click();
		await expect(page.getByTestId('stale-banner')).toHaveCount(0);
		await expect(page.getByTestId('controls-blocked-note')).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Resume entries…' })).toBeEnabled();
	});

	test('an unknown outcome refresh clears the banner only after the load succeeds', async ({
		page
	}) => {
		await page.route(`**/api/v1/deployments/${deploymentId}`, (route) =>
			route.fulfill({ json: detailDeployment({ status: 'paused' }) })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/deployments',
			(route) => route.fulfill({ json: { deployments: [], limit: 200, offset: 0, returned: 0 } })
		);
		await page.route(
			(url) => url.pathname === '/api/v1/operator/performance',
			(route) => route.fulfill({ json: performanceReport })
		);
		await page.route('**/api/v1/strategies/snapshots/*', (route) =>
			route.fulfill({ json: snapshotBody() })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/orders`,
			(route) => route.fulfill({ json: { orders: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${deploymentId}/fills`,
			(route) => route.fulfill({ json: { fills: [], limit: 50, returned: 0, next_cursor: null } })
		);
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('deployment-status')).toHaveText('paused');

		// A resume POST whose transport dies mid-flight: the outcome is ambiguous.
		await page.route('**/api/v1/deployments/*/resume', (route) => route.abort('failed'));
		await page.getByRole('button', { name: 'Resume entries…' }).click();
		await page
			.getByRole('dialog', { name: 'Resume paper deployment?' })
			.getByRole('button', { name: 'Resume entries' })
			.click();
		const banner = page.getByTestId('outcome-unknown-banner');
		await expect(banner).toBeVisible();
		await expect(page.getByTestId('controls-blocked-note')).toBeVisible();

		// Refresh now: the load succeeds, so controls return.
		await banner.getByRole('button', { name: 'Refresh now' }).click();
		await expect(page.getByTestId('outcome-unknown-banner')).toHaveCount(0);
		await expect(page.getByTestId('controls-blocked-note')).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Resume entries…' })).toBeEnabled();
	});

	test('a bot on an earlier edit says so and offers a guided update', async ({ page }) => {
		const replacementId = '01a0ad72-0000-0000-0000-0000000000ff';
		const calls: string[] = [];
		let created: Record<string, unknown> | null = null;
		await mockDetailRoutes(page, { record: strategyRecord(fingerprintB, earlierDraft) });
		await page.route(`**/api/v1/deployments/${deploymentId}/stop**`, async (route) => {
			calls.push(`stop${new URL(route.request().url()).search}`);
			await route.fulfill({ json: detailDeployment({ status: 'stopped' }) });
		});
		await page.route(
			(url) => url.pathname === '/api/v1/deployments',
			async (route) => {
				if (route.request().method() !== 'POST') return route.fallback();
				calls.push('start');
				created = route.request().postDataJSON() as Record<string, unknown>;
				await route.fulfill({
					status: 201,
					json: detailDeployment({ id: replacementId, strategy_fingerprint: fingerprintB })
				});
			}
		);
		await page.route(`**/api/v1/deployments/${replacementId}`, (route) =>
			route.fulfill({
				json: detailDeployment({ id: replacementId, strategy_fingerprint: fingerprintB })
			})
		);
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('version-pill')).toHaveText('Earlier edit →');
		const notice = page.getByTestId('earlier-edit-notice');
		await expect(notice).toContainText('This bot is running an earlier edit');
		await expect(notice).toContainText('Editing the strategy never changes a running bot');
		await notice.getByRole('button', { name: 'What changed' }).click();
		const diff = notice.getByTestId('snapshot-diff');
		await expect(diff).toContainText('20');
		await expect(diff).toContainText('12');
		await notice.getByRole('button', { name: 'Update bot…' }).click();
		const dialog = page.getByRole('dialog', { name: 'Update bot to the current rules?' });
		await expect(dialog).toContainText('two separate actions');
		await dialog.getByRole('button', { name: 'Stop and start paper bot' }).click();
		await expect.poll(() => calls).toEqual(['stop', 'start']);
		expect(created).toMatchObject({
			strategy_id: strategyDraft.strategy_id,
			mode: 'paper',
			paper_starting_cash: '10000'
		});
		await expect(page).toHaveURL(new RegExp(`/deployments/${replacementId}$`));
	});

	test('a kept live bot of a deleted strategy is labelled and keeps its history', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				mode: 'live',
				status: 'stopped',
				strategy_id: null,
				strategy_deleted: true,
				strategy_name: 'Old breakout',
				paper_starting_cash: null
			}),
			strategySource: {
				status: 200,
				body: { ...snapshotBody({ ...strategyDraft, name: 'Old breakout' }), strategy_id: null }
			}
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(
			page.getByRole('heading', { level: 1, name: 'Old breakout (deleted strategy)' })
		).toBeVisible();
		await expect(page.getByTestId('deleted-strategy-note')).toContainText(
			'Its strategy was deleted'
		);
		await expect(page.getByTestId('version-pill')).toHaveCount(0);
		await expect(page.getByTestId('earlier-edit-notice')).toHaveCount(0);
		await expect(page.getByTestId('evidence-link')).toHaveAttribute(
			'href',
			`/backtests?strategy_fingerprint=${encodeURIComponent(fingerprintA)}`
		);
	});

	test('other rules snapshots of the same strategy are separated, not mixed in', async ({
		page
	}) => {
		const otherVersion = detailDeployment({
			id: '01a0ad72-0000-0000-0000-000000000001',
			strategy_fingerprint: fingerprintB
		});
		await mockDetailRoutes(page, {
			inventory: [detailDeployment(), otherVersion]
		});
		await page.goto(`/deployments/${deploymentId}`);
		const section = page.getByRole('region', { name: 'Other deployments for this strategy' });
		await expect(
			section.getByRole('link', { name: new RegExp(fingerprintB.slice(0, 18)) })
		).toBeVisible();
	});

	test('mobile 390px keeps the detail usable without horizontal overflow', async ({ page }) => {
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				positions: [
					{
						product_id: 'UNI-USDC',
						quantity: '5',
						entry_price: '10',
						stop_price: '9',
						target_price: '12',
						entered_bar: '2026-09-21T20:00:00+00:00',
						side: 'long',
						protection_status: 'protected'
					}
				]
			})
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(`/deployments/${deploymentId}`);
		const overflow = await page.evaluate(
			() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
		);
		expect(overflow).toBe(false);
	});

	test('a live bot turns on the live chrome and live resume needs the real-orders checkbox', async ({
		page
	}) => {
		const live = detailDeployment({
			mode: 'live',
			status: 'paused',
			lifecycle_command: 'stop_new_entries',
			product_id: 'ETH-USDC',
			capital: {
				allocated_capital: '100.00',
				venue_available_quote: '1240.18',
				performance_equity: '100.42'
			}
		});
		await mockDetailRoutes(page, {
			deployment: live,
			performance: {
				payload: { ...performanceReport.payload, mode: 'live', total_net_pnl: '0.42' }
			}
		});
		let resumeBody: unknown = 'not called';
		await page.route(`**/api/v1/deployments/${deploymentId}/resume`, async (route) => {
			resumeBody = route.request().postDataJSON();
			await route.fulfill({ json: { ...live, status: 'running', lifecycle_command: 'none' } });
		});
		await page.goto(`/deployments/${deploymentId}`);

		// Live exposure on screen: amber strip with an icon and a LIVE label, plus the inset frame.
		const strip = page.getByTestId('live-strip');
		await expect(strip).toHaveText(
			'LIVE: this bot places real Coinbase orders · ETH / USDC · allocated 100.00 USDC'
		);
		await expect(strip.locator('svg')).toHaveCount(1);
		await expect(page.locator('[data-live-frame="true"]')).toHaveCount(1);
		await expect(page.getByTestId('mode-chip')).toHaveText('LIVE');
		await expect(page.getByTestId('kpi-capital')).toContainText('100.00 USDC');
		await expect(page.getByTestId('kpi-capital')).toContainText('venue 1240.18 USDC');
		await expect(page.getByTestId('kpi-pnl')).toContainText('+0.42 USDC');

		// Resume opens the live dialog; confirm stays disabled until the checkbox is ticked.
		await page.getByRole('button', { name: 'Resume entries…' }).click();
		let dialog = page.getByRole('dialog', { name: 'Resume live deployment?' });
		await expect(dialog).toContainText('re-arms REAL Coinbase spot order submission');
		await expect(dialog.getByText('LIVE', { exact: true })).toBeVisible();
		const confirm = dialog.getByRole('button', { name: 'Resume entries' });
		await expect(confirm).toBeDisabled();
		await dialog.getByRole('button', { name: 'Cancel' }).click();
		await expect(dialog).toHaveCount(0);
		expect(resumeBody).toBe('not called');

		// Reopening starts unacknowledged; ticking the box is the only path to the POST.
		await page.getByRole('button', { name: 'Resume entries…' }).click();
		dialog = page.getByRole('dialog', { name: 'Resume live deployment?' });
		await expect(dialog.getByRole('button', { name: 'Resume entries' })).toBeDisabled();
		await dialog
			.getByLabel('I understand this places real orders on Coinbase with real money.')
			.check();
		await dialog.getByRole('button', { name: 'Resume entries' }).click();
		await expect.poll(() => resumeBody).toEqual({ i_understand_live: true });

		// Leaving the live bot clears the live chrome.
		await page.getByRole('link', { name: '← Portfolio' }).click();
		await expect(page.getByRole('heading', { level: 1, name: 'Portfolio' })).toBeVisible();
		await expect(page.getByTestId('live-strip')).toHaveCount(0);
		await expect(page.locator('[data-live-frame="true"]')).toHaveCount(0);
	});

	test('paper resume never sends the live acknowledgement', async ({ page }) => {
		await mockDetailRoutes(page, { deployment: detailDeployment({ status: 'paused' }) });
		let resumeBody: unknown = 'not called';
		await page.route(`**/api/v1/deployments/${deploymentId}/resume`, async (route) => {
			resumeBody = route.request().postData();
			await route.fulfill({ json: detailDeployment({ status: 'running' }) });
		});
		await page.goto(`/deployments/${deploymentId}`);
		await page.getByRole('button', { name: 'Resume entries…' }).click();
		const dialog = page.getByRole('dialog', { name: 'Resume paper deployment?' });
		await expect(dialog.getByRole('checkbox')).toHaveCount(0);
		await dialog.getByRole('button', { name: 'Resume entries' }).click();
		await expect.poll(() => resumeBody).toBeNull();
	});

	test('an unknown lifecycle value keeps the bot read-only', async ({ page }) => {
		await mockDetailRoutes(page, {
			deployment: detailDeployment({ lifecycle_command: 'teleport', daily_loss_latched: true })
		});
		await page.goto(`/deployments/${deploymentId}`);
		await expect(page.getByTestId('controls-blocked-note')).toContainText(
			'did not return the current lifecycle contract'
		);
		await expect(page.getByRole('button', { name: /Pause|Resume|Stop…|Flatten/ })).toHaveCount(0);
		await expect(page.getByTestId('breaker-row')).toBeVisible();
		await expect(page.getByTestId('reset-breakers-button')).toHaveCount(0);
	});

	test('the decisions timeline lists every outcome kind newest first', async ({ page }) => {
		const searches: string[] = [];
		await mockDetailRoutes(page, {
			decisions: (route) => {
				searches.push(new URL(route.request().url()).search);
				return route.fulfill({
					json: { deployment_id: deploymentId, ...decisionPageBody(everyOutcome(deploymentId)) }
				});
			}
		});
		await page.goto(`/deployments/${deploymentId}`);

		const timeline = page.getByTestId('why-it-traded');
		await expect(timeline.getByRole('heading', { level: 2, name: 'Decisions' })).toBeVisible();
		const rows = timeline.getByTestId('decision-row');
		await expect(rows).toHaveCount(7);
		await expect(timeline.getByTestId('decision-outcome')).toHaveText([
			'Exit',
			'Holding',
			'Entry',
			'Blocked',
			'Skipped',
			'No signal',
			'Error'
		]);
		// Each row: bar close time (UTC), outcome chip, and the server's one-line reason.
		await expect(rows.first()).toContainText('09-21 20:00');
		await expect(rows.first().getByTestId('decision-summary')).toHaveText(
			'Exit: take profit filled at 7.70'
		);
		await expect(rows.nth(3).getByTestId('decision-summary')).toHaveText(
			'Entry blocked by risk: portfolio exposure limit'
		);
		await expect(rows.nth(6)).toContainText('09-21 08:00');
		// One product: rows do not repeat it.
		await expect(timeline.getByTestId('decision-product')).toHaveCount(0);
		// "All" sends no outcome parameter.
		expect(searches[0]).toBe('?limit=50');
		await expect(
			timeline.getByTestId('decision-filter').getByRole('button', { name: 'All' })
		).toHaveAttribute('aria-pressed', 'true');
		await expect(page.getByTestId('decision-retention-note')).toContainText(
			"each bot's newest 20,000 decisions, up to 180 days"
		);
		await expect(page.getByText(/not recorded yet/)).toHaveCount(0);
	});

	test('decision filters send repeated outcome parameters', async ({ page }) => {
		const requested: string[][] = [];
		await mockDetailRoutes(page, {
			decisions: (route) => {
				requested.push(requestedOutcomes(route));
				return route.fulfill({
					json: {
						deployment_id: deploymentId,
						...decisionPageBody(filterByRequest(route, everyOutcome(deploymentId)))
					}
				});
			}
		});
		await page.goto(`/deployments/${deploymentId}`);
		const timeline = page.getByTestId('why-it-traded');
		await expect(timeline.getByTestId('decision-row')).toHaveCount(7);

		const filters = timeline.getByTestId('decision-filter');
		await filters.getByRole('button', { name: 'Trades' }).click();
		await expect(filters.getByRole('button', { name: 'Trades' })).toHaveAttribute(
			'aria-pressed',
			'true'
		);
		await expect(timeline.getByTestId('decision-outcome')).toHaveText(['Exit', 'Entry']);
		await filters.getByRole('button', { name: 'Blocked' }).click();
		await expect(timeline.getByTestId('decision-outcome')).toHaveText(['Blocked']);
		await filters.getByRole('button', { name: 'No signal' }).click();
		await expect(timeline.getByTestId('decision-outcome')).toHaveText(['No signal']);
		await filters.getByRole('button', { name: 'All' }).click();
		await expect(timeline.getByTestId('decision-row')).toHaveCount(7);
		expect(requested).toEqual([[], ['entry_signal', 'exit'], ['entry_blocked'], ['no_signal'], []]);
	});

	test('an empty filter result names the filter, not a missing journal', async ({ page }) => {
		await mockDetailRoutes(page, {
			decisions: (route) =>
				route.fulfill({
					json: {
						deployment_id: deploymentId,
						...decisionPageBody(
							requestedOutcomes(route).length === 0 ? [barDecision(deploymentId)] : []
						)
					}
				})
		});
		await page.goto(`/deployments/${deploymentId}`);
		const timeline = page.getByTestId('why-it-traded');
		await expect(timeline.getByTestId('decision-row')).toHaveCount(1);
		await timeline.getByTestId('decision-filter').getByRole('button', { name: 'Blocked' }).click();
		await expect(timeline.getByTestId('decision-empty')).toHaveText(
			'No blocked entries in the journaled decision history.'
		);
	});

	test('expanding a row shows its rule chips, risk, orders, and its trade reason once', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			decisions: (route) =>
				route.fulfill({
					json: { deployment_id: deploymentId, ...decisionPageBody(everyOutcome(deploymentId)) }
				})
		});
		const reasonRequests = await mockTradeReasons(page, () => [
			tradeReason(deploymentId, EXIT_INTENT, {
				id: 'reason-tp',
				created_at: '2026-09-21T19:41:05Z',
				purpose: 'take_profit',
				side: 'sell',
				signal: {
					kind: 'take_profit',
					last_signal: null,
					candle_starts_at: '2026-09-21T18:00:00Z',
					timeframe: '2h'
				},
				notes: []
			}),
			tradeReason(deploymentId, ENTRY_INTENT),
			tradeReason(deploymentId, EARLIER_INTENT, {
				id: 'reason-old',
				created_at: '2026-09-01T10:00:05Z',
				signal: {
					kind: 'strategy_entry',
					last_signal: 'matched',
					candle_starts_at: '2026-09-01T08:00:00Z',
					timeframe: '2h'
				},
				notes: []
			})
		]);
		await page.goto(`/deployments/${deploymentId}`);
		const timeline = page.getByTestId('why-it-traded');
		await expect(timeline.getByTestId('decision-row')).toHaveCount(7);
		expect(reasonRequests).toContain(deploymentId);

		// Reasons no row links are listed once, below; the linked ones are not.
		const earlier = timeline.getByTestId('earlier-trade-reasons');
		await expect(earlier.getByRole('heading', { name: 'Earlier trade reasons' })).toBeVisible();
		await expect(earlier.getByTestId('trade-reason')).toHaveCount(1);
		await expect(earlier.getByTestId('trade-reason')).toHaveAttribute(
			'data-intent-id',
			EARLIER_INTENT
		);
		await expect(timeline.locator(`[data-intent-id="${ENTRY_INTENT}"]`)).toHaveCount(0);

		// The disclosure is a keyboard-operable button.
		const entry = timeline.locator('[data-testid="decision-row"][data-outcome="entry_signal"]');
		const toggle = entry.getByRole('button', { name: /Entry: RSI\(14\) 55\.20/ });
		await expect(toggle).toHaveAttribute('aria-expanded', 'false');
		await expect(entry.getByTestId('decision-detail')).toHaveCount(0);
		await toggle.focus();
		await page.keyboard.press('Enter');
		await expect(toggle).toHaveAttribute('aria-expanded', 'true');

		const detail = entry.getByTestId('decision-detail');
		await expect(detail.getByTestId('decision-rule-outcome')).toContainText('Entry rule matched');
		const group = detail.getByTestId('condition-group').first();
		await expect(group).toHaveAttribute('data-group', 'all');
		await expect(group).toContainText('ALL ✓');
		const chips = detail.getByTestId('condition-chip');
		await expect(chips).toHaveCount(3);
		await expect(chips.nth(0)).toContainText('RSI(14) 55.2 ≥ 50 ✓');
		await expect(chips.nth(0)).toHaveAttribute('data-result', 'true');
		await expect(chips.nth(1)).toContainText(
			'EMA(20) 7.051→7.123 crosses above EMA(50) 7.08→7.1 ✓'
		);
		await expect(detail.getByTestId('htf-chip')).toHaveText('HTF 4h filter matched ✓');
		await expect(chips.nth(2)).toContainText('Close 7.12 > EMA(200) 6.9 ✓');
		await expect(detail.getByTestId('decision-indicators')).toContainText('rsi_14');
		await expect(detail.getByTestId('decision-indicators')).toContainText('47.21');
		await expect(detail.getByTestId('decision-indicators')).toContainText('n/a');
		await expect(detail.getByTestId('decision-risk')).toHaveText('Risk allowed (ALLOWED)');
		await expect(detail.getByTestId('decision-action')).toContainText(
			'Order submitted · intent 0199aaaa'
		);
		const orderRow = detail.getByTestId('decision-order');
		await expect(orderRow).toHaveCount(1);
		await expect(orderRow).toContainText('entry');
		await expect(orderRow).toContainText('buy');
		await expect(orderRow).toContainText('7.10');
		await expect(orderRow).toContainText('filled');
		await expect(detail.getByTestId('decision-fill')).toContainText('0.0355');
		await expect(detail.getByTestId('decision-position')).toHaveText(
			'long 5 @ 7.10 · stop 6.80 · target 7.70'
		);
		// The persisted trade reason of this bar's intent, merged in, not duplicated.
		const merged = detail.getByTestId('decision-trade-reason');
		await expect(merged).toHaveCount(1);
		await expect(merged).toContainText('Entry · buy UNI-USDC');
		await expect(merged).toContainText('Risk allow (within_limits)');
		await expect(merged).toContainText('Order 0199bbbb · filled · 1 fill');
		await expect(merged).toContainText('human: Breakout confirmed on volume.');
		await expect(timeline.locator(`[data-intent-id="${ENTRY_INTENT}"]`)).toHaveCount(1);

		// The exit's take-profit reason is owned by the bar its order filled on.
		const exit = timeline.locator('[data-testid="decision-row"][data-outcome="exit"]');
		await exit.getByRole('button', { name: /Exit: take profit/ }).click();
		await expect(exit.getByTestId('decision-detail')).toContainText('Exit reason');
		await expect(exit.getByTestId('decision-detail')).toContainText('take profit');
		await expect(exit.getByTestId('decision-trade-reason')).toContainText('Take profit · sell');
		await expect(timeline.locator(`[data-intent-id="${EXIT_INTENT}"]`)).toHaveCount(1);

		// Risk denial with its code and detail.
		const blocked = timeline.locator('[data-testid="decision-row"][data-outcome="entry_blocked"]');
		await blocked.getByRole('button').first().click();
		await expect(blocked.getByTestId('decision-risk')).toHaveText(
			'Risk denied (MAX_PORTFOLIO_EXPOSURE) · Exposure would exceed 10% of equity.'
		);
		await expect(blocked.getByTestId('condition-group').first()).toContainText('ANY ✓');

		// A failed comparison and an undefined one are labelled, not just colored.
		const noSignal = timeline.locator('[data-testid="decision-row"][data-outcome="no_signal"]');
		await noSignal.getByRole('button').first().click();
		await expect(noSignal.getByTestId('condition-chip').first()).toContainText(
			'RSI(14) 47.21 ≥ 50 ✗ (not met)'
		);
		const failed = timeline.locator('[data-testid="decision-row"][data-outcome="error"]');
		await failed.getByRole('button').first().click();
		await expect(failed.getByTestId('decision-rule-outcome')).toContainText(
			'Entry rule could not be evaluated'
		);
		await expect(failed.getByTestId('condition-group').first()).toContainText('NOT ?');
		await expect(failed.getByTestId('condition-chip').first()).toContainText(
			'RSI(14) n/a ≥ 50 ? (unknown)'
		);
		const skipped = timeline.locator('[data-testid="decision-row"][data-outcome="skipped"]');
		await skipped.getByRole('button').first().click();
		await expect(skipped.getByTestId('decision-detail')).toContainText(
			'Entry rules were not evaluated on this bar (cooldown after the last trade).'
		);

		// Collapsing hides the detail again.
		await toggle.click();
		await expect(toggle).toHaveAttribute('aria-expanded', 'false');
		await expect(entry.getByTestId('decision-detail')).toHaveCount(0);
	});

	test('load more follows next_cursor and moves a reason into its row', async ({ page }) => {
		const firstPage = everyOutcome(deploymentId).slice(0, 2);
		const older = barDecision(deploymentId, {
			...at(14),
			outcome: 'entry_signal',
			reason_code: 'SIGNAL_MATCHED',
			summary: 'Entry: earlier breakout',
			action: 'intent_created',
			intent_id: EARLIER_INTENT
		});
		const searches: URLSearchParams[] = [];
		await mockDetailRoutes(page, {
			decisions: (route) => {
				const params = new URL(route.request().url()).searchParams;
				searches.push(params);
				const body: Json =
					params.get('cursor') === null
						? decisionPageBody(firstPage, { next_cursor: 'cursor-2' })
						: decisionPageBody([older]);
				return route.fulfill({ json: { deployment_id: deploymentId, ...body } });
			}
		});
		await mockTradeReasons(page, () => [
			tradeReason(deploymentId, EARLIER_INTENT, { id: 'reason-old', notes: [] })
		]);
		await page.goto(`/deployments/${deploymentId}`);
		const timeline = page.getByTestId('why-it-traded');
		await expect(timeline.getByTestId('decision-row')).toHaveCount(2);
		await expect(timeline.getByTestId('decision-pager')).toContainText(
			'Showing 2 decisions · older decisions available'
		);
		// Until its row loads, the reason is listed once as an earlier trade reason.
		await expect(
			timeline.getByTestId('earlier-trade-reasons').getByTestId('trade-reason')
		).toHaveCount(1);

		await timeline.getByTestId('decision-load-more').click();
		await expect(timeline.getByTestId('decision-row')).toHaveCount(3);
		expect(searches.map((params) => params.get('cursor'))).toEqual([null, 'cursor-2']);
		expect(searches[1]?.get('limit')).toBe('50');
		await expect(timeline.getByTestId('decision-load-more')).toHaveCount(0);
		await expect(timeline.getByTestId('decision-pager')).toContainText(
			'Showing 3 decisions · start of the journaled history'
		);
		// The loaded row now owns the reason: listed nowhere else.
		await expect(timeline.getByTestId('earlier-trade-reasons')).toHaveCount(0);
		const olderRow = timeline.getByTestId('decision-row').nth(2);
		await olderRow.getByRole('button', { name: /Entry: earlier breakout/ }).click();
		await expect(olderRow.getByTestId('decision-trade-reason')).toHaveCount(1);
		await expect(timeline.locator(`[data-intent-id="${EARLIER_INTENT}"]`)).toHaveCount(1);
	});

	test('no durable storage is an honest empty state, not an error', async ({ page }) => {
		await mockDetailRoutes(page, {
			decisions: (route) =>
				route.fulfill({
					json: {
						deployment_id: deploymentId,
						...decisionPageBody([], { storage: 'unavailable' })
					}
				})
		});
		await page.goto(`/deployments/${deploymentId}`);
		const timeline = page.getByTestId('why-it-traded');
		await expect(timeline.getByTestId('decision-storage-unavailable')).toContainText(
			'Decision history is unavailable: no durable storage'
		);
		await expect(timeline.getByRole('alert')).toHaveCount(0);
		await expect(timeline.getByTestId('decision-row')).toHaveCount(0);
		await expect(timeline.getByTestId('decision-error')).toHaveCount(0);
	});

	test('a failed decisions read is an error with retry, distinct from empty', async ({ page }) => {
		let fail = true;
		await mockDetailRoutes(page, {
			decisions: (route) =>
				fail
					? route.fulfill({ status: 503, json: { detail: 'Decision journal store failed.' } })
					: route.fulfill({ json: { deployment_id: deploymentId, ...decisionPageBody([]) } })
		});
		await page.goto(`/deployments/${deploymentId}`);
		const timeline = page.getByTestId('why-it-traded');
		const error = timeline.getByTestId('decision-error');
		await expect(error).toContainText('Decision journal store failed.');
		await expect(error).toHaveAttribute('role', 'alert');
		fail = false;
		await error.getByRole('button', { name: 'Retry' }).click();
		await expect(timeline.getByTestId('decision-empty')).toHaveText(
			'No decisions journaled yet. A row is recorded after each completed 2h bar is evaluated.'
		);
	});

	test('the latest bar names when the next evaluation is due', async ({ page }) => {
		await mockDetailRoutes(page);
		await page.goto(`/deployments/${deploymentId}`);
		const latest = page.getByTestId('kpi-latest-bar');
		// The last evaluated 2h bar starts 20:00 (closed 22:00); the next one closes 00:00.
		await expect(latest.getByTestId('next-evaluation')).toHaveText(
			'Next evaluation ≈ 2026-09-22 00:00 UTC'
		);

		await mockDetailRoutes(page, {
			deployment: detailDeployment({ last_evaluated_bar: null, last_signal: null })
		});
		await page.reload();
		await expect(latest.getByTestId('next-evaluation')).toHaveText(
			'Next evaluation ≈ after the next 2h bar closes'
		);
		await expect(latest).toContainText('Not evaluated yet');
	});

	test('a multi-instrument bot names the product on every decision row', async ({ page }) => {
		const runtime = (productId: string) => ({
			product_id: productId,
			phase: 'flat',
			last_evaluated_bar: '2026-09-21T20:00:00+00:00',
			last_signal: 'not_matched',
			pending_entry_bars: 0,
			bars_held: 0,
			cooldown_bars_remaining: 0
		});
		await mockDetailRoutes(page, {
			deployment: detailDeployment({
				instrument_runtimes: [runtime('UNI-USDC'), runtime('ETH-USDC')]
			}),
			decisions: (route) =>
				route.fulfill({
					json: {
						deployment_id: deploymentId,
						...decisionPageBody([
							barDecision(deploymentId, { product_id: 'ETH-USDC', summary: 'No trade: ETH' }),
							barDecision(deploymentId)
						])
					}
				})
		});
		await page.goto(`/deployments/${deploymentId}`);
		const timeline = page.getByTestId('why-it-traded');
		await expect(timeline.getByTestId('decision-product')).toHaveText(['ETH-USDC', 'UNI-USDC']);
	});

	test('mobile 390px keeps an expanded decision row without horizontal overflow', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			decisions: (route) =>
				route.fulfill({
					json: { deployment_id: deploymentId, ...decisionPageBody(everyOutcome(deploymentId)) }
				})
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(`/deployments/${deploymentId}`);
		const entry = page.locator('[data-testid="decision-row"][data-outcome="entry_signal"]');
		await entry.getByRole('button').first().click();
		await expect(entry.getByTestId('decision-detail')).toBeVisible();
		const overflow = await page.evaluate(
			() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
		);
		expect(overflow).toBe(false);
	});

	test('a signal exit row names the exit and shows the evaluated exit rule (ADR 0093)', async ({
		page
	}) => {
		const signalExit = barDecision(deploymentId, {
			...at(0),
			outcome: 'exit',
			reason_code: 'EXIT_SIGNAL',
			summary: 'Exit (signal): EMA(20) 7.01 crosses below EMA(100) 7.05 → sell 5 @ 7.02',
			exit_reason: 'signal',
			rule: null,
			exit_rule: {
				outcome: 'matched',
				condition: {
					node: 'all',
					result: 'true',
					children: [
						comparison({
							result: 'true',
							label: 'EMA(20) crosses below EMA(100)',
							operator: 'crosses_below',
							operator_symbol: '↘',
							left: {
								kind: 'indicator',
								label: 'EMA(20)',
								key: 'fast',
								value: '7.01',
								previous_value: '7.08'
							},
							right: {
								kind: 'indicator',
								label: 'EMA(100)',
								key: 'slow',
								value: '7.05',
								previous_value: '7.04'
							}
						})
					]
				}
			},
			position: null
		});
		await mockDetailRoutes(page, {
			decisions: (route) =>
				route.fulfill({
					json: { deployment_id: deploymentId, ...decisionPageBody([signalExit]) }
				})
		});
		await page.goto(`/deployments/${deploymentId}`);
		const timeline = page.getByTestId('why-it-traded');
		const row = timeline.locator('[data-testid="decision-row"][data-outcome="exit"]');
		await row.getByRole('button', { name: /Exit \(signal\)/ }).click();
		const detail = row.getByTestId('decision-detail');
		await expect(detail).toContainText('Exit reason');
		await expect(detail).toContainText('signal exit');
		const exitRule = detail.getByTestId('decision-exit-rule');
		await expect(exitRule).toContainText('Exit rule');
		await expect(exitRule.getByTestId('decision-exit-rule-outcome')).toHaveAttribute(
			'data-outcome',
			'matched'
		);
		await expect(exitRule).toContainText('EMA(20)');
		await expect(exitRule).toContainText('EMA(100)');
	});
});

test('paper/live twin selection confirms before linking and unlinking', async ({ page }) => {
	const liveId = '01985cf0-7b60-7000-8000-00000000beef';
	await mockDetailRoutes(page, { inventory: [detailDeployment({ id: liveId, mode: 'live' })] });
	let twin: { paper_deployment_id: string; live_deployment_id: string; linked_at: string } | null =
		null;
	let writes = 0;
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${deploymentId}/twin`,
		async (route) => {
			const method = route.request().method();
			if (method === 'PUT') {
				expect(route.request().postDataJSON()).toEqual({ counterpart_deployment_id: liveId });
				twin = {
					paper_deployment_id: deploymentId,
					live_deployment_id: liveId,
					linked_at: '2026-10-03T03:00:00Z'
				};
				writes++;
			} else if (method === 'DELETE') {
				expect(new URL(route.request().url()).searchParams.get('counterpart_deployment_id')).toBe(
					liveId
				);
				twin = null;
				writes++;
			}
			await route.fulfill({ json: { deployment_id: deploymentId, twin } });
		}
	);
	await page.goto(`/deployments/${deploymentId}`);
	const card = page.getByTestId('deployment-twin');
	await card.getByLabel('Comparison bot').selectOption(liveId);
	await card.getByRole('button', { name: 'Link twin…', exact: true }).click();
	expect(writes).toBe(0);
	const dialog = page.getByTestId('twin-dialog');
	await expect(dialog).toContainText('Trading state and orders stay unchanged');
	await dialog.getByRole('button', { name: 'Link twins', exact: true }).click();
	await expect(card.getByRole('link', { name: /Live twin/ })).toHaveAttribute(
		'href',
		`/deployments/${liveId}`
	);
	expect(writes).toBe(1);
	await card.getByRole('button', { name: 'Unlink twin…', exact: true }).click();
	expect(writes).toBe(1);
	await dialog.getByRole('button', { name: 'Unlink twins', exact: true }).click();
	await expect(card).toContainText('No twin linked');
	expect(writes).toBe(2);
});

test('a twin mutation failure requires a read before another attempt', async ({ page }) => {
	const liveId = '01985cf0-7b60-7000-8000-00000000beef';
	await mockDetailRoutes(page, { inventory: [detailDeployment({ id: liveId, mode: 'live' })] });
	let reads = 0;
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${deploymentId}/twin`,
		async (route) => {
			if (route.request().method() === 'PUT') {
				await route.fulfill({ status: 409, json: { detail: 'A bot already has a twin.' } });
			} else {
				reads++;
				await route.fulfill({ json: { deployment_id: deploymentId, twin: null } });
			}
		}
	);
	await page.goto(`/deployments/${deploymentId}`);
	const card = page.getByTestId('deployment-twin');
	await card.getByLabel('Comparison bot').selectOption(liveId);
	await card.getByRole('button', { name: 'Link twin…', exact: true }).click();
	await page
		.getByTestId('twin-dialog')
		.getByRole('button', { name: 'Link twins', exact: true })
		.click();
	await expect(card).toContainText('Refresh the link before trying again');
	await expect(card.getByRole('button', { name: 'Link twin…', exact: true })).toHaveCount(0);
	await card.getByRole('button', { name: 'Refresh link', exact: true }).click();
	await expect(card).toContainText('No twin linked');
	expect(reads).toBe(2);
});
