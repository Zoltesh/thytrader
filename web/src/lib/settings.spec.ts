import { describe, expect, it, vi } from 'vitest';

import {
	fetchYamlSettings,
	saveYamlSettings,
	toYamlSettingsWrite,
	type YamlSettingsView
} from './settings';

const sample: YamlSettingsView = {
	yolo_enabled: true,
	yolo_tiers: ['paper'],
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
		restart_required_fields: ['api_host']
	}
};

describe('yaml settings client', () => {
	it('loads GET /api/v1/settings without secret fields', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				json: async () => sample
			})
		);
		const view = await fetchYamlSettings();
		expect(view.yolo_tiers).toEqual(['paper']);
		expect(view.live_hard_gate).toBe(true);
		expect(JSON.stringify(view)).not.toContain('api_key');
		expect(JSON.stringify(view)).not.toContain('webhook_url');
	});

	it('PUTs the scalar paper tier without Coinbase fields', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => sample
		});
		vi.stubGlobal('fetch', fetchMock);
		const body = toYamlSettingsWrite(sample);
		await saveYamlSettings(body);
		expect(fetchMock).toHaveBeenCalledWith(
			'/api/v1/settings',
			expect.objectContaining({
				method: 'PUT',
				body: JSON.stringify(body)
			})
		);
		expect(body.yolo_tiers).toEqual(['paper']);
		expect(JSON.stringify(body)).not.toContain('coinbase');
	});
});
