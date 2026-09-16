/**
 * Typed API client for experiential memory journals, hooks, monitor, and notify.
 */

export type ActorOrigin = 'human' | 'agent';
export type JournalKind = 'fact' | 'lesson' | 'note';
export type SentimentLabel = 'bullish' | 'bearish' | 'neutral' | 'unknown';
export type PatternStatus = 'hypothesized' | 'supported' | 'contradicted' | 'retired';
export type NotifyProvider = 'none' | 'log' | 'webhook';
export type DeliveryStatus = 'skipped' | 'logged' | 'delivered' | 'failed';

export interface MemoryCounts {
	journals: number;
	sentiment: number;
	patterns: number;
	notifications: number;
	trade_reasons: number;
}

export interface MemoryStatus {
	schema_version: 'thytrader-experiential-memory-v1';
	counts: MemoryCounts;
	notify_provider: NotifyProvider;
	notify_webhook_configured: boolean;
	notify_enabled: boolean;
	storage: 'available' | 'unavailable';
}

export interface JournalEntry {
	id: string;
	occurred_at: string;
	origin: ActorOrigin;
	kind: JournalKind;
	title: string;
	body: string;
	product_id: string | null;
	runtime_mode: string;
	lesson_outcome: string;
}

export interface SentimentSnapshot {
	id: string;
	occurred_at: string;
	origin: ActorOrigin;
	label: SentimentLabel;
	product_id: string | null;
	note: string;
}

export interface PatternObservation {
	id: string;
	occurred_at: string;
	origin: ActorOrigin;
	pattern_key: string;
	name: string;
	hypothesis: string;
	status: PatternStatus;
}

export interface NotificationRecord {
	id: string;
	occurred_at: string;
	origin: ActorOrigin;
	title: string;
	severity: string;
	provider: NotifyProvider;
	delivery_status: DeliveryStatus;
	detail: string;
}

export interface MonitorFinding {
	reason_code: string;
	detail: string;
	deployment_id: string | null;
}

export interface MonitorDeployment {
	deployment_id: string;
	mode: string;
	status: string;
	product_id: string;
	mismatch_present: boolean;
}

export interface TradeReasonNote {
	origin: 'human' | 'agent';
	body: string;
	recorded_at: string;
}

export interface TradeReasonStrategy {
	strategy_id: string | null;
	strategy_fingerprint: string | null;
	name: string | null;
	version: number | null;
}

export interface TradeReasonSignal {
	kind: string;
	last_signal: string | null;
	candle_starts_at: string;
	timeframe: string | null;
}

export interface TradeReasonRisk {
	decision: 'allow' | 'deny';
	reason_code: string;
	detail: string;
	policy_fingerprint: string;
	policy_source: 'compiled_default' | 'published';
}

export interface TradeReasonFillFact {
	fill_id: string;
	price: string;
	quantity: string;
	fee: string;
	filled_at: string;
}

export interface TradeReasonReconcile {
	order_id: string | null;
	order_status: string | null;
	filled_quantity: string | null;
	reject_reason: string | null;
	unknown_timeout: boolean;
	ledger_available: boolean;
	fills: TradeReasonFillFact[];
}

export interface TradeReasonRecord {
	schema_version: 'thytrader-trade-reason-v1';
	id: string;
	created_at: string;
	origin: 'human' | 'agent' | 'runtime';
	intent_id: string;
	deployment_id: string;
	deployment_kind: 'strategy' | 'discretionary' | string;
	mode: 'paper' | 'live';
	product_id: string;
	purpose: string;
	side: string;
	strategy: TradeReasonStrategy | null;
	signal: TradeReasonSignal;
	risk: TradeReasonRisk;
	notes: TradeReasonNote[];
	reconcile: TradeReasonReconcile;
}

export interface MonitorSnapshot {
	schema_version: 'thytrader-monitor-v1';
	memory: MemoryStatus;
	deployments: MonitorDeployment[];
	recent_journals: JournalEntry[];
	recent_notifications: NotificationRecord[];
	recent_trade_reasons: TradeReasonRecord[];
	findings: MonitorFinding[];
}

async function readJson<T>(path: string, fallback: string): Promise<T> {
	const response = await fetch(path, { headers: { Accept: 'application/json' } });
	if (!response.ok) {
		throw new Error(fallback);
	}
	return (await response.json()) as T;
}

export async function fetchMemoryStatus(): Promise<MemoryStatus> {
	return readJson<MemoryStatus>('/api/v1/memory', 'Experiential memory is unavailable.');
}

export async function fetchMemoryMonitor(): Promise<MonitorSnapshot> {
	return readJson<MonitorSnapshot>('/api/v1/memory/monitor', 'Memory monitor is unavailable.');
}

export async function fetchJournals(): Promise<JournalEntry[]> {
	const payload = await readJson<{ journals: JournalEntry[] }>(
		`/api/v1/memory/journals`,
		'Journals are unavailable.'
	);
	return payload.journals;
}

export async function fetchSentiment(): Promise<SentimentSnapshot[]> {
	const payload = await readJson<{ sentiment: SentimentSnapshot[] }>(
		`/api/v1/memory/sentiment`,
		'Sentiment snapshots are unavailable.'
	);
	return payload.sentiment;
}

export async function fetchPatterns(): Promise<PatternObservation[]> {
	const payload = await readJson<{ patterns: PatternObservation[] }>(
		`/api/v1/memory/patterns`,
		'Pattern observations are unavailable.'
	);
	return payload.patterns;
}

export async function fetchNotifications(): Promise<NotificationRecord[]> {
	const payload = await readJson<{ notifications: NotificationRecord[] }>(
		`/api/v1/memory/notifications`,
		'Notifications are unavailable.'
	);
	return payload.notifications;
}

export async function fetchTradeReasons(options?: {
	deploymentId?: string;
	intentId?: string;
}): Promise<TradeReasonRecord[]> {
	const params = new URLSearchParams();
	if (options?.deploymentId !== undefined) {
		params.set('deployment_id', options.deploymentId);
	}
	if (options?.intentId !== undefined) {
		params.set('intent_id', options.intentId);
	}
	const suffix = params.size > 0 ? `?${params.toString()}` : '';
	const payload = await readJson<{ trade_reasons: TradeReasonRecord[] }>(
		`/api/v1/memory/trade-reasons${suffix}`,
		'Why-trade records are unavailable.'
	);
	return payload.trade_reasons;
}
