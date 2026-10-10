/**
 * Futures fields in the Build form (ADR 0128): saving a futures strategy from the
 * browser round-trips `instrument.kind` and every `derivatives` field, the form
 * shows kind, contract, underlying, leverage and margin mode, and a spot strategy
 * saves exactly the keys it had before. Every API is route-mocked.
 */
import type { Page } from '@playwright/test';
import { expect, test } from '../e2e/harness';
import { definition, strategyId, strategyRecord } from '../e2e/workspace-fixtures';

type SavedBody = { document: Record<string, unknown>; revision: number };

const futuresDefinition = {
	...definition,
	name: 'ETH perp short',
	instrument: {
		product_id: 'ETP-20DEC30-CDE',
		base_currency: 'ETH',
		quote_currency: 'USD',
		kind: 'future'
	},
	entry: { ...definition.entry, side: 'short' },
	derivatives: { max_leverage: '3', margin_mode: 'overnight', flatten_before_expiry_hours: 24 }
};

function futuresRecord() {
	return strategyRecord({
		name: futuresDefinition.name,
		document: futuresDefinition,
		strategy: futuresDefinition,
		product_id: 'ETP-20DEC30-CDE'
	});
}

/** Serve one record and capture each PUT body, answering with the saved document. */
async function mockSaves(
	page: Page,
	record: ReturnType<typeof strategyRecord>
): Promise<() => SavedBody | null> {
	let saved: SavedBody | null = null;
	let current = record;
	await page.route(`**/api/v1/strategies/${strategyId}`, async (route) => {
		if (route.request().method() === 'PUT') {
			saved = route.request().postDataJSON() as SavedBody;
			current = {
				...current,
				document: saved.document as typeof current.document,
				strategy: saved.document as typeof current.strategy,
				revision: current.revision + 1
			};
		}
		await route.fulfill({ json: current });
	});
	return () => saved;
}

test('a futures strategy saves every futures field from the Build form', async ({ page }) => {
	const saved = await mockSaves(page, futuresRecord());
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Market and data' }).click();
	await expect(page.getByLabel('Instrument kind')).toHaveValue('future');
	await expect(page.getByLabel('Contract', { exact: true })).toHaveValue('ETP-20DEC30-CDE');
	const fields = page.getByTestId('futures-fields');
	await expect(fields.getByLabel('Underlying')).toHaveValue('ETH');
	await expect(fields.getByLabel('Maximum leverage')).toHaveValue('3');
	await expect(fields.getByLabel('Margin mode')).toHaveValue('overnight');
	await expect(page.getByTestId('futures-flatten')).toContainText('24 h');

	await fields.getByLabel('Maximum leverage').fill('4');
	await page.getByRole('button', { name: 'Overview' }).click();
	await page.getByLabel('Strategy name').fill('ETH perp short v2');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => saved()?.document.name).toBe('ETH perp short v2');
	const document = (saved() as SavedBody).document;
	expect(document.instrument).toEqual(futuresDefinition.instrument);
	expect(document.derivatives).toEqual({
		max_leverage: '4',
		margin_mode: 'overnight',
		flatten_before_expiry_hours: 24
	});
	await expect(page.getByTestId('workspace-save-state')).toContainText('Revision 2');

	// The saved record reloads into the same form values.
	await page.reload();
	await page.getByRole('button', { name: 'Market and data' }).click();
	await expect(page.getByLabel('Instrument kind')).toHaveValue('future');
	await expect(page.getByTestId('futures-fields').getByLabel('Maximum leverage')).toHaveValue('4');
});

test('a spot strategy saves with no futures keys and shows no futures fields', async ({ page }) => {
	const saved = await mockSaves(page, strategyRecord());
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Market and data' }).click();
	await expect(page.getByLabel('Instrument kind')).toHaveValue('spot');
	await expect(page.getByLabel('Product', { exact: true })).toHaveValue('BTC-USDC');
	await expect(page.getByTestId('futures-fields')).toHaveCount(0);
	await page.getByRole('button', { name: 'Overview' }).click();
	await page.getByLabel('Strategy name').fill('Renamed spot trend');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => saved()?.document.name).toBe('Renamed spot trend');
	const document = (saved() as SavedBody).document;
	expect(document).toEqual({ ...definition, name: 'Renamed spot trend' });
	expect(document.instrument).not.toHaveProperty('kind');
	expect(document).not.toHaveProperty('derivatives');
});

test('switching a spot strategy to a futures contract saves kind and derivatives', async ({
	page
}) => {
	const saved = await mockSaves(page, strategyRecord());
	await page.goto(`/strategies/${strategyId}`);
	await page.getByRole('button', { name: 'Market and data' }).click();
	await page.getByLabel('Instrument kind').selectOption('future');
	await page.getByLabel('Contract', { exact: true }).fill('bip-20dec30-cde');
	const fields = page.getByTestId('futures-fields');
	await expect(fields.getByLabel('Underlying')).toHaveValue('BTC');
	await expect(fields.getByLabel('Maximum leverage')).toHaveValue('1');
	await fields.getByLabel('Maximum leverage').fill('2');
	await page.getByRole('button', { name: 'Position sizing' }).click();
	await expect(page.getByLabel('Minimum USD notional')).toBeVisible();
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect
		.poll(() => (saved()?.document.instrument as { kind?: string } | undefined)?.kind)
		.toBe('future');
	const document = (saved() as SavedBody).document;
	expect(document.instrument).toEqual({
		product_id: 'BIP-20DEC30-CDE',
		base_currency: 'BTC',
		quote_currency: 'USD',
		kind: 'future'
	});
	expect(document.derivatives).toEqual({ max_leverage: '2', margin_mode: 'overnight' });
});
