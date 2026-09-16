/**
 * Typed client for YAML non-secret settings. Secrets never appear in payloads.
 */

export type YoloTier = 'data' | 'research' | 'paper' | 'live';
export type LogLevel = 'CRITICAL' | 'ERROR' | 'WARNING' | 'INFO' | 'DEBUG';
export type NotifyProvider = 'none' | 'log' | 'webhook';

export const YOLO_TIERS: readonly YoloTier[] = ['data', 'research', 'paper', 'live'];
export const LOG_LEVELS: readonly LogLevel[] = ['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'];

export interface ProcessSettingsView {
	environment: string;
	api_host: string;
	api_port: number;
	containerized: boolean;
	allow_remote_access: boolean;
	market_data_dataset_root: string;
	database_configured: boolean;
	coinbase_credentials_configured: boolean;
	notify_webhook_configured: boolean;
	restart_required: true;
	restart_required_fields: string[];
}

export interface YamlSettingsView {
	yolo_enabled: boolean;
	yolo_tiers: YoloTier[];
	log_level: LogLevel;
	snapshot_interval_seconds: number;
	market_data_worker_interval_seconds: number;
	market_data_worker_lookback_hours: number;
	market_data_worker_product_id: string;
	execution_worker_interval_seconds: number;
	notify_provider: NotifyProvider;
	settings_file: string;
	yaml_loaded: boolean;
	live_hard_gate: true;
	playbook_live_authority: false;
	process: ProcessSettingsView;
}

export interface YamlSettingsWrite {
	yolo_enabled: boolean;
	yolo_tiers: YoloTier[];
	log_level: LogLevel;
	snapshot_interval_seconds: number;
	market_data_worker_interval_seconds: number;
	market_data_worker_lookback_hours: number;
	market_data_worker_product_id: string;
	execution_worker_interval_seconds: number;
	notify_provider: NotifyProvider;
}

async function readJson<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
	const response = await fetch(path, {
		headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
		...init
	});
	if (!response.ok) {
		throw new Error(await errorMessage(response, fallback));
	}
	return (await response.json()) as T;
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
	try {
		const payload: unknown = await response.json();
		if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
			const detail = (payload as { detail: unknown }).detail;
			if (typeof detail === 'string' && detail.length > 0) {
				return detail;
			}
			if (
				typeof detail === 'object' &&
				detail !== null &&
				'message' in detail &&
				typeof (detail as { message: unknown }).message === 'string'
			) {
				return (detail as { message: string }).message;
			}
		}
	} catch {
		return fallback;
	}
	return fallback;
}

export async function fetchYamlSettings(): Promise<YamlSettingsView> {
	return readJson<YamlSettingsView>('/api/v1/settings', 'YAML settings are unavailable.');
}

export async function saveYamlSettings(body: YamlSettingsWrite): Promise<YamlSettingsView> {
	return readJson<YamlSettingsView>('/api/v1/settings', 'Could not save YAML settings.', {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

export function toYamlSettingsWrite(view: YamlSettingsView): YamlSettingsWrite {
	return {
		yolo_enabled: view.yolo_enabled,
		yolo_tiers: [...view.yolo_tiers],
		log_level: view.log_level,
		snapshot_interval_seconds: view.snapshot_interval_seconds,
		market_data_worker_interval_seconds: view.market_data_worker_interval_seconds,
		market_data_worker_lookback_hours: view.market_data_worker_lookback_hours,
		market_data_worker_product_id: view.market_data_worker_product_id,
		execution_worker_interval_seconds: view.execution_worker_interval_seconds,
		notify_provider: view.notify_provider
	};
}
