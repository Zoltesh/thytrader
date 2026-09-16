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

	await page.goto('/settings');
	await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
	await expect(page.getByText('Loopback settings')).toBeVisible();
	const panel = page.getByRole('region', { name: 'YAML settings and YOLO' });
	await expect(panel).toBeVisible();
	await panel.getByLabel('YOLO enabled').check();
	await panel.getByLabel('paper').check();
	await panel.getByRole('button', { name: 'Save YAML settings' }).click();
	await expect(page.getByRole('status')).toContainText('Applied without restart');
	await expect(page.getByText('--i-understand-live')).toBeVisible();
});
