import { expect, test } from '../../../e2e/harness';

const deploymentId = '01a0ad72-0000-0000-0000-000000000000';
const fingerprintA = `sha256:${'a'.repeat(64)}`;
const fingerprintB = `sha256:${'b'.repeat(64)}`;

const strategyDraft = {
	schema_version: '1.0',
	strategy_id: '01a0ad42-0000-0000-0000-000000000000',
	version: 2,
	name: 'UNI trend config',
	description: null,
	status: 'published',
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

function detailDeployment(overrides: Record<string, unknown> = {}) {
	return {
		id: deploymentId,
		strategy_fingerprint: fingerprintA,
		strategy_id: '01a0ad42-0000-0000-0000-000000000000',
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
		inventory?: unknown[];
	} = {}
) {
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
	await page.route('**/api/v1/strategies/source/*', (route) => {
		const source = overrides.strategySource ?? { status: 200, body: { strategy: strategyDraft } };
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

		await expect(page.getByRole('heading', { level: 1, name: 'UNI / USDC' })).toBeVisible();
		await expect(page.getByTestId('deployment-fingerprint')).toHaveText(fingerprintA);
		await expect(page.getByTestId('deployment-status')).toHaveText('running');
		await expect(page.getByTestId('deployment-eligibility')).toHaveText('Eligible');
		await expect(page.getByText(/signal: not_matched/)).toBeVisible();
		// The immutable rule/config from the source API, not just the fingerprint.
		const config = page.getByTestId('strategy-config-summary');
		await expect(config).toBeVisible();
		await expect(config).toContainText('UNI trend config: when ema_fast crosses above ema_slow');
		await expect(config).toContainText('ema_fast crosses above ema_slow');
		await expect(config.getByText(/atr_multiple/)).toBeVisible();
	});

	test('shows an explicit unavailable state when the source API cannot load the config', async ({
		page
	}) => {
		await mockDetailRoutes(page, {
			strategySource: { status: 404, body: { detail: 'Published strategy was not found.' } }
		});
		await page.goto(`/deployments/${deploymentId}`);
		const unavailable = page.getByTestId('strategy-config-unavailable');
		await expect(unavailable).toBeVisible();
		await expect(unavailable).toContainText('Immutable configuration unavailable');
		await expect(unavailable).toContainText('no other version was substituted');
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
		await page.route('**/api/v1/strategies/source/*', (route) =>
			route.fulfill({ json: { strategy: strategyDraft } })
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

		// First page renders with a working Next; fills shows its distinct empty state.
		await expect(page.getByTestId('orders-pager').getByText('50 orders')).toBeVisible();
		await expect(page.getByTestId('fills-empty')).toHaveText(
			'No fills recorded for this deployment.'
		);
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
		await page.route('**/api/v1/strategies/source/*', (route) =>
			route.fulfill({ json: { strategy: strategyDraft } })
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
		await expect(page.getByTestId('fills-empty')).toBeVisible();

		// Retry succeeds into the true-empty state, distinct from the failure.
		ordersFail = false;
		await error.getByRole('button', { name: 'Retry' }).click();
		await expect(page.getByTestId('orders-empty')).toHaveText(
			'No orders recorded for this deployment.'
		);
	});

	test('evidence links are exact-version scoped and never claim completeness', async ({ page }) => {
		await mockDetailRoutes(page);
		await page.goto(`/deployments/${deploymentId}`);
		const links = page.getByTestId('evidence-link');
		await expect(links).toHaveCount(2);
		await expect(links.first()).toBeVisible();
		await expect(page.getByTestId('evidence-link').first()).toHaveAttribute(
			'href',
			`/backtests?strategy_fingerprint=${encodeURIComponent(fingerprintA)}`
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
		await expect(table.getByRole('cell', { name: '5' })).toBeVisible();
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
		await page.route('**/api/v1/strategies/source/*', (route) =>
			route.fulfill({ json: { strategy: strategyDraft } })
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
		await page.route('**/api/v1/strategies/source/*', (route) =>
			route.fulfill({ json: { strategy: strategyDraft } })
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
		await page.route('**/api/v1/strategies/source/*', (route) =>
			route.fulfill({ json: { strategy: strategyDraft } })
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
		await page.route('**/api/v1/strategies/source/*', (route) =>
			route.fulfill({ json: { strategy: strategyDraft } })
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
		await page.route('**/api/v1/strategies/source/*', (route) =>
			route.fulfill({ json: { strategy: strategyDraft } })
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

	test('other versions of the same strategy are separated, not mixed in', async ({ page }) => {
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
});
