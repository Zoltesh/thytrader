/**
 * Run stage of the strategy workspace (absorbs /deploy) plus the /deploy
 * redirect and chooser.
 */
import { expect, test } from '../e2e/harness';
import {
	deployment,
	draft,
	fingerprint,
	fingerprintV2,
	historyVersion,
	mockDeployments,
	mockFees,
	mockPreflight,
	mockStrategy,
	strategyId,
	suggestedFeeProfile
} from '../e2e/workspace-fixtures';

const runStage = `/strategies/${strategyId}/run`;

test('old /deploy links redirect to the Run stage, and an unknown version fails closed', async ({
	page
}) => {
	await mockStrategy(page, {
		versions: [historyVersion(1, fingerprint), historyVersion(2, fingerprintV2)]
	});
	await mockDeployments(page, () => []);
	await mockPreflight(page);
	await page.goto(`/deploy?strategy=${strategyId}&strategy_fingerprint=${fingerprint}`);
	await expect(page).toHaveURL(
		new RegExp(`/strategies/${strategyId}/run\\?version=${encodeURIComponent(fingerprint)}`)
	);
	await expect(page.getByTestId('workspace-version-pill')).toHaveText(/Published v1/);

	await page.goto(`/deploy?strategy=${strategyId}&strategy_fingerprint=sha256:deadbeef`);
	await expect(page.getByTestId('workspace-invalid-version')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Start paper deployment…' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Arm live trading…' })).toHaveCount(0);
});

test('/deploy without a strategy is a chooser that points at the library', async ({ page }) => {
	await page.goto('/deploy');
	await expect(page.getByTestId('stage-chooser')).toContainText('Run stage');
	await expect(page.getByRole('link', { name: 'Open a strategy' })).toBeVisible();
	await expect(
		page.getByRole('navigation', { name: 'Primary navigation' }).getByRole('link', {
			name: 'Strategies'
		})
	).toHaveAttribute('aria-current', 'page');
	await expect(page.getByTestId('breadcrumb')).toHaveText(/Strategies\s*\/\s*Deploy/);
});

test('starts paper through a confirmation with the fee-tier assumptions', async ({ page }) => {
	await mockStrategy(page);
	await mockFees(page, suggestedFeeProfile());
	await mockPreflight(page);
	let created: Record<string, unknown> | null = null;
	const started = deployment();
	await mockDeployments(
		page,
		() => (created ? [started] : []),
		async (body, route) => {
			created = body as Record<string, unknown>;
			await route.fulfill({ status: 201, json: started });
		}
	);
	await page.goto(runStage);
	const paperCard = page.getByTestId('paper-card');
	await expect(paperCard).toContainText('No paper deployment using this version.');
	await expect(paperCard.getByLabel('Paper starting cash (USDC)')).toHaveValue('10000');
	await expect(paperCard.getByLabel('Maker fee rate')).toHaveValue('0.0025');
	await expect(paperCard.getByLabel('Taker fee rate')).toHaveValue('0.0040');
	await expect(
		paperCard.getByText('These are documented paper fill assumptions, not observed Coinbase fees.')
	).toBeVisible();

	await paperCard.getByRole('button', { name: 'Start paper deployment…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Start paper deployment?' });
	await expect(dialog).toContainText('not a promotion of any backtest');
	await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
	await dialog.getByRole('button', { name: 'Start paper deployment' }).click();
	await expect.poll(() => created).not.toBeNull();
	expect(created).toEqual({
		strategy_fingerprint: fingerprint,
		mode: 'paper',
		paper_starting_cash: '10000',
		maker_fee_rate: '0.0025',
		taker_fee_rate: '0.0040'
	});
	const row = paperCard.getByTestId('runtime-row');
	await expect(row).toContainText('Running since 2026-09-24');
	await expect(row).toContainText('Entries enabled');
	await expect(row).toContainText('6 closed trades · net P&L 21.40 USDC · return 2.14%');
	await expect(row).toContainText('long 0.0041 @ 63412 · stop 61902 · target 66432');
	await expect(row).toContainText('maker 0.001 · taker 0.002 (not real Coinbase fees)');
	await expect(row.getByRole('link', { name: /Open paper bot/ })).toHaveAttribute(
		'href',
		`/deployments/${started.id}`
	);
	await expect(paperCard.getByText('Start another paper deployment')).toBeVisible();
});

test('pause and stop use the lifecycle dialog with managed stop or explicit flatten', async ({
	page
}) => {
	await mockStrategy(page);
	await mockPreflight(page);
	const current = deployment();
	await mockDeployments(page, () => [current]);
	const posts: string[] = [];
	await page.route('**/api/v1/deployments/*/*', async (route) => {
		const url = new URL(route.request().url());
		posts.push(`${url.pathname.split('/').at(-1)}${url.search}`);
		const status = url.pathname.endsWith('/pause') ? 'paused' : 'stopped';
		await route.fulfill({ json: { ...current, status } });
	});
	await page.goto(runStage);
	const row = page.getByTestId('paper-card').getByTestId('runtime-row');

	await row.getByRole('button', { name: 'Pause entries…' }).click();
	const pauseDialog = page.getByRole('dialog', { name: 'Pause paper deployment?' });
	await expect(pauseDialog).toContainText('protective orders remain active');
	await expect(pauseDialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(pauseDialog).toHaveCount(0);
	expect(posts).toEqual([]);
	await row.getByRole('button', { name: 'Pause entries…' }).click();
	await page.getByRole('button', { name: 'Pause entries', exact: true }).click();
	await expect.poll(() => posts).toEqual(['pause']);
	await expect(page.getByRole('status').filter({ hasText: 'Deployment paused' })).toBeVisible();

	await row.getByRole('button', { name: 'Stop…' }).click();
	const stopDialog = page.getByRole('dialog', { name: 'Stop paper deployment?' });
	await expect(stopDialog).toContainText('Managed stop — keep protection');
	await stopDialog.getByRole('button', { name: 'Stop with protection' }).click();
	await expect.poll(() => posts).toEqual(['pause', 'stop']);

	await row.getByRole('button', { name: 'Stop…' }).click();
	await page.getByLabel(/Stop and flatten/).check();
	await page.getByRole('button', { name: 'Stop and flatten', exact: true }).click();
	await expect.poll(() => posts).toEqual(['pause', 'stop', 'stop?flatten=true']);
});

test('an incomplete lifecycle contract hides every lifecycle trigger', async ({ page }) => {
	await mockStrategy(page);
	await mockPreflight(page);
	const partial = deployment() as Record<string, unknown>;
	delete partial.lifecycle_command;
	delete partial.worker_lease_held;
	await mockDeployments(page, () => [partial]);
	await page.goto(runStage);
	const row = page.getByTestId('runtime-row');
	await expect(row.getByTestId('lifecycle-contract-note')).toContainText(
		'lifecycle_command, worker_lease_held'
	);
	await expect(row.getByRole('button')).toHaveCount(0);
});

test('arming live needs the real-orders checkbox before i_understand_live is sent', async ({
	page
}) => {
	await mockStrategy(page);
	await mockPreflight(page);
	let body: Record<string, unknown> | null = null;
	const armed = deployment({ mode: 'live', position: null });
	await mockDeployments(
		page,
		() => (body === null ? [] : [armed]),
		async (posted, route) => {
			body = posted as Record<string, unknown>;
			await route.fulfill({ status: 201, json: armed });
		}
	);
	await page.goto(runStage);
	const liveCard = page.getByTestId('live-card');
	await expect(liveCard.getByRole('heading', { name: 'Not running' })).toBeVisible();
	await liveCard.getByRole('button', { name: 'Arm live trading…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Arm live trading?' });
	await expect(dialog).toContainText('places real spot orders on Coinbase');
	await expect(dialog).toContainText('BTC / USDC · 1h');
	await expect(dialog).toContainText('BTC-USDC');
	await expect(dialog).toContainText(fingerprint);
	const confirm = dialog.getByRole('button', { name: 'Arm live trading', exact: true });
	await expect(confirm).toBeDisabled();
	await expect(dialog.getByText('Tick the acknowledgement to continue.')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
	expect(body).toBeNull();

	await liveCard.getByRole('button', { name: 'Arm live trading…' }).click();
	await expect(confirm).toBeDisabled();
	await dialog
		.getByLabel('I understand this places real orders on Coinbase with real money.')
		.check();
	await expect(confirm).toBeEnabled();
	await confirm.click();
	await expect.poll(() => body).not.toBeNull();
	expect(body).toEqual({
		strategy_fingerprint: fingerprint,
		mode: 'live',
		i_understand_live: true
	});
	await expect(liveCard.getByTestId('runtime-row')).toBeVisible();
});

test('resuming a paused live deployment also needs the real-orders checkbox', async ({ page }) => {
	await mockStrategy(page);
	await mockPreflight(page);
	const paused = deployment({
		mode: 'live',
		status: 'paused',
		lifecycle_command: 'stop_new_entries'
	});
	await mockDeployments(page, () => [paused]);
	let resumeBody: unknown = 'not called';
	await page.route('**/api/v1/deployments/*/resume', async (route) => {
		resumeBody = route.request().postDataJSON();
		await route.fulfill({ json: { ...paused, status: 'running', lifecycle_command: 'none' } });
	});
	await page.goto(runStage);
	await page.getByTestId('live-card').getByRole('button', { name: 'Resume entries…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Resume live deployment?' });
	await expect(dialog).toContainText('re-arms REAL Coinbase spot order submission');
	const confirm = dialog.getByRole('button', { name: 'Resume entries' });
	await expect(confirm).toBeDisabled();
	await dialog
		.getByLabel('I understand this places real orders on Coinbase with real money.')
		.check();
	await confirm.click();
	await expect.poll(() => resumeBody).toEqual({ i_understand_live: true });
});

test('live preflight reports each source and says Unknown when one cannot be read', async ({
	page
}) => {
	await mockStrategy(page);
	await mockPreflight(page, { allocation: false });
	await mockDeployments(page, () => [deployment()]);
	await page.goto(runStage);
	const checks = page.getByRole('list', { name: 'Live preflight' });
	await expect(checks.locator('[data-preflight="credentials"]')).toContainText(
		'Coinbase credentials are configured'
	);
	await expect(checks.locator('[data-preflight="risk-policy"]')).toContainText(
		'Risk policy published · v7'
	);
	await expect(checks.locator('[data-preflight="balance"]')).toContainText(
		'1240.18 USDC available on Coinbase'
	);
	await expect(checks.locator('[data-preflight="allocation"]')).toHaveAttribute(
		'data-state',
		'attention'
	);
	await expect(checks.locator('[data-preflight="clock-feed"]')).toContainText('1h clock');
	await expect(checks.locator('[data-preflight="paper-evidence"]')).toContainText(
		"Paper: 1 deployment of this version, 6 closed trades. Paper results don't qualify a strategy for live; that's your call."
	);
	await expect(page.getByTestId('live-card')).not.toContainText(/\bready\b/i);
	// Arming is still the operator's decision: not gated by preflight items.
	await expect(page.getByRole('button', { name: 'Arm live trading…' })).toBeEnabled();

	await page.route('**/api/v1/credentials/coinbase', (route) =>
		route.fulfill({ status: 500, json: { detail: 'boom' } })
	);
	await page.route('**/api/v1/risk-policy', (route) =>
		route.fulfill({ status: 503, json: { detail: 'down' } })
	);
	await page.route('**/api/v1/portfolio', (route) =>
		route.fulfill({ status: 502, json: { detail: { code: 'coinbase_unavailable' } } })
	);
	await page.reload();
	for (const id of ['credentials', 'risk-policy', 'balance', 'allocation']) {
		const item = checks.locator(`[data-preflight="${id}"]`);
		await expect(item).toHaveAttribute('data-state', 'unknown');
		await expect(item).toContainText('Unknown');
	}
});

test('a sub-hour clock reads the user-order feed from the operator runtime report', async ({
	page
}) => {
	await page.route(`**/api/v1/strategies/${strategyId}/history`, (route) =>
		route.fulfill({
			json: {
				strategy_id: strategyId,
				latest_version: 1,
				next_version: 2,
				versions: [historyVersion(1, fingerprint)],
				draft: null
			}
		})
	);
	await page.route('**/api/v1/strategies/source/*', (route) =>
		route.fulfill({ json: { strategy: { ...draft, status: 'published', timeframe: '5m' } } })
	);
	await mockPreflight(page);
	await mockDeployments(page, () => []);
	await page.route('**/api/v1/operator/runtime', (route) =>
		route.fulfill({ json: { payload: { user_order_feed: { state: 'stale' } } } })
	);
	await page.goto(runStage);
	const feed = page.locator('[data-preflight="clock-feed"]');
	await expect(feed).toHaveAttribute('data-state', 'attention');
	await expect(feed).toContainText('5m clock needs a connected user-order feed (currently stale)');
});

test('deployments are filtered by exact fingerprint; other versions stay separate', async ({
	page
}) => {
	await mockStrategy(page, {
		versions: [historyVersion(1, fingerprint), historyVersion(2, fingerprintV2)]
	});
	await mockPreflight(page);
	const v1 = deployment();
	const v2 = deployment({
		id: '01985cf0-7b60-7000-8000-000000000333',
		strategy_fingerprint: fingerprintV2,
		status: 'paused'
	});
	await mockDeployments(page, () => [v1, v2]);
	await page.goto(`${runStage}?version=${fingerprint}`);
	await expect(page.getByTestId('paper-card').getByTestId('runtime-row')).toHaveCount(1);
	await expect(page.getByTestId('paper-card').getByTestId('runtime-row')).toHaveAttribute(
		'data-deployment-id',
		v1.id
	);
	const others = page.getByRole('region', { name: 'Other deployments for this strategy' });
	await expect(others).toContainText('paper · paused · v2');

	// Switching the version swaps the runtime rows.
	await page.getByTestId('workspace-version-picker').selectOption(fingerprintV2);
	await expect(page.getByTestId('paper-card').getByTestId('runtime-row')).toHaveAttribute(
		'data-deployment-id',
		v2.id
	);
	await expect(others).toContainText('paper · running · v1');
});

test('a draft-only strategy cannot run', async ({ page }) => {
	await mockStrategy(page, { versions: [], draft });
	await page.goto(runStage);
	await expect(page.getByRole('heading', { name: 'No published version yet' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Arm live trading…' })).toHaveCount(0);
});
