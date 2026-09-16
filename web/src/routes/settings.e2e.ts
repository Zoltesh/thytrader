import { expect, test } from '../e2e/harness';

const settings = {
	yolo_enabled: false,
	yolo_tiers: [] as string[],
	log_level: 'INFO',
	snapshot_interval_seconds: 300,
	market_data_worker_interval_seconds: 300,
	market_data_worker_lookback_hours: 168,
	market_data_worker_product_id: 'BTC-USD',
	execution_worker_interval_seconds: 30,
	notify_provider: 'none',
	settings_file: 'thytrader.yaml',
	yaml_loaded: true,
	live_hard_gate: true,
	playbook_live_authority: false,
	process: {
		environment: 'development',
		api_host: '127.0.0.1',
		api_port: 8200,
		containerized: false,
		allow_remote_access: false,
		market_data_dataset_root: 'data/market-data',
		database_configured: false,
		coinbase_credentials_configured: false,
		notify_webhook_configured: false,
		restart_required: true,
		restart_required_fields: ['api_host', 'api_port']
	}
};

const status = {
	provider: 'coinbase',
	configured: false,
	persisted: false,
	env_file_writable: true,
	api_hot_reloaded: false,
	workers_require_restart: false,
	workers_restart_detail: 'Restart Compose or native workers after changing Coinbase keys.'
};

test('settings page toggles YOLO paper without a restart and without secrets', async ({ page }) => {
	let stored = { ...settings, yolo_tiers: [...settings.yolo_tiers] };
	await page.route('**/api/v1/settings', async (route) => {
		const method = route.request().method();
		if (method === 'GET') {
			await route.fulfill({ json: stored });
			return;
		}
		if (method === 'PUT') {
			const body = route.request().postDataJSON() as {
				yolo_enabled?: boolean;
				yolo_tiers?: string[];
			};
			expect(body.yolo_enabled).toBe(true);
			expect(body.yolo_tiers).toEqual(['paper']);
			expect(JSON.stringify(body)).not.toContain('coinbase');
			expect(JSON.stringify(body)).not.toContain('webhook_url');
			stored = {
				...stored,
				yolo_enabled: true,
				yolo_tiers: ['paper']
			};
			await route.fulfill({ json: stored });
			return;
		}
		await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
	});
	await page.route('**/api/v1/credentials/coinbase', async (route) => {
		if (route.request().method() === 'GET') {
			await route.fulfill({ json: status });
			return;
		}
		await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
	});

	await page.goto('/settings');
	await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
	await expect(page.getByRole('main').getByText('Loopback settings')).toBeVisible();
	const panel = page.getByRole('region', { name: 'YAML settings and YOLO' });
	await expect(panel).toBeVisible();
	await panel.getByLabel('YOLO enabled').check();
	await panel.getByLabel('paper').check();
	await panel.getByRole('button', { name: 'Save YAML settings' }).click();
	await expect(page.getByRole('status')).toContainText('Applied without restart');
	await expect(page.getByRole('status')).toContainText('--i-understand-live');
});

test('settings keeps YAML/YOLO and saves Coinbase secrets without echoing them', async ({
	page
}) => {
	const pem =
		'-----BEGIN EC PRIVATE KEY-----\nSYNTHETIC-SETTINGS-PEM-DO-NOT-ECHO\n-----END EC PRIVATE KEY-----';
	await page.route('**/api/v1/settings', async (route) => {
		if (route.request().method() === 'GET') {
			await route.fulfill({ json: settings });
			return;
		}
		await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
	});
	await page.route('**/api/v1/credentials/coinbase', async (route) => {
		const method = route.request().method();
		if (method === 'GET') {
			await route.fulfill({ json: status });
			return;
		}
		if (method === 'PUT') {
			const body = route.request().postDataJSON() as {
				api_key_name?: string;
				private_key?: string;
			};
			expect(body.api_key_name).toBe('organizations/example/apiKeys/settings-e2e');
			expect(body.private_key).toContain('SYNTHETIC-SETTINGS-PEM-DO-NOT-ECHO');
			await route.fulfill({
				json: {
					...status,
					configured: true,
					persisted: true,
					api_hot_reloaded: true,
					workers_require_restart: true
				}
			});
			return;
		}
		await route.fulfill({ status: 405, json: { detail: 'method not allowed' } });
	});

	await page.goto('/settings');
	const nav = page.getByRole('navigation', { name: 'Primary navigation' });
	await expect(nav.getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/chat');
	await expect(nav.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings');
	await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
	await expect(page.getByRole('region', { name: 'YAML settings and YOLO' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Coinbase credentials' })).toBeVisible();
	await expect(page.getByText('SYNTHETIC-SETTINGS-PEM-DO-NOT-ECHO')).toHaveCount(0);

	const form = page.getByRole('form', { name: 'Set or rotate Coinbase credentials' });
	await form.getByLabel('API key name').fill('organizations/example/apiKeys/settings-e2e');
	await form.getByLabel('EC private key (PEM)').fill(pem);
	await form.getByRole('checkbox').check();
	await form.getByRole('button', { name: 'Save Coinbase credentials' }).click();

	await expect(form.getByRole('status')).toContainText('stored server-side');
	await expect(form.getByLabel('API key name')).toHaveValue('');
	await expect(form.getByLabel('EC private key (PEM)')).toHaveValue('');
	await expect(page.getByText('SYNTHETIC-SETTINGS-PEM-DO-NOT-ECHO')).toHaveCount(0);
});
