import { expect, test } from '@playwright/test';

const strategyId = '01985cf0-7b60-7000-8000-000000000003';
const fingerprint = `sha256:${'a'.repeat(64)}`;

const draft = {
	schema_version: '1.0',
	strategy_id: strategyId,
	version: 1,
	name: 'Recovered BTC trend draft',
	description: 'Reference research strategy; not trading authority.',
	status: 'draft',
	created_at: '2026-08-14T12:00:00Z',
	instrument: { product_id: 'BTC-USD', base_currency: 'BTC', quote_currency: 'USD' },
	timeframe: '1h',
	data_requirements: {
		warmup_bars: 50,
		required_fields: ['open', 'high', 'low', 'close', 'volume']
	},
	indicators: [],
	entry: { side: 'long', when: { all: [] }, cooldown_bars: 3, max_open_positions: 1 },
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
	metadata: { tags: ['reference'], notes: [] }
};

const backtestSummary = {
	initial_equity: '10000',
	final_equity: '10850',
	total_net_pnl: '850',
	total_return_fraction: '0.085',
	gross_profit: '1200',
	gross_loss: '350',
	win_rate: '0.6',
	profit_factor: null,
	average_win: null,
	average_loss: null,
	trade_count: 5,
	winning_trade_count: 3,
	maximum_drawdown: '400',
	maximum_drawdown_fraction: '0.04',
	exposure_bars: 40,
	evaluation_bars: 168
};

const libraryEntry = {
	strategy_id: strategyId,
	name: 'Recovered BTC trend draft',
	product_id: 'BTC-USD',
	timeframe: '1h',
	latest_version: 1,
	status: 'draft',
	latest_fingerprint: null,
	published_versions: [],
	archived: false,
	summary: 'BTC-USD · 1h · EMA(20) crosses above EMA(50) · RSI ≥ 50 · 0.5% risk · $10-$100',
	backtest: null,
	paper_live: { paper: 'unavailable', live: 'unavailable' },
	created_at: '2026-08-14T12:00:00Z',
	updated_at: '2026-08-14T12:00:00Z'
};

const secondStrategyEntry = {
	...libraryEntry,
	strategy_id: '01985cf0-7b60-7000-8000-000000000009'
};

const publishedEntry = {
	...libraryEntry,
	status: 'published',
	latest_fingerprint: fingerprint,
	published_versions: [{ version: 1, strategy_fingerprint: fingerprint }],
	backtest: {
		result_fingerprint: `sha256:${'b'.repeat(64)}`,
		published_at: '2026-08-20T09:30:00Z',
		summary: backtestSummary
	}
};

async function mockLibrary(
	page: import('@playwright/test').Page,
	entries: unknown[]
): Promise<void> {
	await page.route('**/api/v1/strategies', async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		await route.fulfill({ json: { strategies: entries } });
	});
}

test('shows an empty library with create and import actions when no strategies exist', async ({
	page
}) => {
	await mockLibrary(page, []);
	await page.goto('/strategies');
	await expect(page.getByRole('heading', { name: 'Strategy library' })).toBeVisible();
	await expect(page.getByText('No strategies yet.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'New strategy' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Import JSON…' })).toBeVisible();
});

test('lists every strategy with market, version, status, backtest, and paper/live columns', async ({
	page
}) => {
	await mockLibrary(page, [publishedEntry, secondStrategyEntry]);
	await page.goto('/strategies');
	await expect(page.getByRole('cell', { name: 'Recovered BTC trend draft' })).toHaveCount(2);
	await expect(page.getByText('BTC-USD · 1h').first()).toBeVisible();
	const statusPills = page.locator('.status-pill');
	await expect(statusPills).toHaveCount(2);
	await expect(statusPills.first()).toHaveAttribute('data-status', 'published');
	await expect(statusPills.last()).toHaveAttribute('data-status', 'draft');
	await expect(page.getByText('unavailable / unavailable').first()).toBeVisible();
	await expect(page.getByRole('link', { name: /8\.50% · 5 trades/ })).toBeVisible();
});

test('links the latest backtest result to the backtests detail view', async ({ page }) => {
	await mockLibrary(page, [publishedEntry]);
	await page.goto('/strategies');
	const backtestLink = page.getByRole('link', { name: /8\.50% · 5 trades/ });
	await expect(backtestLink).toHaveAttribute(
		'href',
		`/backtests?result=${encodeURIComponent(`sha256:${'b'.repeat(64)}`)}`
	);
});

test('creates a reference strategy and refreshes the library', async ({ page }) => {
	await mockLibrary(page, [libraryEntry]);
	let createCalls = 0;
	await page.route('**/api/v1/strategies', async (route) => {
		if (route.request().method() === 'POST') {
			createCalls += 1;
			await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
			return;
		}
		await route.fulfill({ json: { strategies: [libraryEntry] } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.getByRole('button', { name: 'New strategy' }).click();
	await expect.poll(() => createCalls).toBe(1);
	await expect(page.getByRole('cell', { name: 'Recovered BTC trend draft' })).toBeVisible();
	await expect(page.getByRole('alert')).not.toBeVisible();
});

test('clones a published strategy by fingerprint and refreshes the library', async ({ page }) => {
	await mockLibrary(page, [publishedEntry]);
	let cloneFingerprint = '';
	await page.route('**/api/v1/strategies/clone', async (route) => {
		const body = (await route.request().postDataJSON()) as { strategy_fingerprint: string };
		cloneFingerprint = body.strategy_fingerprint;
		await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().hover();
	await page
		.getByRole('toolbar', { name: 'Row actions' })
		.getByRole('button', { name: 'Clone' })
		.click();
	await expect.poll(() => cloneFingerprint).toBe(fingerprint);
	await expect(page.getByRole('alert')).not.toBeVisible();
});

test('archives a published strategy and refreshes the library', async ({ page }) => {
	const archivedEntry = { ...publishedEntry, status: 'archived', archived: true };
	let archivedOnce = false;
	await page.route('**/api/v1/strategies', async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		await route.fulfill({ json: { strategies: [archivedOnce ? archivedEntry : publishedEntry] } });
	});
	await page.route('**/api/v1/strategies/*/archive', async (route) => {
		archivedOnce = true;
		await route.fulfill({
			json: { strategy_fingerprint: fingerprint, archived_at: '2026-08-28T12:00:00Z' }
		});
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().hover();
	const toolbar = page.getByRole('toolbar', { name: 'Row actions' });
	await toolbar.getByRole('button', { name: 'Archive' }).click();
	await expect(page.locator('.status-pill[data-status="archived"]')).toBeVisible();
	await expect(page.getByRole('alert')).not.toBeVisible();
});

test('shows a hover action bar with status-appropriate actions and edge-safe positioning', async ({
	page
}) => {
	await mockLibrary(page, [publishedEntry, secondStrategyEntry]);
	await page.setViewportSize({ width: 900, height: 600 });
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.waitForTimeout(400);
	await expect(page.locator('th')).toHaveCount(6);
	await expect(page.getByRole('columnheader', { name: 'Actions' })).toHaveCount(0);

	const firstRow = page.locator('table tbody tr').first();
	await firstRow.hover();
	const toolbar = page.getByRole('toolbar', { name: 'Row actions' });
	await expect(toolbar).toBeVisible();
	await expect(toolbar.getByRole('button', { name: 'View' })).toBeVisible();
	await expect(toolbar.getByRole('button', { name: 'Clone' })).toBeVisible();
	await expect(toolbar.getByRole('button', { name: 'Archive' })).toBeVisible();
	await expect(toolbar.getByRole('link', { name: 'Edit' })).toHaveCount(0);
	const barBox = await toolbar.boundingBox();
	expect(barBox).not.toBeNull();
	if (barBox) {
		expect(barBox.x).toBeGreaterThanOrEqual(0);
		expect(barBox.x + barBox.width).toBeLessThanOrEqual(900);
	}

	await page.locator('table tbody tr').nth(1).hover();
	await expect(toolbar.getByRole('link', { name: 'Edit' })).toBeVisible();
	await expect(toolbar.getByRole('button', { name: 'Clone' })).toHaveCount(0);

	await page.locator('tbody').hover({ position: { x: 5, y: 5 } });
	await firstRow.hover();
	await page.mouse.move(10, 10);
	await expect(toolbar).not.toBeVisible();
});

test('ignores stale inspector responses after another strategy is opened', async ({ page }) => {
	const otherFingerprint = `sha256:${'c'.repeat(64)}`;
	const otherEntry = {
		...publishedEntry,
		strategy_id: secondStrategyEntry.strategy_id,
		name: 'Second strategy',
		latest_fingerprint: otherFingerprint,
		published_versions: [{ version: 1, strategy_fingerprint: otherFingerprint }]
	};
	await mockLibrary(page, [publishedEntry, otherEntry]);
	let firstSourceStarted = false;
	let releaseFirstSource: () => void = () => undefined;
	const firstSourceGate = new Promise<void>((resolve) => {
		releaseFirstSource = resolve;
	});
	await page.route('**/api/v1/strategies/source/*', async (route) => {
		if (route.request().url().includes(encodeURIComponent(fingerprint))) {
			firstSourceStarted = true;
			await firstSourceGate;
			await route.fulfill({ json: { strategy: { ...draft, status: 'published' } } });
			return;
		}
		await route.fulfill({
			json: {
				strategy: {
					...draft,
					strategy_id: otherEntry.strategy_id,
					name: otherEntry.name,
					status: 'published'
				}
			}
		});
	});
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);
	await page.route(
		(url) =>
			url.toString().includes('/api/v1/backtests') &&
			url.toString().includes('strategy_fingerprint='),
		async (route) => route.fulfill({ json: { entries: [], limit: 20, offset: 0, returned: 0 } })
	);

	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().click();
	await page.getByRole('button', { name: 'Close' }).click();
	await page.locator('table tbody tr').nth(1).click();
	await expect(page.getByRole('heading', { name: 'Second strategy' })).toBeVisible();
	await expect(page.getByText(/^Second strategy: when/)).toBeVisible();

	expect(firstSourceStarted).toBe(true);
	releaseFirstSource();
	await page.waitForTimeout(250);
	await expect(page.getByRole('heading', { name: 'Second strategy' })).toBeVisible();
	await expect(page.getByText(/^Second strategy: when/)).toBeVisible();
});

test('versions tab lists history, exports, diffs, and revises into draft v2', async ({ page }) => {
	const secondFingerprint = `sha256:${'c'.repeat(64)}`;
	const versionsEntry = {
		...publishedEntry,
		latest_version: 2,
		latest_fingerprint: secondFingerprint,
		published_versions: [
			{ version: 1, strategy_fingerprint: fingerprint },
			{ version: 2, strategy_fingerprint: secondFingerprint }
		]
	};
	const revisedDraft = {
		...draft,
		strategy_id: versionsEntry.strategy_id,
		version: 3,
		status: 'draft'
	};
	let reviseFingerprint = '';
	await mockLibrary(page, [versionsEntry]);
	await page.route(`**/api/v1/strategies/${versionsEntry.strategy_id}/history`, async (route) =>
		route.fulfill({
			json: {
				strategy_id: versionsEntry.strategy_id,
				latest_version: 2,
				next_version: 3,
				versions: [
					{
						version: 1,
						strategy_fingerprint: fingerprint,
						published: true,
						archived: true,
						archived_at: '2026-08-28T12:00:00Z',
						backtest: null
					},
					{
						version: 2,
						strategy_fingerprint: secondFingerprint,
						published: true,
						archived: false,
						archived_at: null,
						backtest: null
					}
				],
				draft: null
			}
		})
	);
	await page.route('**/api/v1/strategies/source/*', async (route) => {
		const url = route.request().url();
		const requested = decodeURIComponent(url.split('/source/')[1] ?? '');
		await route.fulfill({
			json: {
				strategy: {
					...draft,
					strategy_id: versionsEntry.strategy_id,
					status: 'published',
					name: requested === fingerprint ? 'Recovered BTC trend draft' : 'Renamed trend draft'
				}
			}
		});
	});
	await page.route('**/api/v1/strategies/*/revise', async (route) => {
		const body = (await route.request().postDataJSON()) as { strategy_fingerprint: string };
		reviseFingerprint = body.strategy_fingerprint;
		await route.fulfill({
			status: 201,
			json: {
				strategy: revisedDraft,
				revision: 1,
				source_fingerprint: body.strategy_fingerprint,
				summary: 'revised'
			}
		});
	});

	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().click();
	await page.getByRole('tab', { name: 'Versions' }).click();
	await expect(page.getByRole('table', { name: 'Published version history' })).toBeVisible();
	const historyTable = page.getByRole('table', { name: 'Published version history' });
	await expect(historyTable.getByRole('cell', { name: 'V1', exact: true })).toBeVisible();
	await expect(historyTable.getByRole('cell', { name: 'V2', exact: true })).toBeVisible();
	await expect(page.getByText(/archived ·/).first()).toBeVisible();

	// Export downloads the canonical definition for the selected version.
	const download = page.waitForEvent('download');
	await page
		.getByRole('table', { name: 'Published version history' })
		.getByRole('button', { name: 'Export' })
		.first()
		.click();
	expect((await download).suggestedFilename()).toMatch(/\.json$/);

	// Semantic diff between V1 and V2 reports the renamed strategy name.
	const diffTable = page.getByRole('table', { name: 'Semantic diff' });
	await expect(diffTable).toBeVisible();
	await expect(diffTable.getByRole('cell', { name: 'Strategy name' })).toBeVisible();
	await expect(diffTable.getByText('Recovered BTC trend draft')).toBeVisible();
	await expect(diffTable.getByText('Renamed trend draft')).toBeVisible();

	// Edit into next draft creates draft v3 on the same identity.
	await page
		.getByRole('table', { name: 'Published version history' })
		.getByRole('button', { name: 'Edit into next draft' })
		.first()
		.click();
	await expect.poll(() => reviseFingerprint).toBe(fingerprint);
	await expect(page.getByRole('alert')).not.toBeVisible();
});

test('research tab launches a backtest with engine and spread and lists version results', async ({
	page
}) => {
	const secondFingerprint = `sha256:${'c'.repeat(64)}`;
	const researchEntry = {
		...publishedEntry,
		latest_version: 2,
		latest_fingerprint: secondFingerprint,
		published_versions: [
			{ version: 1, strategy_fingerprint: fingerprint },
			{ version: 2, strategy_fingerprint: secondFingerprint }
		]
	};
	await mockLibrary(page, [researchEntry]);
	let launchBody: {
		engine_contract_version: string;
		spread_bps: string | null;
		strategy_fingerprint: string;
	} | null = null;
	await page.route(
		(url) =>
			url.toString().includes('/api/v1/backtests') &&
			url.toString().includes('strategy_fingerprint='),
		async (route) => {
			const requestedFingerprint = new URL(route.request().url()).searchParams.get(
				'strategy_fingerprint'
			);
			const isSecondVersion = requestedFingerprint === secondFingerprint;
			await route.fulfill({
				json: {
					entries: [
						{
							result_fingerprint: `sha256:${isSecondVersion ? '9'.repeat(64) : 'd'.repeat(64)}`,
							run_fingerprint: `sha256:${'e'.repeat(64)}`,
							strategy_fingerprint: requestedFingerprint,
							dataset_fingerprint: `sha256:${'f'.repeat(64)}`,
							engine_contract_version: isSecondVersion
								? 'thytrader-bar-backtest-v2'
								: 'thytrader-bar-backtest-v1',
							published_at: '2026-08-21T10:00:00Z',
							summary: {
								...backtestSummary,
								total_return_fraction: isSecondVersion ? '0.12' : '0.085'
							}
						}
					],
					limit: 20,
					offset: 0,
					returned: 1
				}
			});
		}
	);
	await page.route('**/api/v1/backtests', async (route) => {
		if (route.request().method() === 'POST') {
			launchBody = (await route.request().postDataJSON()) as typeof launchBody;
			await route.fulfill({
				status: 201,
				json: {
					run_fingerprint: `sha256:${'e'.repeat(64)}`,
					result_fingerprint: `sha256:${'d'.repeat(64)}`
				}
			});
			return;
		}
		await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
	});
	const datasetFingerprint = `sha256:${'f'.repeat(64)}`;
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({
			json: {
				datasets: [
					{
						product_id: 'BTC-USD',
						starts_at: '2026-06-01T00:00:00Z',
						ends_at: '2026-08-01T00:00:00Z',
						content_fingerprint: datasetFingerprint
					}
				]
			}
		})
	);
	await page.route('**/api/v1/strategies/source/*', async (route) =>
		route.fulfill({ json: { strategy: { ...draft, status: 'published' } } })
	);

	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().hover();
	await page
		.getByRole('toolbar', { name: 'Row actions' })
		.getByRole('button', { name: 'View' })
		.click();
	await expect(page.getByRole('dialog', { name: 'Strategy inspector' })).toBeVisible();
	await expect(page.getByText('Plain-English summary')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByRole('heading', { name: 'Unsaved changes' })).toBeVisible();
	await expect(page.getByText('Read-only — no local edits.')).toBeVisible();
	const engineMatrix = page.getByRole('table', { name: 'Engine support matrix' });
	await expect(engineMatrix.getByRole('columnheader', { name: 'V1' })).toBeVisible();
	await expect(engineMatrix.getByRole('columnheader', { name: 'V2' })).toBeVisible();

	// Insight tab shows version results loaded per fingerprint.
	await expect(page.getByText('Results by version')).not.toBeVisible();
	await page.getByRole('tab', { name: 'Research' }).click();
	await expect(page.getByText('Launch backtest')).toBeVisible();
	await expect(page.getByText('Results by version')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Version 1', exact: false })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Version 2', exact: false })).toBeVisible();
	await expect(page.getByRole('link', { name: '8.50%', exact: true })).toHaveCount(2);
	await expect(page.getByRole('link', { name: '12.00%', exact: true })).toHaveCount(2);
	await expect(page.getByRole('table', { name: 'Latest result comparison' })).toBeVisible();
	await expect(page.getByLabel('Strategy version')).toHaveValue(secondFingerprint);
	await expect(page.getByLabel('Verified dataset')).toHaveValue(datasetFingerprint);

	// Fill launch form with V2 + spread and submit.
	await page.getByLabel('Engine').selectOption('thytrader-bar-backtest-v2');
	await page.getByLabel('Constant spread (bps, total bid-ask)').fill('8');
	await page.getByLabel('Evaluation start').fill('2026-07-01T00:00');
	await page.getByLabel('Evaluation end').fill('2026-07-08T00:00');
	await page.getByLabel('Initial capital (USD)').fill('10000');
	await page.getByRole('button', { name: 'Run backtest' }).click();
	await expect.poll(() => launchBody).not.toBeNull();
	const sent = launchBody as unknown as {
		engine_contract_version: string;
		spread_bps: string | null;
		strategy_fingerprint: string;
	};
	expect(sent.engine_contract_version).toBe('thytrader-bar-backtest-v2');
	expect(sent.spread_bps).toBe('8');
	expect(sent.strategy_fingerprint).toBe(secondFingerprint);
});

test('research tab loads every result page for an exact strategy version', async ({ page }) => {
	await mockLibrary(page, [publishedEntry]);
	const requestedOffsets: number[] = [];
	await page.route(
		(url) =>
			url.toString().includes('/api/v1/backtests') &&
			url.toString().includes('strategy_fingerprint='),
		async (route) => {
			const offset = Number(new URL(route.request().url()).searchParams.get('offset') ?? '0');
			requestedOffsets.push(offset);
			const indexes = offset === 0 ? Array.from({ length: 20 }, (_, index) => index + 1) : [21];
			await route.fulfill({
				json: {
					entries: indexes.map((index) => ({
						result_fingerprint: `sha256:${index.toString(16).padStart(64, '0')}`,
						run_fingerprint: `sha256:${'e'.repeat(64)}`,
						strategy_fingerprint: fingerprint,
						dataset_fingerprint: `sha256:${'f'.repeat(64)}`,
						engine_contract_version: 'thytrader-bar-backtest-v1',
						published_at: '2026-08-21T10:00:00Z',
						summary: {
							...backtestSummary,
							total_return_fraction: index === 21 ? '0.21' : '0.01'
						}
					})),
					limit: 20,
					offset,
					returned: indexes.length
				}
			});
		}
	);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ json: { datasets: [] } })
	);
	await page.route('**/api/v1/strategies/source/*', async (route) =>
		route.fulfill({ json: { strategy: { ...draft, status: 'published' } } })
	);

	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().hover();
	await page
		.getByRole('toolbar', { name: 'Row actions' })
		.getByRole('button', { name: 'View' })
		.click();
	await page.getByRole('tab', { name: 'Research' }).click();

	await expect(page.getByRole('link', { name: '21.00%', exact: true })).toBeVisible();
	expect(requestedOffsets).toEqual([0, 20]);
});

test('research tab preserves inspector evidence when the dataset catalog fails', async ({
	page
}) => {
	await mockLibrary(page, [publishedEntry]);
	await page.route('**/api/v1/market-data/datasets', async (route) =>
		route.fulfill({ status: 503, json: { detail: 'Verified datasets are unavailable.' } })
	);
	await page.route('**/api/v1/strategies/source/*', async (route) =>
		route.fulfill({ json: { strategy: { ...draft, status: 'published' } } })
	);
	await page.route(
		(url) =>
			url.toString().includes('/api/v1/backtests') &&
			url.toString().includes('strategy_fingerprint='),
		async (route) => route.fulfill({ json: { entries: [], limit: 20, offset: 0, returned: 0 } })
	);

	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.locator('table tbody tr').first().hover();
	await page
		.getByRole('toolbar', { name: 'Row actions' })
		.getByRole('button', { name: 'View' })
		.click();
	await expect(page.getByText('Plain-English summary')).toBeVisible();
	await page.getByRole('tab', { name: 'Research' }).click();

	await expect(page.getByRole('alert')).toContainText('Verified datasets are unavailable.');
	await expect(page.getByRole('button', { name: 'Run backtest' })).toBeDisabled();
});

test('imports a pasted strategy definition as a new draft', async ({ page }) => {
	await mockLibrary(page, [libraryEntry]);
	let importedBody: unknown = null;
	await page.route('**/api/v1/strategies/import', async (route) => {
		importedBody = await route.request().postDataJSON();
		await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.getByRole('button', { name: 'Import JSON…' }).click();
	await expect(page.getByRole('dialog', { name: 'Import strategy JSON' })).toBeVisible();
	await page.getByLabel('Strategy definition JSON').fill(JSON.stringify(draft));
	await page.getByRole('button', { name: 'Import draft' }).click();
	await expect.poll(() => importedBody).not.toBeNull();
	expect((importedBody as { strategy: { strategy_id: string } }).strategy.strategy_id).toBe(
		strategyId
	);
	await expect(page.getByRole('cell', { name: 'Recovered BTC trend draft' })).toBeVisible();
});

test('rejects invalid import JSON without leaving the dialog or sending a request', async ({
	page
}) => {
	await mockLibrary(page, []);
	let importCalls = 0;
	await page.route('**/api/v1/strategies/import', async (route) => {
		importCalls += 1;
		await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('.empty-state');
	await page.getByRole('button', { name: 'Import JSON…' }).click();
	await expect(page.getByRole('dialog', { name: 'Import strategy JSON' })).toBeVisible();
	await page.getByLabel('Strategy definition JSON').fill('{not json');
	await page.getByRole('button', { name: 'Import draft' }).click();
	await expect(page.getByRole('alert').last()).toContainText('not valid JSON');
	await expect(page.getByLabel('Strategy definition JSON')).toBeVisible();
	expect(importCalls).toBe(0);
});

test('surfaces a controlled error banner when the library cannot load', async ({ page }) => {
	await page.route('**/api/v1/strategies', async (route) =>
		route.fulfill({ status: 503, json: { detail: 'Strategy lifecycle storage is unavailable.' } })
	);
	await page.goto('/strategies');
	await expect(page.getByRole('alert')).toContainText('Strategy lifecycle storage is unavailable.');
	await expect(page.getByRole('button', { name: 'Retry library load' })).toBeVisible();
});
