import {
	EARLIER_INTENT,
	ENTRY_INTENT,
	barDecision,
	decisionPageBody,
	mockTradeReasons,
	tradeReason
} from '../e2e/decision-fixtures';
import { expect, test } from '../e2e/harness';
import {
	definition,
	backtestEntry,
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

test('keeps the Test result table inside the mobile viewport', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await mockStrategy(page);
	await mockBacktestList(page, () => [backtestEntry(fingerprint)]);
	await mockDatasets(page);
	await mockDeployments(page, () => []);
	await page.goto(`/strategies/${strategyId}/test`);
	await expect(
		page
			.getByRole('table', { name: 'Backtest results for this strategy' })
			.locator('tbody tr')
			.first()
	).toBeVisible();
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
	const table = page.locator('.table-wrap');
	await expect
		.poll(() =>
			table.evaluate((element) => {
				element.scrollLeft = element.scrollWidth;
				return element.scrollLeft;
			})
		)
		.toBeGreaterThan(0);
	await expect(table.getByRole('columnheader', { name: 'Open' })).toHaveCount(1);
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

test('Why shows the decision timeline of every bot with a deployment selector', async ({
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
	const btc = { product_id: 'BTC-USDC', timeframe: '1h', strategy_id: strategyId };
	const reasonRequests = await mockTradeReasons(page, (id) =>
		id === paper.id
			? [
					tradeReason(paper.id, ENTRY_INTENT, { product_id: 'BTC-USDC', notes: [] }),
					tradeReason(paper.id, EARLIER_INTENT, {
						id: 'reason-old',
						created_at: '2026-09-01T10:00:05Z',
						product_id: 'BTC-USDC',
						notes: []
					})
				]
			: []
	);
	const rows = [
		barDecision(paper.id, {
			...btc,
			bar_starts_at: '2026-09-29T13:00:00Z',
			bar_closes_at: '2026-09-29T14:00:00Z',
			evaluated_at: '2026-09-29T14:00:02Z',
			outcome: 'entry_signal',
			reason_code: 'SIGNAL_MATCHED',
			summary: 'Entry: fast EMA crossed above slow EMA',
			action: 'intent_created',
			intent_id: ENTRY_INTENT
		}),
		barDecision(live.id, {
			...btc,
			mode: 'live',
			bar_starts_at: '2026-09-29T12:00:00Z',
			bar_closes_at: '2026-09-29T13:00:00Z',
			evaluated_at: '2026-09-29T13:00:02Z',
			outcome: 'error',
			reason_code: 'DATA_GAP',
			summary: 'Could not evaluate: market data gap'
		}),
		barDecision(earlierEdit.id, {
			...btc,
			strategy_fingerprint: fingerprintV2,
			bar_starts_at: '2026-09-29T11:00:00Z',
			bar_closes_at: '2026-09-29T12:00:00Z',
			evaluated_at: '2026-09-29T12:00:02Z'
		})
	];
	const decisionRequests: URLSearchParams[] = [];
	await page.route(
		(url) => url.pathname === `/api/v1/strategies/${strategyId}/decisions`,
		(route) => {
			const params = new URL(route.request().url()).searchParams;
			decisionRequests.push(params);
			const selected = params.get('deployment_id');
			const outcomes = params.getAll('outcome');
			const matching = rows.filter(
				(row) =>
					(selected === null || row.deployment_id === selected) &&
					(outcomes.length === 0 || outcomes.includes(String(row.outcome)))
			);
			return route.fulfill({
				json: { strategy_id: strategyId, deployment_id: selected, ...decisionPageBody(matching) }
			});
		}
	);

	await page.goto(`/strategies/${strategyId}/why`);
	await expect(page.getByText(/not recorded yet/)).toHaveCount(0);
	await expect(page.getByTestId('decision-retention-note')).toContainText(
		"each bot's newest 20,000 decisions, up to 180 days"
	);

	// Selector: All bots, then each bot with its rules, latest bar, and next evaluation.
	const selector = page.getByTestId('decision-deployment-selector');
	const options = selector.getByTestId('decision-deployment-option');
	await expect(options).toHaveCount(4);
	await expect(options.first()).toHaveText(/All bots\s*3/);
	await expect(options.first()).toHaveAttribute('aria-pressed', 'true');
	await expect(options.nth(2)).toContainText('LIVE');
	const badges = selector.getByTestId('rules-badge');
	await expect(badges.nth(0)).toContainText('Current rules');
	await expect(badges.nth(2)).toContainText('Earlier edit');
	const signals = selector.getByTestId('latest-signal');
	await expect(signals).toHaveCount(3);
	await expect(signals.first()).toContainText('No trade — conditions did not match');
	await expect(signals.nth(1)).toContainText('No trade — conditions could not be evaluated');
	const nextEvaluation = selector.getByTestId('bot-next-evaluation');
	await expect(nextEvaluation.first()).toHaveText('Next evaluation ≈ 2026-09-29 16:00 UTC');
	await expect(nextEvaluation.nth(2)).toHaveText(
		'Stopped: bars are evaluated only while residual exposure remains'
	);
	await expect(selector.getByRole('link', { name: /Open paper bot/ }).first()).toHaveAttribute(
		'href',
		`/deployments/${paper.id}`
	);

	// All bots: the strategy endpoint without deployment_id; each row names its bot.
	const timeline = page.getByTestId('why-decisions');
	await expect(timeline.getByTestId('decision-row')).toHaveCount(3);
	expect(decisionRequests[0]?.has('deployment_id')).toBe(false);
	expect(decisionRequests[0]?.get('limit')).toBe('50');
	await expect(timeline.getByTestId('decision-bot')).toHaveText([
		'Paper · BTC / USDC · since 2026-09-24',
		'LIVE · BTC / USDC · since 2026-09-24',
		'Paper · BTC / USDC · since 2026-09-24'
	]);
	// Each bot's trade reasons were read; the linked one joins its row, the other is listed once.
	await expect
		.poll(() => [...reasonRequests].sort())
		.toEqual([live.id, paper.id, earlierEdit.id].sort());
	const earlier = timeline.getByTestId('earlier-trade-reasons');
	await expect(earlier.getByTestId('trade-reason')).toHaveCount(1);
	await expect(earlier.getByTestId('trade-reason')).toHaveAttribute(
		'data-intent-id',
		EARLIER_INTENT
	);
	const entry = timeline.locator('[data-testid="decision-row"][data-outcome="entry_signal"]');
	await entry.getByRole('button', { name: /Entry: fast EMA/ }).click();
	await expect(entry.getByTestId('decision-trade-reason')).toHaveCount(1);
	await expect(timeline.locator(`[data-intent-id="${ENTRY_INTENT}"]`)).toHaveCount(1);

	// Selecting one bot sends its deployment_id and narrows the timeline to it.
	await options.nth(2).click();
	await expect(options.nth(2)).toHaveAttribute('aria-pressed', 'true');
	await expect(options.first()).toHaveAttribute('aria-pressed', 'false');
	await expect.poll(() => decisionRequests.at(-1)?.get('deployment_id')).toBe(live.id);
	await expect(timeline.getByTestId('decision-row')).toHaveCount(1);
	await expect(timeline.getByTestId('decision-outcome')).toHaveText(['Error']);
	await expect(timeline.getByTestId('decision-bot')).toHaveCount(0);
	await expect(page.getByTestId('why-decisions-scope')).toHaveText(
		'LIVE · BTC / USDC · since 2026-09-24'
	);
	await expect(timeline.getByTestId('earlier-trade-reasons')).toHaveCount(0);

	// Filters keep the selected bot.
	await timeline.getByTestId('decision-filter').getByRole('button', { name: 'Trades' }).click();
	await expect
		.poll(() => decisionRequests.at(-1)?.getAll('outcome'))
		.toEqual(['entry_signal', 'exit']);
	expect(decisionRequests.at(-1)?.get('deployment_id')).toBe(live.id);
	await expect(timeline.getByTestId('decision-empty')).toHaveText(
		'No entries or exits in the journaled decision history.'
	);

	// Back to all bots.
	await options.first().click();
	await expect.poll(() => decisionRequests.at(-1)?.has('deployment_id')).toBe(false);
	await expect(timeline.getByTestId('decision-outcome')).toHaveText(['Entry']);
});
