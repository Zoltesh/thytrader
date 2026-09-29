import { expect, isStrategyLibraryRequest, test } from '../e2e/harness';

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
	summary: 'BTC-USD · 1h · EMA(20) crosses above EMA(50) AND RSI(14) ≥ 50 · 0.5% risk · $10-$100',
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
	await page.route(isStrategyLibraryRequest, async (route) => {
		if (route.request().method() !== 'GET') {
			await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
			return;
		}
		await route.fulfill({ json: { strategies: entries } });
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
	await expect.poll(() => requested).toEqual(['?limit=10']);
	await page.getByRole('button', { name: 'Next strategy page' }).click();
	await expect(
		page.locator(`tr[data-strategy-id="${secondStrategyEntry.strategy_id}"]`)
	).toBeVisible();
	await expect.poll(() => requested).toEqual(['?limit=10', '?limit=10&cursor=next']);
	await page.getByRole('button', { name: 'Previous strategy page' }).click();
	await expect(page.locator(`tr[data-strategy-id="${strategyId}"]`)).toBeVisible();
	await page.getByTestId('strategy-page-size').selectOption('25');
	await expect(page.getByTestId('strategy-page-size')).toHaveValue('25');
	await expect.poll(() => requested.at(-1)).toBe('?limit=25');
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

test('rows show market, latest version, an evidence pipeline, and the latest backtest', async ({
	page
}) => {
	const draftOverPublished = {
		...secondStrategyEntry,
		name: 'Draft over published',
		status: 'draft',
		latest_version: 2,
		published_versions: [{ version: 1, strategy_fingerprint: fingerprint }],
		latest_fingerprint: fingerprint,
		paper_live: { paper: 'running', live: 'unavailable' }
	};
	await mockLibrary(page, [publishedEntry, draftOverPublished]);
	await page.goto('/strategies');
	const table = page.getByRole('table', { name: 'Strategies' });
	for (const header of ['Strategy', 'Market', 'Latest', 'Progress', 'Latest backtest', 'Updated']) {
		await expect(table.getByRole('columnheader', { name: header, exact: true })).toBeVisible();
	}
	// The clipped "Paper / live" column is replaced by the pipeline.
	await expect(table.getByRole('columnheader', { name: /Paper \/ live/ })).toHaveCount(0);
	const first = page.locator(`tr[data-strategy-id="${strategyId}"]`);
	await expect(first).toContainText('BTC / USD · 1h');
	await expect(first).toContainText('sha256:aaaa…aaaa');
	await expect(first.locator('.pill')).toHaveText('v1');
	await expect(first).toContainText(
		'Build: published; Test: has a backtest; Paper: not deployed; Live: not deployed'
	);
	await expect(first.getByRole('link', { name: /Latest backtest 8\.50%, 5 trades/ })).toBeVisible();
	const second = page.locator(`tr[data-strategy-id="${secondStrategyEntry.strategy_id}"]`);
	await expect(second.locator('.pill')).toHaveText('v1 · draft v2');
	await expect(second).toContainText('Build: draft open · published earlier');
	await expect(second).toContainText('Paper: running');
	await expect(second.getByTestId('library-pipeline').locator('.step.paper')).toHaveText('Paper');
});

test('a row opens the strategy workspace', async ({ page }) => {
	await mockLibrary(page, [publishedEntry]);
	await page.route(`**/api/v1/strategies/${strategyId}/history`, (route) =>
		route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 1,
				next_version: 2,
				versions: [
					{
						version: 1,
						strategy_fingerprint: fingerprint,
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
	await page.route('**/api/v1/strategies/source/*', (route) =>
		route.fulfill({ json: { strategy: { ...draft, status: 'published' } } })
	);
	await page.goto('/strategies');
	await expect(page.getByRole('link', { name: 'Recovered BTC trend draft' })).toHaveAttribute(
		'href',
		`/strategies/${strategyId}`
	);
	await page.locator(`tr[data-strategy-id="${strategyId}"] td`).nth(1).click();
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
		`/strategies/${strategyId}/test?version=${encodeURIComponent(fingerprint)}&result=${encodeURIComponent(`sha256:${'b'.repeat(64)}`)}`
	);
});

test('creates a reference strategy from the chosen template and clock', async ({ page }) => {
	let createUrl = '';
	await page.route(isStrategyLibraryRequest, async (route) => {
		if (route.request().method() === 'POST') {
			createUrl = route.request().url();
			await route.fulfill({ status: 201, json: { strategy: draft, revision: 1 } });
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
	await expect(page.getByRole('link', { name: 'Recovered BTC trend draft' })).toBeVisible();
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
	await page.getByRole('button', { name: 'Clone Recovered BTC trend draft' }).click();
	await expect.poll(() => cloneFingerprint).toBe(fingerprint);
	await expect(page).toHaveURL(/\/strategies$/);
});

test('draft-only rows offer no clone or archive', async ({ page }) => {
	await mockLibrary(page, [libraryEntry]);
	await page.goto('/strategies');
	const row = page.locator(`tr[data-strategy-id="${strategyId}"]`);
	await expect(row.locator('.pill')).toHaveText('Draft v1');
	await expect(row.getByRole('button')).toHaveCount(0);
});

test('archive confirms in an accessible dialog, then refreshes the library', async ({ page }) => {
	const archivedEntry = { ...publishedEntry, status: 'archived', archived: true };
	let archived = false;
	await page.route(isStrategyLibraryRequest, (route) =>
		route.fulfill({ json: { strategies: [archived ? archivedEntry : publishedEntry] } })
	);
	await page.route('**/api/v1/strategies/*/archive', async (route) => {
		archived = true;
		await route.fulfill({
			json: { strategy_fingerprint: fingerprint, archived_at: '2026-08-28T12:00:00Z' }
		});
	});
	page.on('dialog', () => {
		throw new Error('window.confirm must not be used');
	});
	await page.goto('/strategies');
	const trigger = page.getByRole('button', { name: 'Archive Recovered BTC trend draft…' });
	await trigger.click();
	const dialog = page.getByRole('dialog', { name: 'Archive Recovered BTC trend draft?' });
	await expect(dialog).toContainText('v1');
	await expect(dialog).toContainText(fingerprint);
	await expect(dialog).toContainText(
		'hides the latest published fingerprint from active selection'
	);
	await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
	await dialog.getByRole('button', { name: 'Archive version' }).click();
	await expect(page.locator('.pill[data-status="archived"]')).toHaveText('Archived v1');
	await expect(dialog).toBeHidden();
});

test('archive on a later page refreshes that page, falling back if it becomes empty', async ({
	page
}) => {
	let archived = false;
	const cursors: (string | null)[] = [];
	await page.route(isStrategyLibraryRequest, async (route) => {
		const cursor = new URL(route.request().url()).searchParams.get('cursor');
		cursors.push(cursor);
		await route.fulfill({
			json:
				cursor === null
					? {
							strategies: [libraryEntry],
							has_more: !archived,
							next_cursor: archived ? null : 'next'
						}
					: { strategies: archived ? [] : [publishedEntry], has_more: false }
		});
	});
	await page.route('**/api/v1/strategies/*/archive', async (route) => {
		archived = true;
		await route.fulfill({
			json: { strategy_fingerprint: fingerprint, archived_at: '2026-08-28T12:00:00Z' }
		});
	});
	await page.goto('/strategies');
	await expect(page.locator('tbody tr')).toHaveCount(1);
	await page.getByRole('button', { name: 'Next strategy page' }).click();
	await expect(page.getByTestId('strategy-page-range')).toHaveText('Page 2');
	await page.getByRole('button', { name: /^Archive .*…$/ }).click();
	await page.getByRole('button', { name: 'Archive version' }).click();
	await expect(page.getByTestId('strategy-page-range')).toHaveText('Page 1');
	await expect(page.locator(`tr[data-strategy-id="${strategyId}"]`)).toBeVisible();
	expect(cursors).toEqual([null, 'next', 'next', null]);
});

test('cancelling or escaping the archive dialog archives nothing', async ({ page }) => {
	let archiveCalls = 0;
	await mockLibrary(page, [publishedEntry]);
	await page.route('**/api/v1/strategies/*/archive', async (route) => {
		archiveCalls += 1;
		await route.fulfill({ json: { strategy_fingerprint: fingerprint, archived_at: null } });
	});
	await page.goto('/strategies');
	const trigger = page.getByRole('button', { name: 'Archive Recovered BTC trend draft…' });
	await trigger.click();
	await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
	await expect(page.getByRole('dialog')).toBeHidden();
	await expect(trigger).toBeFocused();
	await trigger.click();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toBeHidden();
	expect(archiveCalls).toBe(0);
	await expect(page.locator('.pill[data-status="published"]')).toBeVisible();
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
	const dialog = page.getByRole('dialog', { name: 'Import strategy JSON' });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByRole('button', { name: 'Import draft' })).toBeDisabled();
	await page.getByLabel('Strategy definition JSON').fill(JSON.stringify(draft));
	await dialog.getByRole('button', { name: 'Import draft' }).click();
	await expect.poll(() => importedBody).not.toBeNull();
	expect((importedBody as { strategy: { strategy_id: string } }).strategy.strategy_id).toBe(
		strategyId
	);
	await expect(dialog).toBeHidden();
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
	await expect(page.getByText('No strategies yet.')).toBeVisible();
	await page.getByRole('button', { name: 'Import JSON…' }).click();
	await page.getByLabel('Strategy definition JSON').fill('{not json');
	await page.getByRole('button', { name: 'Import draft' }).click();
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
