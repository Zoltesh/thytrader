import { expect, test } from '../e2e/harness';
import {
	deployment,
	draft,
	fingerprint,
	fingerprintV2,
	historyVersion,
	mockBacktestList,
	mockDatasets,
	mockDeployments,
	mockStrategy,
	strategyId
} from '../e2e/workspace-fixtures';

const twoVersions = [historyVersion(1, fingerprint), historyVersion(2, fingerprintV2)];

test('identity bar and stage links carry one exact version across stages', async ({ page }) => {
	await mockStrategy(page, { versions: twoVersions, draft: { ...draft, version: 3 } });
	await mockBacktestList(page);
	await mockDatasets(page);
	await mockDeployments(page, () => []);
	await page.goto(`/strategies/${strategyId}/test`);

	await expect(page.getByTestId('workspace-name')).toHaveText('Recovered BTC trend draft');
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Published v2/);
	await expect(page.getByTestId('workspace-market')).toHaveText('BTC / USDC');
	await expect(page.getByTestId('workspace-clock')).toHaveText('1h');
	await expect(page.getByText('Coinbase product BTC-USDC')).toBeVisible();
	await expect(page.getByTestId('workspace-draft-state')).toContainText('Draft v3 open');
	await expect(page.getByRole('button', { name: 'Copy full fingerprint' })).toBeVisible();

	const stages = page.getByRole('navigation', { name: 'Strategy stages' });
	await expect(page.getByRole('tablist')).toHaveCount(0);
	await expect(stages.getByRole('link', { name: 'Test' })).toHaveAttribute('aria-current', 'page');
	await expect(stages.getByRole('link', { name: /Build/ })).toContainText('draft v3');
	await expect(page.getByTestId('breadcrumb')).toHaveText(/Strategies\s*\/\s*Test/);

	// Picking v1 keeps the stage and pins ?version=.
	await page.getByTestId('workspace-version-picker').selectOption(fingerprint);
	await expect(page).toHaveURL(new RegExp(`/test\\?version=${encodeURIComponent(fingerprint)}`));
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Published v1/);

	// Stage links keep the version context.
	await stages.getByRole('link', { name: 'Run' }).click();
	await expect(page).toHaveURL(new RegExp(`/run\\?version=${encodeURIComponent(fingerprint)}`));
	await expect(stages.getByRole('link', { name: 'Run' })).toHaveAttribute('aria-current', 'page');
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Published v1/);

	// Choosing the open draft goes to Build.
	await page.getByTestId('workspace-version-picker').selectOption('draft');
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}$`));
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Draft v3/);
});

test('a version that does not belong to the strategy fails closed on every stage', async ({
	page
}) => {
	await mockStrategy(page, { versions: twoVersions });
	await mockBacktestList(page);
	const posts: string[] = [];
	await page.route('**/api/v1/**', async (route) => {
		if (route.request().method() === 'POST') posts.push(route.request().url());
		await route.fallback();
	});
	await mockDeployments(page, () => [deployment()]);
	const foreign = `sha256:${'d'.repeat(64)}`;
	for (const stage of ['test', 'run', 'why']) {
		await page.goto(`/strategies/${strategyId}/${stage}?version=${foreign}`);
		const alert = page.getByTestId('workspace-invalid-version');
		await expect(alert).toBeVisible();
		await expect(alert).toContainText('does not belong to this strategy');
		await expect(alert).toContainText(foreign);
		await expect(page.getByTestId('workspace-version-pill')).toHaveText('Unknown version');
		await expect(page.getByRole('button', { name: 'Run backtest' })).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Arm live trading…' })).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Start paper deployment…' })).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Pause entries…' })).toHaveCount(0);
	}
	expect(posts.filter((url) => !url.includes('/security/session'))).toEqual([]);
	await page.getByRole('link', { name: 'Open latest version' }).click();
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}/why$`));
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Published v2/);
});

test('Versions lists history, exports, diffs, and revises into the next draft', async ({
	page
}) => {
	const versions = [
		{ ...historyVersion(1, fingerprint), archived: true, archived_at: '2026-08-28T12:00:00Z' },
		historyVersion(2, fingerprintV2)
	];
	let reviseFingerprint = '';
	let revised = false;
	await page.route(`**/api/v1/strategies/${strategyId}/history`, (route) =>
		route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 2,
				next_version: 3,
				versions,
				draft: revised ? { strategy: { ...draft, version: 3 }, revision: 1, summary: '' } : null
			}
		})
	);
	await page.route('**/api/v1/strategies/source/*', (route) => {
		const requested = decodeURIComponent(route.request().url().split('/source/')[1] ?? '');
		return route.fulfill({
			json: {
				strategy: {
					...draft,
					status: 'published',
					name: requested === fingerprint ? 'Recovered BTC trend draft' : 'Renamed trend draft'
				}
			}
		});
	});
	await page.route('**/api/v1/strategies/*/revise', async (route) => {
		const body = (await route.request().postDataJSON()) as { strategy_fingerprint: string };
		reviseFingerprint = body.strategy_fingerprint;
		revised = true;
		await route.fulfill({
			status: 201,
			json: { strategy: { ...draft, version: 3 }, revision: 1, source_fingerprint: '', summary: '' }
		});
	});

	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByTestId('workspace-draft-state')).toContainText('No editable draft');
	await page.getByRole('button', { name: 'Versions' }).click();
	const dialog = page.getByRole('dialog', { name: 'Versions' });
	await expect(dialog).toBeVisible();
	const historyTable = dialog.getByRole('table', { name: 'Published version history' });
	await expect(historyTable.getByRole('cell', { name: 'V1', exact: true })).toBeVisible();
	await expect(historyTable.getByRole('cell', { name: 'V2', exact: true })).toBeVisible();
	await expect(dialog.getByText(/archived ·/)).toBeVisible();

	const download = page.waitForEvent('download');
	await historyTable.getByRole('button', { name: 'Export' }).first().click();
	expect((await download).suggestedFilename()).toMatch(/-v1\.json$/);

	const diffTable = dialog.getByRole('table', { name: 'Semantic diff' });
	await expect(diffTable.getByRole('cell', { name: 'Strategy name' })).toBeVisible();
	await expect(diffTable.getByText('Renamed trend draft')).toBeVisible();
	await dialog.getByLabel('To version').selectOption('1');
	await expect(dialog.getByText('Select two different versions to compare.')).toBeVisible();

	await historyTable.getByRole('button', { name: 'Edit into next draft' }).first().click();
	await expect.poll(() => reviseFingerprint).toBe(fingerprint);
	await expect(dialog).toBeHidden();
	await expect(page.getByTestId('workspace-draft-state')).toContainText('Draft v3 open');
	await expect(page.getByLabel('Strategy name')).toBeEnabled();
});

test('semantic diff reports a load error only when a version fetch fails', async ({ page }) => {
	await mockStrategy(page, { versions: twoVersions });
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Published v2/);
	await page.route('**/api/v1/strategies/source/*', (route) =>
		route.fulfill({ status: 500, json: { detail: 'source unavailable' } })
	);
	await page.getByRole('button', { name: 'Versions' }).click();
	await expect(
		page.getByText('Could not load the selected versions for comparison.')
	).toBeVisible();
	await expect(page.getByText('Select two different versions to compare.')).toHaveCount(0);
});

test('Revise into new draft from a published-only identity opens the draft in Build', async ({
	page
}) => {
	let revised = false;
	await page.route(`**/api/v1/strategies/${strategyId}/history`, (route) =>
		route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 1,
				next_version: 2,
				versions: [historyVersion(1, fingerprint)],
				draft: revised ? { strategy: { ...draft, version: 2 }, revision: 1, summary: '' } : null
			}
		})
	);
	await page.route('**/api/v1/strategies/source/*', (route) =>
		route.fulfill({ json: { strategy: { ...draft, status: 'published' } } })
	);
	await page.route('**/api/v1/strategies/*/revise', async (route) => {
		revised = true;
		await route.fulfill({
			status: 201,
			json: { strategy: { ...draft, version: 2 }, revision: 1, source_fingerprint: '', summary: '' }
		});
	});
	await page.goto(`/strategies/${strategyId}/test`);
	await page.getByRole('button', { name: 'Revise into new draft' }).click();
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}$`));
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Draft v2/);
});

test('Clone copies the selected published version into a new strategy workspace', async ({
	page
}) => {
	const cloneId = '01985cf0-7b60-7000-8000-000000000099';
	let cloned = '';
	await mockStrategy(page, { versions: twoVersions });
	await page.route(`**/api/v1/strategies/${cloneId}/history`, (route) =>
		route.fulfill({
			json: {
				strategy_id: cloneId,
				latest_version: 1,
				next_version: 1,
				versions: [],
				draft: {
					strategy: { ...draft, strategy_id: cloneId, name: 'Copy of trend' },
					revision: 1,
					summary: ''
				}
			}
		})
	);
	await page.route('**/api/v1/strategies/clone', async (route) => {
		cloned = ((await route.request().postDataJSON()) as { strategy_fingerprint: string })
			.strategy_fingerprint;
		await route.fulfill({
			status: 201,
			json: { strategy: { ...draft, strategy_id: cloneId }, revision: 1, summary: '' }
		});
	});
	await page.goto(`/strategies/${strategyId}?version=${encodeURIComponent(fingerprint)}`);
	await page.getByRole('button', { name: 'Clone' }).click();
	await expect.poll(() => cloned).toBe(fingerprint);
	await expect(page).toHaveURL(new RegExp(`/strategies/${cloneId}$`));
	await expect(page.getByTestId('workspace-name')).toHaveText('Copy of trend');
});

test('Why shows the latest completed-bar signal and trade reasons per deployment', async ({
	page
}) => {
	await mockStrategy(page);
	const paper = deployment();
	const live = deployment({
		id: '01985cf0-7b60-7000-8000-000000000333',
		mode: 'live',
		last_signal: 'undefined'
	});
	const otherVersion = deployment({
		id: '01985cf0-7b60-7000-8000-000000000444',
		strategy_fingerprint: fingerprintV2
	});
	await mockDeployments(page, () => [paper, live, otherVersion]);
	const requested: string[] = [];
	await page.route('**/api/v1/memory/trade-reasons**', (route) => {
		const id = new URL(route.request().url()).searchParams.get('deployment_id') ?? '';
		requested.push(id);
		const records =
			id === paper.id
				? [
						{
							schema_version: 'thytrader-trade-reason-v1',
							id: 'r1',
							created_at: '2026-09-28T22:00:05Z',
							origin: 'runtime',
							intent_id: 'i1',
							deployment_id: paper.id,
							deployment_kind: 'strategy',
							mode: 'paper',
							product_id: 'BTC-USDC',
							purpose: 'entry',
							side: 'buy',
							strategy: null,
							signal: {
								kind: 'strategy_entry',
								last_signal: 'matched',
								candle_starts_at: '2026-09-28T22:00:00Z',
								timeframe: '1h'
							},
							risk: {
								decision: 'allow',
								reason_code: 'within_limits',
								detail: '',
								policy_fingerprint: 'p',
								policy_source: 'published'
							},
							notes: [],
							reconcile: {
								order_id: '0199aaaa-0000-0000-0000-000000000001',
								order_status: 'filled',
								filled_quantity: '0.0041',
								reject_reason: null,
								unknown_timeout: false,
								ledger_available: true,
								fills: [
									{
										fill_id: 'f1',
										price: '63412',
										quantity: '0.0041',
										fee: '0.26',
										filled_at: '2026-09-28T23:00:00Z'
									}
								]
							}
						}
					]
				: [];
		return route.fulfill({ json: { trade_reasons: records } });
	});

	await page.goto(`/strategies/${strategyId}/why`);
	await expect(page.getByTestId('decision-history-note')).toContainText(
		'Full per-bar decision history is not recorded yet'
	);
	const signals = page.getByTestId('latest-signal');
	await expect(signals).toHaveCount(2);
	await expect(signals.first()).toContainText('No trade — conditions did not match');
	await expect(signals.nth(1)).toContainText('No trade — conditions could not be evaluated');
	const reason = page.getByTestId('trade-reason');
	await expect(reason).toContainText('Entry');
	await expect(reason).toContainText('risk allow (within_limits)');
	await expect(reason).toContainText('filled · 1 fill');
	await expect(reason).toContainText('No operator note.');
	await expect(page.getByTestId('no-trade-reasons')).toContainText(
		'No recorded trade rationale for this deployment'
	);
	expect(requested.sort()).toEqual([live.id, paper.id].sort());
	await expect(page.getByRole('link', { name: 'Open bot →' }).first()).toHaveAttribute(
		'href',
		`/deployments/${paper.id}`
	);
});
