import { expect, test } from '../e2e/harness';
import {
	definition,
	deployment,
	fingerprint,
	fingerprintV2,
	invalidRecord,
	mockBacktestList,
	mockDatasets,
	mockDeployments,
	mockStrategy,
	strategyId,
	strategyRecord
} from '../e2e/workspace-fixtures';

test('identity bar shows validity and the next snapshot fingerprint; stages have no version', async ({
	page
}) => {
	await mockStrategy(page);
	await mockBacktestList(page);
	await mockDatasets(page);
	await mockDeployments(page, () => []);
	await page.goto(`/strategies/${strategyId}/test`);

	await expect(page.getByTestId('workspace-name')).toHaveText('Recovered BTC trend draft');
	await expect(page.getByTestId('workspace-validity')).toHaveText(/Valid rules/);
	await expect(page.getByTestId('workspace-fingerprint')).toHaveText(/sha256:aaaa…aaaa/);
	await expect(page.getByTestId('workspace-market')).toHaveText('BTC / USDC');
	await expect(page.getByTestId('workspace-clock')).toHaveText('1h');
	await expect(page.getByText('Coinbase product BTC-USDC')).toBeVisible();
	await expect(page.getByTestId('workspace-save-state')).toContainText('Revision 1');
	await expect(page.getByRole('button', { name: 'Copy full fingerprint' })).toBeVisible();
	await expect(page.getByTestId('workspace-version-picker')).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Versions' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: /Publish/ })).toHaveCount(0);

	const stages = page.getByRole('navigation', { name: 'Strategy stages' });
	await expect(page.getByRole('tablist')).toHaveCount(0);
	await expect(stages.getByRole('link', { name: 'Test' })).toHaveAttribute('aria-current', 'page');
	await expect(page.getByTestId('breadcrumb')).toHaveText(/Strategies\s*\/\s*Test/);
	await stages.getByRole('link', { name: 'Run' }).click();
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}/run$`));
	await expect(stages.getByRole('link', { name: 'Run' })).toHaveAttribute('aria-current', 'page');
});

test('an invalid saved definition shows its problems in the identity bar', async ({ page }) => {
	await mockStrategy(page, { record: invalidRecord() });
	await page.goto(`/strategies/${strategyId}`);
	await expect(page.getByTestId('workspace-validity')).toHaveText(/1 problem/);
	await expect(page.getByTestId('workspace-fingerprint')).toHaveCount(0);
});

test('old ?version= links drop the parameter and open the current strategy', async ({ page }) => {
	await mockStrategy(page);
	await mockDeployments(page, () => []);
	await page.goto(`/strategies/${strategyId}/run?version=${encodeURIComponent(fingerprint)}`);
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}/run$`));
	await expect(page.getByTestId('workspace-name')).toHaveText('Recovered BTC trend draft');
});

test('old fingerprint deep links resolve to the owning strategy', async ({ page }) => {
	await mockStrategy(page);
	await mockBacktestList(page);
	await mockDatasets(page);
	await page.goto(`/strategies/${encodeURIComponent(fingerprintV2)}/test`);
	await expect(page).toHaveURL(new RegExp(`/strategies/${strategyId}/test$`));
	await expect(page.getByTestId('workspace-name')).toHaveText('Recovered BTC trend draft');
});

test('a fingerprint link whose strategy was deleted says so', async ({ page }) => {
	const orphan = `sha256:${'d'.repeat(64)}`;
	await page.route(`**/api/v1/strategies/snapshots/${encodeURIComponent(orphan)}`, (route) =>
		route.fulfill({
			json: {
				strategy_fingerprint: orphan,
				strategy_id: null,
				strategy_name: 'Old breakout',
				strategy: definition,
				created_at: '2026-09-01T00:00:00Z',
				is_current: false
			}
		})
	);
	await page.goto(`/strategies/${encodeURIComponent(orphan)}`);
	const note = page.getByTestId('workspace-legacy-link');
	await expect(note).toContainText('Old breakout (deleted strategy)');
	await expect(note).toContainText('live history is kept');
});

test('Clone copies the strategy into a new workspace', async ({ page }) => {
	const cloneId = '01985cf0-7b60-7000-8000-000000000099';
	let cloned = false;
	await mockStrategy(page);
	const copy = strategyRecord({
		strategy_id: cloneId,
		name: 'Copy of trend',
		document: { ...definition, strategy_id: cloneId, name: 'Copy of trend' },
		strategy: { ...definition, strategy_id: cloneId, name: 'Copy of trend' },
		current_fingerprint: `sha256:${'9'.repeat(64)}`
	});
	await page.route(`**/api/v1/strategies/${cloneId}`, (route) => route.fulfill({ json: copy }));
	await page.route(`**/api/v1/strategies/${strategyId}/clone`, async (route) => {
		cloned = route.request().method() === 'POST';
		await route.fulfill({ status: 201, json: copy });
	});
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Clone' }).click();
	await expect.poll(() => cloned).toBe(true);
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
	const earlierEdit = deployment({
		id: '01985cf0-7b60-7000-8000-000000000444',
		strategy_fingerprint: fingerprintV2,
		status: 'stopped'
	});
	await mockDeployments(page, () => [paper, live, earlierEdit]);
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
	await expect(signals).toHaveCount(3);
	const badges = page.getByTestId('rules-badge');
	await expect(badges.nth(0)).toContainText('Current rules');
	await expect(badges.nth(2)).toContainText('Earlier edit');
	await expect(signals.first()).toContainText('No trade — conditions did not match');
	await expect(signals.nth(1)).toContainText('No trade — conditions could not be evaluated');
	const reason = page.getByTestId('trade-reason');
	await expect(reason).toContainText('Entry');
	await expect(reason).toContainText('risk allow (within_limits)');
	await expect(reason).toContainText('filled · 1 fill');
	await expect(reason).toContainText('No operator note.');
	await expect(page.getByTestId('no-trade-reasons').first()).toContainText(
		'No recorded trade rationale for this deployment'
	);
	expect(requested.sort()).toEqual([live.id, paper.id, earlierEdit.id].sort());
	await expect(page.getByRole('link', { name: 'Open bot →' }).first()).toHaveAttribute(
		'href',
		`/deployments/${paper.id}`
	);
});
