import { expect, isStrategyLibraryRequest, test } from '../e2e/harness';

const strategyId = '01985cf0-7b60-7000-8000-000000000003';
const fingerprint = `sha256:${'a'.repeat(64)}`;

const draft = {
	schema_version: '1.0',
	strategy_id: strategyId,
	name: 'Recovered BTC trend draft',
	description: 'Reference research strategy; not trading authority.',
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
	revision: 1,
	valid: false,
	current_fingerprint: null,
	summary: 'BTC-USD · 1h · EMA(20) crosses above EMA(50) AND RSI(14) ≥ 50 · 0.5% risk · $10-$100',
	backtest: null,
	paper_live: { paper: 'none', live: 'none' },
	active_deployment_count: 0,
	created_at: '2026-08-14T12:00:00Z',
	updated_at: '2026-08-14T12:00:00Z'
};

const secondStrategyEntry = {
	...libraryEntry,
	strategy_id: '01985cf0-7b60-7000-8000-000000000009',
	name: 'Second strategy'
};

const publishedEntry = {
	...libraryEntry,
	valid: true,
	current_fingerprint: fingerprint,
	backtest: {
		result_fingerprint: `sha256:${'b'.repeat(64)}`,
		strategy_fingerprint: fingerprint,
		published_at: '2026-08-20T09:30:00Z',
		summary: backtestSummary
	}
};

function record(strategy: typeof draft = draft) {
	return {
		strategy_id: strategy.strategy_id,
		name: strategy.name,
		revision: 1,
		created_at: strategy.created_at,
		updated_at: strategy.created_at,
		document: strategy,
		strategy: null,
		validation: { valid: false, issues: [{ loc: 'indicators', message: 'too short' }] },
		current_fingerprint: null,
		summary: null,
		product_id: 'BTC-USD',
		timeframe: '1h'
	};
}

const zeroCounts = {
	snapshots: 0,
	backtests: 0,
	research_runs: 0,
	studies: 0,
	research_jobs: 0,
	dataset_bindings: 0,
	paper_deployments: 0,
	live_deployments_kept: 0,
	allocations_removed: 0
};

type BulkBody = { strategy_ids: string[]; confirm: boolean; dry_run: boolean };

async function mockLibrary(
	page: import('@playwright/test').Page,
	entries: unknown[]
): Promise<void> {
	await page.route(isStrategyLibraryRequest, async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		await route.fulfill({ json: { strategies: entries, has_more: false, next_cursor: null } });
	});
}

test('loads only the requested strategy page and changes the server limit', async ({ page }) => {
	const requested: string[] = [];
	await page.route(isStrategyLibraryRequest, async (route) => {
		const url = new URL(route.request().url());
		requested.push(url.search);
		const cursor = url.searchParams.get('cursor');
		await route.fulfill({
			json: {
				strategies: [cursor ? secondStrategyEntry : libraryEntry],
				has_more: cursor === null,
				next_cursor: cursor === null ? 'next' : null
			}
		});
	});
	await page.goto('/strategies');
	await expect(page.getByTestId('strategy-page-size')).toHaveValue('10');
	await expect(page.locator('tbody tr')).toHaveCount(1);
	await expect.poll(() => requested).toEqual(['?limit=10&origin=operator']);
	await page.getByRole('button', { name: 'Next strategy page' }).click();
	await expect(
		page.locator(`tr[data-strategy-id="${secondStrategyEntry.strategy_id}"]`)
	).toBeVisible();
	await expect
		.poll(() => requested)
		.toEqual(['?limit=10&origin=operator', '?limit=10&cursor=next&origin=operator']);
	await page.getByRole('button', { name: 'Previous strategy page' }).click();
	await expect(page.locator(`tr[data-strategy-id="${strategyId}"]`)).toBeVisible();
	await page.getByTestId('strategy-page-size').selectOption('25');
	await expect(page.getByTestId('strategy-page-size')).toHaveValue('25');
	await expect.poll(() => requested.at(-1)).toBe('?limit=25&origin=operator');
	await expect(page.getByTestId('strategy-page-range')).toContainText('Page 1');
});

test('shows an empty library with create and import actions when no strategies exist', async ({
	page
}) => {
	await mockLibrary(page, []);
	await page.goto('/strategies');
	await expect(page.getByRole('heading', { name: 'Strategies', level: 1 })).toBeVisible();
	await expect(page.getByText('No strategies yet.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'New strategy' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Import JSON…' })).toBeVisible();
});

test('fetches the next strategy page only after navigation, not in the background', async ({
	page
}) => {
	let releaseNextPage: () => void = () => undefined;
	const nextPageHeld = new Promise<void>((resolve) => {
		releaseNextPage = resolve;
	});
	let requests = 0;
	await page.route(isStrategyLibraryRequest, async (route) => {
		requests += 1;
		if (new URL(route.request().url()).searchParams.has('cursor')) {
			await nextPageHeld;
			await route.fulfill({ json: { strategies: [secondStrategyEntry], has_more: false } });
			return;
		}
		await route.fulfill({
			json: { strategies: [libraryEntry], has_more: true, next_cursor: 'next' }
		});
	});
	await page.goto('/strategies');
	await expect(page.locator(`tr[data-strategy-id="${strategyId}"]`)).toBeVisible();
	expect(requests).toBe(1);
	await page.getByRole('button', { name: 'Next strategy page' }).click();
	try {
		await expect.poll(() => requests).toBe(2);
		await expect(page.getByRole('button', { name: 'Next strategy page' })).toBeDisabled();
	} finally {
		releaseNextPage();
	}
	await expect(
		page.locator(`tr[data-strategy-id="${secondStrategyEntry.strategy_id}"]`)
	).toBeVisible();
});

test('a failed next strategy page remains an error rather than an empty library', async ({
	page
}) => {
	await page.route(isStrategyLibraryRequest, async (route) => {
		if (new URL(route.request().url()).searchParams.has('cursor')) {
			await route.fulfill({ status: 503, json: { detail: 'Catalog unavailable' } });
			return;
		}
		await route.fulfill({
			json: { strategies: [libraryEntry], has_more: true, next_cursor: 'next' }
		});
	});
	await page.goto('/strategies');
	await expect(page.locator(`tr[data-strategy-id="${strategyId}"]`)).toBeVisible();
	await page.getByRole('button', { name: 'Next strategy page' }).click();
	await expect(page.getByRole('alert')).toContainText('HTTP 503');
	await expect(page.getByText('No strategies yet.')).toHaveCount(0);
});

test('rejects an empty page that claims more instead of reporting an empty library', async ({
	page
}) => {
	await page.route(isStrategyLibraryRequest, async (route) => {
		await route.fulfill({
			json: { strategies: [], returned: 0, has_more: true, next_cursor: 'next' }
		});
	});
	await page.goto('/strategies');
	await expect(page.getByRole('alert')).toContainText('empty page');
	await expect(page.getByText('No strategies yet.')).toHaveCount(0);
});

test('does not label an unavailable first page as an empty library', async ({ page }) => {
	await page.route(isStrategyLibraryRequest, async (route) => {
		await route.fulfill({ status: 503, json: { detail: 'Catalog unavailable' } });
	});
	await page.goto('/strategies');
	await expect(page.getByRole('alert')).toBeVisible();
	await expect(page.getByText('Could not load strategies. Retry the library load.')).toBeVisible();
	await expect(page.getByText('No strategies yet.')).toHaveCount(0);
});

test('rows show market, validity, an evidence pipeline, and the latest backtest', async ({
	page
}) => {
	const invalidRunning = {
		...secondStrategyEntry,
		name: 'Work in progress',
		paper_live: { paper: 'running', live: 'none' }
	};
	await mockLibrary(page, [publishedEntry, invalidRunning]);
	await page.goto('/strategies');
	const table = page.getByRole('table', { name: 'Strategies' });
	for (const header of ['Strategy', 'Market', 'Progress', 'Latest backtest', 'Updated']) {
		await expect(table.getByRole('columnheader', { name: header, exact: true })).toBeVisible();
	}
	await expect(table.getByRole('columnheader', { name: 'Latest', exact: true })).toHaveCount(0);
	await expect(table.getByRole('columnheader', { name: /Paper \/ live/ })).toHaveCount(0);
	const first = page.locator(`tr[data-strategy-id="${strategyId}"]`);
	await expect(first).toContainText('BTC / USD · 1h');
	await expect(first).toContainText('sha256:aaaa…aaaa');
	await expect(first).not.toContainText(/\bv\d/);
	await expect(first).toContainText(
		'Build: rules valid; Test: has a backtest; Paper: not deployed; Live: not deployed'
	);
	await expect(first.getByRole('link', { name: /Latest backtest 8\.50%, 5 trades/ })).toBeVisible();
	const second = page.locator(`tr[data-strategy-id="${secondStrategyEntry.strategy_id}"]`);
	await expect(second).toContainText('definition has problems');
	await expect(second).toContainText('Build: definition has problems');
	await expect(second).toContainText('Paper: running');
	await expect(second.getByTestId('library-pipeline').locator('.step')).toHaveText([
		'Build',
		'Test',
		'Paper',
		'Live'
	]);
	await expect(second.getByTestId('library-pipeline').locator('.step.paper')).toHaveText('Paper');
});

test('a row opens the strategy workspace', async ({ page }) => {
	await mockLibrary(page, [publishedEntry]);
	await page.route(`**/api/v1/strategies/${strategyId}`, (route) =>
		route.fulfill({ json: record() })
	);
	await page.goto('/strategies');
	await expect(page.getByRole('link', { name: 'Recovered BTC trend draft' })).toHaveAttribute(
		'href',
		`/strategies/${strategyId}`
	);
	await page.locator(`tr[data-strategy-id="${strategyId}"] td`).nth(2).click();
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}$`));
	await expect(page.getByTestId('workspace-name')).toHaveText('Recovered BTC trend draft');
	await expect(
		page.getByRole('navigation', { name: 'Strategy stages' }).getByRole('link', { name: /Build/ })
	).toHaveAttribute('aria-current', 'page');
});

test('the latest backtest links to that result on the Test stage', async ({ page }) => {
	await mockLibrary(page, [publishedEntry]);
	await page.goto('/strategies');
	await expect(page.getByRole('link', { name: /Latest backtest 8\.50%/ })).toHaveAttribute(
		'href',
		`/strategies/${strategyId}/test?result=${encodeURIComponent(`sha256:${'b'.repeat(64)}`)}`
	);
});

test('creates a reference strategy from the chosen template and clock', async ({ page }) => {
	let createUrl = '';
	await page.route(isStrategyLibraryRequest, async (route) => {
		if (route.request().method() === 'POST') {
			createUrl = route.request().url();
			await route.fulfill({ status: 201, json: record() });
			return;
		}
		await route.fulfill({ json: { strategies: [libraryEntry] } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.getByLabel('Template').selectOption('rsi-mean-reversion');
	await page.getByLabel('Clock').selectOption('4h');
	await page.getByRole('button', { name: 'New strategy' }).click();
	await expect.poll(() => createUrl).toContain('template=rsi-mean-reversion');
	expect(createUrl).toContain('timeframe=4h');
	expect(createUrl).toContain('product_id=BTC-USDC');
	await expect(page.getByRole('link', { name: 'Recovered BTC trend draft' })).toBeVisible();
});

test('new strategy uses the chosen market and remembers it', async ({ page }) => {
	const createUrls: string[] = [];
	await page.route(isStrategyLibraryRequest, async (route) => {
		if (route.request().method() === 'POST') {
			createUrls.push(route.request().url());
			await route.fulfill({ status: 201, json: record() });
			return;
		}
		await route.fulfill({ json: { strategies: [libraryEntry] } });
	});
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await expect(page.getByLabel('Market', { exact: true })).toHaveValue('BTC-USDC');
	await page.getByLabel('Market', { exact: true }).fill('eth-usd');
	await page.getByRole('button', { name: 'New strategy' }).click();
	await expect.poll(() => createUrls.length).toBe(1);
	expect(createUrls[0]).toContain('product_id=ETH-USD');
	await page.reload();
	await page.waitForSelector('table tbody tr');
	await expect(page.getByLabel('Market', { exact: true })).toHaveValue('ETH-USD');
});

test('clones a strategy by id and refreshes the library', async ({ page }) => {
	await mockLibrary(page, [publishedEntry]);
	let cloned = false;
	await page.route(`**/api/v1/strategies/${strategyId}/clone`, async (route) => {
		cloned = route.request().method() === 'POST';
		await route.fulfill({ status: 201, json: record() });
	});
	await page.goto('/strategies');
	await page.getByRole('button', { name: 'Clone Recovered BTC trend draft' }).click();
	await expect.poll(() => cloned).toBe(true);
	await expect(page).toHaveURL(/\/strategies$/);
});

test('every row offers clone and delete, including invalid work in progress', async ({ page }) => {
	await mockLibrary(page, [libraryEntry]);
	await page.goto('/strategies');
	const row = page.locator(`tr[data-strategy-id="${strategyId}"]`);
	await expect(row.getByRole('button', { name: 'Clone Recovered BTC trend draft' })).toBeVisible();
	await expect(
		row.getByRole('button', { name: 'Delete Recovered BTC trend draft…' })
	).toBeVisible();
	await expect(row.getByRole('button', { name: /Archive/ })).toHaveCount(0);
});

test('single delete previews what goes, keeps live history, then deletes', async ({ page }) => {
	let deleted = false;
	const dryRuns: BulkBody[] = [];
	await page.route(isStrategyLibraryRequest, (route) =>
		route.fulfill({ json: { strategies: deleted ? [] : [publishedEntry] } })
	);
	await page.route('**/api/v1/strategies/bulk-delete', async (route) => {
		const body = (await route.request().postDataJSON()) as BulkBody;
		dryRuns.push(body);
		await route.fulfill({
			json: {
				dry_run: true,
				results: [
					{
						strategy_id: strategyId,
						name: 'Recovered BTC trend draft',
						outcome: 'would_delete',
						code: null,
						message: null,
						deployment_ids: [],
						counts: {
							...zeroCounts,
							backtests: 4,
							snapshots: 2,
							paper_deployments: 1,
							live_deployments_kept: 1
						}
					}
				],
				deleted: 0,
				blocked: 0,
				not_found: 0,
				failed: 0
			}
		});
	});
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		expect(route.request().method()).toBe('DELETE');
		deleted = true;
		await route.fulfill({
			json: {
				strategy_id: strategyId,
				name: 'Recovered BTC trend draft',
				outcome: 'deleted',
				counts: zeroCounts,
				risk_policy_republished: false
			}
		});
	});
	page.on('dialog', () => {
		throw new Error('window.confirm must not be used');
	});
	await page.goto('/strategies');
	await page.getByRole('button', { name: 'Delete Recovered BTC trend draft…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Delete Recovered BTC trend draft?' });
	await expect(dialog.getByTestId('delete-preview')).toContainText('4 backtests');
	await expect(dialog.getByTestId('delete-preview')).toContainText(
		'1 stopped live bot kept with full history'
	);
	await expect(dialog).toContainText('Live history is kept');
	await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
	expect(dryRuns).toEqual([{ strategy_ids: [strategyId], confirm: false, dry_run: true }]);
	await dialog.getByRole('button', { name: 'Delete 1 strategy' }).click();
	await expect.poll(() => deleted).toBe(true);
	await expect(dialog).toBeHidden();
	await expect(page.getByTestId('delete-results')).toContainText('1 deleted');
	await expect(page.getByText('No strategies yet.')).toBeVisible();
});

test('bulk delete selects the page, blocks running bots, and reports partial results', async ({
	page
}) => {
	const third = {
		...libraryEntry,
		strategy_id: '01985cf0-7b60-7000-8000-00000000000a',
		name: 'Third'
	};
	const confirmed: BulkBody[] = [];
	await mockLibrary(page, [publishedEntry, secondStrategyEntry, third]);
	await page.route('**/api/v1/strategies/bulk-delete', async (route) => {
		const body = (await route.request().postDataJSON()) as BulkBody;
		if (body.dry_run) {
			await route.fulfill({
				json: {
					dry_run: true,
					results: [
						{
							strategy_id: strategyId,
							name: publishedEntry.name,
							outcome: 'would_delete',
							code: null,
							message: null,
							deployment_ids: [],
							counts: { ...zeroCounts, backtests: 1 }
						},
						{
							strategy_id: secondStrategyEntry.strategy_id,
							name: 'Second strategy',
							outcome: 'blocked',
							code: 'strategy_has_active_deployments',
							message: 'Stop running bots first.',
							deployment_ids: ['01985cf0-7b60-7000-8000-000000000222'],
							counts: null
						},
						{
							strategy_id: third.strategy_id,
							name: 'Third',
							outcome: 'would_delete',
							code: null,
							message: null,
							deployment_ids: [],
							counts: zeroCounts
						}
					],
					deleted: 0,
					blocked: 1,
					not_found: 0,
					failed: 0
				}
			});
			return;
		}
		confirmed.push(body);
		await route.fulfill({
			json: {
				dry_run: false,
				results: [
					{
						strategy_id: strategyId,
						name: publishedEntry.name,
						outcome: 'deleted',
						code: null,
						message: null,
						deployment_ids: [],
						counts: zeroCounts
					},
					{
						strategy_id: third.strategy_id,
						name: 'Third',
						outcome: 'failed',
						code: 'strategy_delete_failed',
						message: 'Strategy storage is unavailable.',
						deployment_ids: [],
						counts: null
					}
				],
				deleted: 1,
				blocked: 0,
				not_found: 0,
				failed: 1
			}
		});
	});
	await page.goto('/strategies');
	await expect(page.getByTestId('bulk-bar')).toHaveCount(0);
	await page.getByTestId('select-all').check();
	await expect(page.getByTestId('bulk-bar')).toContainText('3 selected');
	await page.getByLabel('Select Third').uncheck();
	await expect(page.getByTestId('select-all')).not.toBeChecked();
	await page.getByLabel('Select Third').check();
	await expect(page.getByTestId('select-all')).toBeChecked();
	await page.getByRole('button', { name: 'Delete 3 strategies…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Delete 3 strategies?' });
	const preview = dialog.getByTestId('delete-preview');
	await expect(preview.locator('[data-outcome="blocked"]')).toContainText(
		'Blocked: 1 running or paused bot. Stop it first.'
	);
	await expect(preview.getByRole('link', { name: 'Open bot' })).toHaveAttribute(
		'href',
		'/deployments/01985cf0-7b60-7000-8000-000000000222'
	);
	await expect(dialog).toContainText('1 of 3 will be skipped');
	await dialog.getByRole('button', { name: 'Delete 2 strategies' }).click();
	await expect.poll(() => confirmed.length).toBe(1);
	expect(confirmed[0]).toEqual({
		strategy_ids: [strategyId, third.strategy_id],
		confirm: true,
		dry_run: false
	});
	const results = page.getByTestId('delete-results');
	await expect(results).toContainText('1 deleted · 2 not deleted');
	await expect(results.locator('[data-outcome="failed"]')).toContainText(
		'Failed: Strategy storage is unavailable.'
	);
	await expect(results.locator('[data-outcome="blocked"]')).toContainText('Second strategy');
});

test('nothing deletable disables confirm and explains why', async ({ page }) => {
	await mockLibrary(page, [publishedEntry]);
	await page.route('**/api/v1/strategies/bulk-delete', (route) =>
		route.fulfill({
			json: {
				dry_run: true,
				results: [
					{
						strategy_id: strategyId,
						name: publishedEntry.name,
						outcome: 'blocked',
						code: 'strategy_has_active_deployments',
						message: null,
						deployment_ids: ['a', 'b'],
						counts: null
					}
				],
				deleted: 0,
				blocked: 1,
				not_found: 0,
				failed: 0
			}
		})
	);
	await page.goto('/strategies');
	await page.getByRole('button', { name: 'Delete Recovered BTC trend draft…' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText('Blocked: 2 running or paused bots. Stop them first.');
	await expect(dialog.getByRole('button', { name: 'Delete 0 strategies' })).toBeDisabled();
	await expect(dialog).toContainText('Nothing selected can be deleted right now.');
});

test('cancelling or escaping the delete dialog deletes nothing', async ({ page }) => {
	let confirmCalls = 0;
	await mockLibrary(page, [publishedEntry]);
	await page.route('**/api/v1/strategies/bulk-delete', async (route) => {
		const body = (await route.request().postDataJSON()) as BulkBody;
		if (!body.dry_run) confirmCalls += 1;
		await route.fulfill({
			json: {
				dry_run: true,
				results: [
					{
						strategy_id: strategyId,
						name: publishedEntry.name,
						outcome: 'would_delete',
						code: null,
						message: null,
						deployment_ids: [],
						counts: zeroCounts
					}
				],
				deleted: 0,
				blocked: 0,
				not_found: 0,
				failed: 0
			}
		});
	});
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		confirmCalls += 1;
		await route.fulfill({ status: 500, json: {} });
	});
	await page.goto('/strategies');
	const trigger = page.getByRole('button', { name: 'Delete Recovered BTC trend draft…' });
	await trigger.click();
	await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
	await expect(page.getByRole('dialog')).toBeHidden();
	await expect(trigger).toBeFocused();
	await trigger.click();
	await expect(page.getByRole('dialog').getByTestId('delete-preview')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toBeHidden();
	expect(confirmCalls).toBe(0);
	await expect(page.locator(`tr[data-strategy-id="${strategyId}"]`)).toBeVisible();
});

test('imports a pasted strategy definition as a new strategy and opens it', async ({ page }) => {
	await mockLibrary(page, [libraryEntry]);
	let importedBody: unknown = null;
	const importedId = '01985cf0-7b60-7000-8000-0000000000ff';
	const imported = record({ ...draft, strategy_id: importedId });
	await page.route('**/api/v1/strategies/import', async (route) => {
		importedBody = await route.request().postDataJSON();
		await route.fulfill({ status: 201, json: imported });
	});
	await page.route(`**/api/v1/strategies/${importedId}`, (route) =>
		route.fulfill({ json: imported })
	);
	await page.goto('/strategies');
	await page.waitForSelector('table tbody tr');
	await page.getByRole('button', { name: 'Import JSON…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Import strategy JSON' });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByRole('button', { name: 'Import strategy' })).toBeDisabled();
	await page.getByLabel('Strategy definition JSON').fill(JSON.stringify(draft));
	await dialog.getByRole('button', { name: 'Import strategy' }).click();
	await expect.poll(() => importedBody).not.toBeNull();
	expect((importedBody as { document: { strategy_id: string } }).document.strategy_id).toBe(
		strategyId
	);
	await expect(page).toHaveURL(new RegExp(`/strategies/${importedId}$`));
});

test('rejects invalid import JSON without leaving the dialog or sending a request', async ({
	page
}) => {
	await mockLibrary(page, []);
	let importCalls = 0;
	await page.route('**/api/v1/strategies/import', async (route) => {
		importCalls += 1;
		await route.fulfill({ status: 201, json: record() });
	});
	await page.goto('/strategies');
	await expect(page.getByText('No strategies yet.')).toBeVisible();
	await page.getByRole('button', { name: 'Import JSON…' }).click();
	await page.getByLabel('Strategy definition JSON').fill('{not json');
	await page.getByRole('button', { name: 'Import strategy' }).click();
	await expect(page.getByRole('dialog').getByRole('alert')).toContainText('not valid JSON');
	await expect(page.getByLabel('Strategy definition JSON')).toBeVisible();
	expect(importCalls).toBe(0);
});

test('surfaces a controlled error banner when the library cannot load', async ({ page }) => {
	await page.route(isStrategyLibraryRequest, async (route) =>
		route.fulfill({ status: 503, json: { detail: 'Strategy lifecycle storage is unavailable.' } })
	);
	await page.goto('/strategies');
	await expect(page.getByRole('alert')).toContainText('Strategy lifecycle storage is unavailable.');
	await expect(page.getByRole('button', { name: 'Retry library load' })).toBeVisible();
});

test('the template picker offers every research template', async ({ page }) => {
	await mockLibrary(page, [libraryEntry]);
	await page.goto('/strategies');
	const picker = page.getByLabel('Template');
	await expect(picker).toHaveValue('ema-trend');
	await expect(picker.locator('option')).toHaveText([
		'EMA trend',
		'RSI mean reversion',
		'MACD trend',
		'Bollinger mean reversion',
		'Donchian breakout',
		'Supertrend trend',
		'Squeeze breakout',
		'Z-score mean reversion',
		'EMA trend hold',
		'BTC regime gate'
	]);
	await picker.selectOption('donchian-breakout');
	await expect(picker).toHaveValue('donchian-breakout');
});

test('a tag chip filters the library by metadata tag and the filter chip clears it', async ({
	page
}) => {
	const requested: string[] = [];
	const tagged = { ...libraryEntry, tags: ['per-market', 'majors'] };
	await page.route(isStrategyLibraryRequest, async (route) => {
		const url = new URL(route.request().url());
		requested.push(url.search);
		const tag = url.searchParams.get('tag');
		await route.fulfill({
			json: {
				strategies: tag === null ? [tagged, secondStrategyEntry] : [tagged],
				has_more: false,
				next_cursor: null
			}
		});
	});
	await page.goto('/strategies');
	await expect(page.locator('tbody tr')).toHaveCount(2);
	await page.getByTestId('library-tag-chip').filter({ hasText: 'per-market' }).click();
	await expect.poll(() => requested.at(-1)).toBe('?limit=10&tag=per-market&origin=operator');
	await expect(page.locator('tbody tr')).toHaveCount(1);
	await expect(page.getByTestId('library-tag-filter')).toContainText('per-market');
	await page.getByRole('button', { name: 'Clear the tag filter per-market' }).click();
	await expect.poll(() => requested.at(-1)).toBe('?limit=10&origin=operator');
	await expect(page.locator('tbody tr')).toHaveCount(2);
});

test('the library opens on Mine, splits out research, and remembers the view', async ({ page }) => {
	const requested: string[] = [];
	const research = {
		...secondStrategyEntry,
		name: 'Sweep candidate 7',
		tags: ['claude-research', 'research-sweep']
	};
	await page.route(isStrategyLibraryRequest, async (route) => {
		const url = new URL(route.request().url());
		requested.push(url.search);
		const origin = url.searchParams.get('origin');
		const rows =
			origin === 'operator'
				? [libraryEntry]
				: origin === 'research'
					? [research]
					: [research, libraryEntry];
		await route.fulfill({
			json: { strategies: rows, total: rows.length, has_more: false, next_cursor: null }
		});
	});
	await page.goto('/strategies');
	const views = page.getByTestId('library-origin');
	await expect(views.getByRole('button', { name: 'Mine' })).toHaveAttribute('aria-pressed', 'true');
	await expect(page.locator('tbody tr')).toHaveCount(1);
	await expect(page.getByTestId('library-total')).toHaveText('1 strategy');
	await views.getByRole('button', { name: 'Research' }).click();
	await expect.poll(() => requested.at(-1)).toBe('?limit=10&origin=research');
	await expect(page.locator(`tr[data-strategy-id="${research.strategy_id}"]`)).toBeVisible();
	await page.getByTestId('library-tag-chip').filter({ hasText: 'research-sweep' }).click();
	await expect.poll(() => requested.at(-1)).toBe('?limit=10&tag=research-sweep&origin=research');
	await page.reload();
	await expect(views.getByRole('button', { name: 'Research' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect.poll(() => requested.at(-1)).toBe('?limit=10&origin=research');
	await views.getByRole('button', { name: 'All' }).click();
	await expect.poll(() => requested.at(-1)).toBe('?limit=10');
	await expect(page.locator('tbody tr')).toHaveCount(2);
});

test('an empty Mine view points at Research instead of looking empty', async ({ page }) => {
	await page.route(isStrategyLibraryRequest, async (route) => {
		const origin = new URL(route.request().url()).searchParams.get('origin');
		const rows = origin === 'operator' ? [] : [libraryEntry];
		await route.fulfill({ json: { strategies: rows, total: rows.length, has_more: false } });
	});
	await page.goto('/strategies');
	await expect(page.getByTestId('library-empty-mine')).toContainText('No strategies yet');
	await page.getByTestId('library-empty-mine').getByRole('button', { name: 'Research' }).click();
	await expect(page.locator('tbody tr')).toHaveCount(1);
});
