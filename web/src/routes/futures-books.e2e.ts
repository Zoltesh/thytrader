/**
 * Paper futures books in the browser (ADR 0129): the bot-detail futures card, the
 * Portfolio badge, the Run stage paper-only start with all three fees, and Home's
 * "Paper futures books" list. Every API is route-mocked.
 */
import type { Page } from '@playwright/test';
import { decisionPageBody } from '../e2e/decision-fixtures';
import { expect, test } from '../e2e/harness';
import { inventoryPageFixture } from '../e2e/inventory';
import {
	definition,
	deployment,
	fingerprint,
	mockDeployments,
	mockPreflight,
	mockStrategy,
	strategyId,
	strategyRecord
} from '../e2e/workspace-fixtures';

const futuresId = '01985cf0-7b60-7000-8000-00000000f001';
const spotId = '01985cf0-7b60-7000-8000-000000000222';
const product = 'BIP-20DEC30-CDE';

const futuresDefinition = {
	...definition,
	name: 'BTC perp trend',
	instrument: {
		product_id: product,
		base_currency: 'BTC',
		quote_currency: 'USD',
		kind: 'future'
	},
	derivatives: { max_leverage: '3' }
};

function futuresDeployment(overrides: Record<string, unknown> = {}) {
	return deployment({
		id: futuresId,
		strategy_name: 'BTC perp trend',
		product_id: product,
		phase: 'flat',
		position: null,
		positions: [],
		cash: '980.30',
		paper_starting_cash: '1000',
		ledger: undefined,
		...overrides
	});
}

function book(overrides: Record<string, unknown> = {}) {
	return {
		deployment_id: futuresId,
		strategy_name: 'BTC perp trend',
		status: 'running',
		mode: 'paper',
		product_id: product,
		currency: 'USD',
		contract_kind: 'perpetual_future',
		underlying: 'BTC',
		contract_size: '0.01',
		fee_per_contract: '0.15',
		maker_fee_rate: '0.0002',
		taker_fee_rate: '0.0005',
		catalog_fingerprint: 'sha256:abc',
		bound_at: '2026-10-09T12:00:00Z',
		side: 'long',
		contracts: '2',
		base_quantity: '0.02',
		entry_price: '62000',
		mark_price: '63000.5',
		marked_at: '2026-10-10T01:00:00Z',
		paper_starting_cash: '1000',
		cash: '980.30',
		equity: '1000.31',
		notional: '1260.01',
		leverage: '1.26',
		policy_max_leverage: '3',
		overnight_long_margin_rate: '0.25',
		overnight_short_margin_rate: '0.3',
		margin_observed_at: '2026-10-10T00:00:00Z',
		initial_margin: '315.0025',
		maintenance_margin: '315.0025',
		liquidation_buffer_fraction: '0.685093',
		min_liquidation_buffer_fraction: '0.2',
		liquidation_price: '28984.5',
		funding_total: '-0.42',
		funding_hours: 13,
		funding_overdue_since: null,
		recent_funding: [
			{
				funding_time: '2026-10-10T01:00:00Z',
				signed_quantity: '0.02',
				mark_price: '63000.5',
				rate: '0.0001',
				amount: '-0.0126'
			}
		],
		daily_loss_latched: false,
		entry_blocks: [],
		unknown: [],
		...overrides
	};
}

const unknownBook = book({
	contract_kind: null,
	fee_per_contract: null,
	bound_at: null,
	mark_price: null,
	marked_at: null,
	equity: null,
	notional: null,
	leverage: null,
	initial_margin: null,
	maintenance_margin: null,
	liquidation_buffer_fraction: null,
	liquidation_price: null,
	overnight_long_margin_rate: null,
	overnight_short_margin_rate: null,
	recent_funding: [],
	entry_blocks: ['FUTURES_CONTRACT_UNBOUND', 'FUTURES_MARGIN_UNKNOWN'],
	unknown: ['binding', 'mark', 'margin_rates']
});

/** Read surfaces of the bot detail page for one deployment; returns the futures GET count. */
async function mockDetail(
	page: Page,
	current: Record<string, unknown>,
	futures: unknown
): Promise<{ futuresRequests: () => number }> {
	const id = current.id as string;
	let futuresRequests = 0;
	await page.route(`**/api/v1/strategies/${strategyId}`, (route) =>
		route.fulfill({ json: strategyRecord() })
	);
	await page.route('**/api/v1/strategies/snapshots/*', (route) =>
		route.fulfill({
			json: {
				strategy_fingerprint: fingerprint,
				strategy_id: strategyId,
				strategy_name: 'BTC perp trend',
				strategy: definition,
				created_at: '2026-09-20T00:00:00Z',
				is_current: true
			}
		})
	);
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${id}/decisions`,
		(route) => route.fulfill({ json: { deployment_id: id, ...decisionPageBody([]) } })
	);
	await page.route(
		(url) => url.pathname === '/api/v1/memory/trade-reasons',
		(route) => route.fulfill({ json: { trade_reasons: [] } })
	);
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${id}/twin`,
		(route) => route.fulfill({ json: { deployment_id: id, twin: null } })
	);
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${id}`,
		(route) => route.fulfill({ json: current })
	);
	await page.route(
		(url) => url.pathname === `/api/v1/deployments/${id}/futures`,
		(route) => {
			futuresRequests += 1;
			return route.fulfill({ json: futures });
		}
	);
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) => route.fulfill({ json: inventoryPageFixture([current]) })
	);
	await page.route(
		(url) => url.pathname === '/api/v1/operator/performance',
		(route) => route.fulfill({ status: 503, json: { detail: 'Performance is unavailable.' } })
	);
	for (const kind of ['orders', 'fills'] as const) {
		await page.route(
			(url) => url.pathname === `/api/v1/deployments/${id}/${kind}`,
			(route) => route.fulfill({ json: { [kind]: [], next_cursor: null, limit: 50, returned: 0 } })
		);
	}
	return { futuresRequests: () => futuresRequests };
}

test('bot detail shows a known paper futures book in USD', async ({ page }) => {
	await mockDetail(page, futuresDeployment(), book());
	await page.goto(`/deployments/${futuresId}`);
	const card = page.getByTestId('futures-book');
	await expect(card.getByRole('heading', { name: 'Paper futures book (USD)' })).toBeVisible();
	await expect(card.getByTestId('futures-paper-only')).toContainText('no live futures order path');
	await expect(card.getByTestId('futures-collateral-note')).toContainText(
		'USDC spot balance as futures collateral'
	);
	await expect(card.getByTestId('futures-fact-product')).toContainText(product);
	await expect(card.getByTestId('futures-fact-product')).toContainText('Perpetual');
	await expect(card.getByTestId('futures-fact-contract-size')).toContainText('0.01 BTC');
	await expect(card.getByTestId('futures-fact-fee-per-contract')).toContainText('0.15 USD');
	await expect(card.getByTestId('futures-fact-side')).toContainText('Long 2 contracts');
	await expect(card.getByTestId('futures-fact-mark')).toContainText('$63,000.50');
	await expect(card.getByTestId('futures-fact-equity')).toContainText('$1,000.31');
	await expect(card.getByTestId('futures-fact-leverage')).toContainText('1.26×');
	await expect(card.getByTestId('futures-fact-leverage')).toContainText('Policy max 3×');
	await expect(card.getByTestId('futures-fact-initial-margin')).toContainText('long 25.00%');
	await expect(card.getByTestId('futures-fact-liquidation-buffer')).toContainText('68.51%');
	await expect(card.getByTestId('futures-fact-liquidation-buffer')).toContainText(
		'Policy minimum 20.00%'
	);
	await expect(card.getByTestId('futures-fact-liquidation-price')).toContainText('$28,984.50');
	await expect(card.getByTestId('futures-fact-funding-total')).toContainText('-$0.42');
	await expect(card.getByTestId('futures-funding-table')).toContainText('-0.0126 USD');
	await expect(card.getByTestId('futures-entry-blocks')).toHaveCount(0);
	await expect(card.getByTestId('futures-unknown')).toHaveCount(0);
});

test('bot detail says unknown, not zero, and why entries are denied', async ({ page }) => {
	await mockDetail(page, futuresDeployment(), unknownBook);
	await page.goto(`/deployments/${futuresId}`);
	const card = page.getByTestId('futures-book');
	await expect(card.getByTestId('futures-entry-blocks')).toHaveText(
		'New entries denied: no contract is bound to this book (FUTURES_CONTRACT_UNBOUND); ' +
			'the overnight margin rates were never observed (FUTURES_MARGIN_UNKNOWN). ' +
			'Exits are never blocked.'
	);
	await expect(card.getByTestId('futures-unknown')).toContainText(
		'Unknown, not zero: contract binding, mark price, margin rates'
	);
	await expect(card.getByTestId('futures-fact-equity')).toContainText('Unknown');
	await expect(card.getByTestId('futures-fact-liquidation-buffer')).toContainText('Unknown');
	await expect(card.getByTestId('futures-fact-equity')).not.toContainText('$0');
	await expect(card).toContainText('No funding charged yet.');
});

test('a spot bot makes no futures request and shows no futures card', async ({ page }) => {
	const spot = deployment({ id: spotId });
	const { futuresRequests } = await mockDetail(page, spot, {});
	await page.goto(`/deployments/${spotId}`);
	await expect(page.getByTestId('kpi-position')).toBeVisible();
	await expect(page.getByTestId('futures-book')).toHaveCount(0);
	expect(futuresRequests()).toBe(0);
});

test('Portfolio marks a futures book with a USD paper badge', async ({ page }) => {
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) =>
			route.fulfill({
				json: inventoryPageFixture([futuresDeployment(), deployment({ id: spotId })])
			})
	);
	await page.goto('/deployments');
	const rows = page.getByTestId('bot-row');
	await expect(rows).toHaveCount(2);
	const badges = page.getByTestId('futures-badge');
	await expect(badges).toHaveCount(1);
	await expect(badges).toHaveText('Futures · USD · paper');
	await expect(rows.filter({ has: badges })).toContainText('BTC perp trend');
});

function feesReport(futures: Record<string, unknown> | null) {
	return {
		report_kind: 'fees',
		overall_status: 'healthy',
		components: [],
		payload: {
			taker_fee_rate: '0.004',
			maker_fee_rate: '0.0025',
			futures
		}
	};
}

const futuresEvidence = {
	status: 'available',
	maker_fee_rate: '0.0002',
	taker_fee_rate: '0.0005',
	usd_volume_30d: '0',
	fee_tier: 'Intro 1',
	as_of: '2026-10-10T00:00:00Z',
	fee_per_contract: null,
	fee_per_contract_source: 'operator_input',
	unavailable_reason: null,
	preview_product_id: null,
	preview_commission_total: null,
	preview_observed_at: null,
	preview_unavailable_reason: null
};

test('Run stage starts a paper futures book with all three fees and refuses live', async ({
	page
}) => {
	await mockStrategy(page, {
		record: strategyRecord({
			name: 'BTC perp trend',
			document: futuresDefinition,
			strategy: futuresDefinition,
			product_id: product
		})
	});
	await mockPreflight(page);
	const previews: (string | null)[] = [];
	await page.route(
		(url) => url.pathname === '/api/v1/operator/fees',
		(route) => {
			const preview = new URL(route.request().url()).searchParams.get('futures_preview_product_id');
			previews.push(preview);
			return route.fulfill({
				json: feesReport(
					preview === null
						? futuresEvidence
						: {
								...futuresEvidence,
								fee_per_contract: '0.15',
								fee_per_contract_source: 'orders_preview',
								preview_product_id: preview
							}
				)
			});
		}
	);
	let created: Record<string, unknown> | null = null;
	const started = futuresDeployment();
	await mockDeployments(
		page,
		() => (created ? [started] : []),
		async (body, route) => {
			created = body as Record<string, unknown>;
			await route.fulfill({ status: 201, json: started });
		}
	);
	await page.goto(`/strategies/${strategyId}/run`);

	const live = page.getByTestId('live-card');
	await expect(live.getByTestId('live-futures-unsupported')).toHaveText(
		'Live futures are not supported (FUTURES_LIVE_UNSUPPORTED) — run this strategy as a paper book.'
	);
	await expect(live.getByRole('button', { name: 'Arm live trading…' })).toHaveCount(0);

	const paper = page.getByTestId('paper-card');
	const form = paper.getByTestId('futures-start-form');
	await expect(form.getByLabel('Maker fee rate')).toHaveValue('0.0002');
	await expect(form.getByLabel('Taker fee rate')).toHaveValue('0.0005');
	await expect(form.getByLabel('Fee per contract (USD)')).toHaveValue('');
	await expect(form.getByLabel('Paper starting cash (USD)')).toHaveValue('');
	const startButton = form.getByRole('button', { name: 'Start paper futures book…' });
	await expect(startButton).toBeDisabled();
	await expect(form.getByTestId('futures-start-missing')).toContainText('starting cash (USD)');

	await form.getByRole('button', { name: 'Quote fee per contract from Coinbase' }).click();
	await expect(form.getByLabel('Fee per contract (USD)')).toHaveValue('0.15');
	expect(previews).toContain(product);
	await form.getByLabel('Paper starting cash (USD)').fill('1000');
	await expect(startButton).toBeEnabled();
	await startButton.click();

	const dialog = page.getByRole('dialog', { name: 'Start paper futures book?' });
	await expect(dialog).toContainText('0.15 USD per contract');
	await expect(dialog).toContainText('places no Coinbase orders');
	await dialog.getByRole('button', { name: 'Start paper futures book' }).click();
	await expect.poll(() => created).not.toBeNull();
	expect(created).toEqual({
		strategy_id: strategyId,
		mode: 'paper',
		paper_starting_cash: '1000',
		maker_fee_rate: '0.0002',
		taker_fee_rate: '0.0005',
		paper_fee_per_contract: '0.15'
	});
	await expect(paper.getByTestId('runtime-row')).toBeVisible();
});

test('Run stage surfaces a refused futures start', async ({ page }) => {
	await mockStrategy(page, {
		record: strategyRecord({
			document: futuresDefinition,
			strategy: futuresDefinition,
			product_id: product
		})
	});
	await mockPreflight(page);
	await page.route(
		(url) => url.pathname === '/api/v1/operator/fees',
		(route) => route.fulfill({ json: feesReport(null) })
	);
	await mockDeployments(
		page,
		() => [],
		async (_body, route) => {
			await route.fulfill({
				status: 409,
				json: { detail: 'FUTURES_PAPER_CAPITAL_EXCEEDED: starting cash exceeds paper capital.' }
			});
		}
	);
	await page.goto(`/strategies/${strategyId}/run`);
	const form = page.getByTestId('futures-start-form');
	await expect(form.getByTestId('futures-fee-source')).toContainText(
		'Coinbase reported no futures fee rates'
	);
	await form.getByLabel('Paper starting cash (USD)').fill('900000');
	await form.getByLabel('Maker fee rate').fill('0.0002');
	await form.getByLabel('Taker fee rate').fill('0.0005');
	await form.getByLabel('Fee per contract (USD)').fill('0.15');
	await form.getByRole('button', { name: 'Start paper futures book…' }).click();
	const dialog = page.getByRole('dialog', { name: 'Start paper futures book?' });
	await dialog.getByRole('button', { name: 'Start paper futures book' }).click();
	await expect(dialog).toContainText('FUTURES_PAPER_CAPITAL_EXCEEDED');
});

function futuresBooksReport(books: unknown[]) {
	return {
		report_kind: 'futures_books',
		overall_status: 'healthy',
		components: [{ name: 'futures_books', status: 'healthy', reason_code: 'OK', detail: 'x' }],
		payload: {
			paper_capital_usd: '5000',
			committed_paper_cash_usd: '1000',
			futures_policy_set: true,
			books,
			collateral_note: 'Coinbase counts the USDC spot balance as CFM futures collateral.',
			live_supported: false
		}
	};
}

test('Home lists paper futures books even when the account was never observed', async ({
	page
}) => {
	await page.route('**/api/v1/operator/futures-account', (route) =>
		route.fulfill({
			json: {
				overall_status: 'degraded',
				components: [
					{
						name: 'futures_account',
						status: 'degraded',
						reason_code: 'FUTURES_MIRROR_NOT_RUN',
						detail: 'x'
					}
				],
				payload: {
					observed_at: null,
					age_seconds: null,
					stale: null,
					enablement: null,
					read_failures: [],
					balance: null,
					positions: null
				}
			}
		})
	);
	await page.route('**/api/v1/operator/futures-books', (route) =>
		route.fulfill({
			json: futuresBooksReport([
				book(),
				book({ deployment_id: 'stopped-book', status: 'stopped' }),
				{ ...unknownBook, deployment_id: spotId, strategy_name: 'ETH perp', status: 'paused' }
			])
		})
	);
	await page.route(
		(url) => url.pathname === '/api/v1/deployments',
		(route) => route.fulfill({ json: inventoryPageFixture([futuresDeployment()]) })
	);
	await page.goto('/');
	const card = page.getByTestId('futures-card');
	await expect(card.getByRole('heading', { name: 'Paper futures books (USD)' })).toBeVisible();
	await expect(card.getByTestId('futures-enablement')).toHaveCount(0);
	await expect(card.getByTestId('futures-books-envelope')).toHaveText(
		'$1,000.00 committed of $5,000.00 paper futures capital'
	);
	const rows = card.getByTestId('futures-book-row');
	await expect(rows).toHaveCount(2);
	await expect(rows.first()).toContainText('Long 2 contracts');
	await expect(rows.first()).toContainText('Equity $1,000.31');
	await expect(rows.first()).toContainText('Buffer 68.51%');
	await expect(rows.first().getByTestId('futures-book-warning')).toHaveCount(0);
	await expect(rows.nth(1).getByTestId('futures-book-warning')).toHaveText('Unknown evidence');
	await expect(rows.nth(1)).toContainText('Equity Unknown');
	await expect(page.getByTestId('your-bots').getByTestId('futures-badge')).toHaveText(
		'Futures · USD · paper'
	);
	await rows.first().getByRole('link').click();
	await expect(page).toHaveURL(new RegExp(`/deployments/${futuresId}$`));
});
