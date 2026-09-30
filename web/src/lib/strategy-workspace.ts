/**
 * Strategy workspace view model (slice 2 of ADR 0079; see ADR 0080).
 *
 * One workspace per strategy: `/strategies/[id]` (Build), `/test`, `/run`, and
 * `/why`. A `?version=<strategy_fingerprint>` query selects the exact
 * published version for Test, Run, and Why; without it the latest published
 * version is the default. A fingerprint that does not belong to the strategy
 * fails closed: nothing is selected and mutations stay disabled.
 *
 * Everything here is a pure function over existing API payloads. There is no
 * readiness state: pipeline chips report evidence that exists, and preflight
 * items report what each existing endpoint says (or `unknown` when it could
 * not be read). No item is combined into a verdict.
 */
import type { CoinbaseCredentialsStatus } from './credentials';
import { productIdQuote } from './deployment-detail';
import type { Deployment } from './deployments';
import type { Portfolio } from './portfolio';
import type { StrategyLibraryEntry, StrategyVersionHistoryEntry } from './strategies';

export type WorkspaceStage = 'build' | 'test' | 'run' | 'why';

export const WORKSPACE_STAGES: readonly { id: WorkspaceStage; label: string }[] = [
	{ id: 'build', label: 'Build' },
	{ id: 'test', label: 'Test' },
	{ id: 'run', label: 'Run' },
	{ id: 'why', label: 'Why' }
];

/**
 * Workspace URL for one stage. `version` and `result` are only emitted when
 * given; Build never carries a result.
 */
export function workspaceHref(
	strategyId: string,
	stage: WorkspaceStage,
	options: { version?: string | null; result?: string | null } = {}
): `/strategies/${string}` {
	const base = `/strategies/${encodeURIComponent(strategyId)}${stage === 'build' ? '' : `/${stage}`}`;
	const params = new URLSearchParams();
	if (options.version) params.set('version', options.version);
	if (options.result && stage === 'test') params.set('result', options.result);
	const query = params.toString();
	return `${base}${query === '' ? '' : `?${query}`}` as `/strategies/${string}`;
}

/** Which stage a SvelteKit route id renders, or null outside the workspace. */
export function stageFromRouteId(routeId: string | null): WorkspaceStage | null {
	switch (routeId) {
		case '/strategies/[id]':
			return 'build';
		case '/strategies/[id]/test':
			return 'test';
		case '/strategies/[id]/run':
			return 'run';
		case '/strategies/[id]/why':
			return 'why';
		default:
			return null;
	}
}

export type WorkspaceVersion =
	/** No `?version=`: the latest published version is selected. */
	| { status: 'default'; entry: StrategyVersionHistoryEntry }
	/** `?version=` matched one of this strategy's published versions. */
	| { status: 'exact'; entry: StrategyVersionHistoryEntry }
	/** Nothing is published yet (draft only). */
	| { status: 'none'; entry: null }
	/** `?version=` does not belong to this strategy: fail closed. */
	| { status: 'invalid'; entry: null; requested: string };

/**
 * Resolve the workspace version context.
 *
 * An explicit fingerprint is honored only when it is one of this strategy's
 * published versions. Anything else is `invalid` — never silently replaced
 * by the latest version, so a mistyped or foreign link cannot start or test a
 * different immutable version.
 */
export function resolveWorkspaceVersion(
	requested: string | null,
	versions: readonly StrategyVersionHistoryEntry[]
): WorkspaceVersion {
	if (requested !== null && requested !== '') {
		const match = versions.find((version) => version.strategy_fingerprint === requested);
		return match === undefined
			? { status: 'invalid', entry: null, requested }
			: { status: 'exact', entry: match };
	}
	const latest = versions[versions.length - 1];
	return latest === undefined
		? { status: 'none', entry: null }
		: { status: 'default', entry: latest };
}

export const INVALID_VERSION_MESSAGE =
	'This version does not belong to this strategy. Nothing is selected and every action on this stage is disabled, so a different immutable version cannot be tested or started by mistake. Pick a published version from the version picker.';

/** `sha256:9f3a…c21e` style short fingerprint for labels; full value stays copyable. */
export function shortStrategyFingerprint(fingerprint: string): string {
	const [algorithm, digest] = fingerprint.includes(':')
		? (fingerprint.split(':', 2) as [string, string])
		: ['', fingerprint];
	if (digest.length <= 12) return fingerprint;
	return `${algorithm === '' ? '' : `${algorithm}:`}${digest.slice(0, 4)}…${digest.slice(-4)}`;
}

/** Minutes in a venue clock such as `5m`, `2h`, `1d`; null when unparseable. */
export function timeframeMinutes(timeframe: string): number | null {
	const match = /^(\d+)([mhd])$/.exec(timeframe.trim());
	if (match === null) return null;
	const amount = Number(match[1]);
	const unit = match[2];
	return unit === 'm' ? amount : unit === 'h' ? amount * 60 : amount * 1440;
}

export type PipelineState = 'none' | 'done' | 'active' | 'paper' | 'live';

export type PipelineStep = {
	stage: 'Build' | 'Test' | 'Paper' | 'Live';
	state: PipelineState;
	/** Text that carries the meaning (color never does it alone). */
	detail: string;
};

/**
 * Library progress chips derived only from the library row payload.
 *
 * Build: an open draft is `active`; published history without a draft is
 * `done`. Test: `done` only when a backtest result is attached. Paper / Live:
 * the newest deployment status per mode. This is evidence, not readiness.
 */
export function libraryPipeline(entry: StrategyLibraryEntry): PipelineStep[] {
	const published = entry.published_versions.length > 0 || entry.latest_fingerprint !== null;
	const build: PipelineStep =
		entry.status === 'draft'
			? {
					stage: 'Build',
					state: 'active',
					detail: published ? 'draft open · published earlier' : 'draft, not published'
				}
			: entry.status === 'archived'
				? { stage: 'Build', state: 'done', detail: 'archived' }
				: { stage: 'Build', state: 'done', detail: 'published' };
	const test: PipelineStep =
		entry.backtest === null
			? { stage: 'Test', state: 'none', detail: 'no backtest' }
			: { stage: 'Test', state: 'done', detail: 'has a backtest' };
	return [
		build,
		test,
		runtimeStep('Paper', entry.paper_live.paper),
		runtimeStep('Live', entry.paper_live.live)
	];
}

function runtimeStep(stage: 'Paper' | 'Live', status: string): PipelineStep {
	const running = stage === 'Paper' ? 'paper' : 'live';
	switch (status) {
		case 'running':
			return { stage, state: running, detail: 'running' };
		case 'paused':
			return { stage, state: running, detail: 'paused' };
		case 'stopped':
			return { stage, state: 'done', detail: 'stopped' };
		case 'unavailable':
			return { stage, state: 'none', detail: 'not deployed' };
		default:
			return { stage, state: 'none', detail: status === '' ? 'unknown' : status };
	}
}

/** Screen-reader sentence for one library row's pipeline. */
export function pipelineSummary(steps: readonly PipelineStep[]): string {
	return steps.map((step) => `${step.stage}: ${step.detail}`).join('; ');
}

export type PreflightState = 'ok' | 'attention' | 'unknown';

export type PreflightItem = {
	id: 'credentials' | 'risk-policy' | 'balance' | 'allocation' | 'clock-feed';
	state: PreflightState;
	label: string;
};

/** A read that failed or is not exposed over HTTP. */
export type Unreadable = { unreadable: true };

export type RiskPolicySnapshot = {
	source: 'compiled_default' | 'published' | string;
	version: number;
	quote_currency: string;
	allocations: { strategy_id: string; allocated_quote: string }[];
};

export type UserOrderFeedState =
	'disconnected' | 'connecting' | 'connected' | 'stale' | 'reconnecting' | 'disabled';

export type LivePreflightInput = {
	strategyId: string;
	productId: string;
	timeframe: string;
	credentials: CoinbaseCredentialsStatus | Unreadable;
	riskPolicy: RiskPolicySnapshot | Unreadable;
	portfolio: Portfolio | Unreadable;
	/** `payload.user_order_feed` from `GET /api/v1/operator/runtime`; null when omitted. */
	userOrderFeed: { state: UserOrderFeedState | string } | null | Unreadable;
};

function unreadable<T extends object>(value: T | Unreadable): value is Unreadable {
	return 'unreadable' in value;
}

/**
 * Live preflight checklist from existing endpoints only.
 *
 * Each item is independent. `unknown` means the source could not be read (or
 * does not report the fact); it is never shown as a pass, and the items are
 * never combined into a ready / not-ready verdict. Arming stays the
 * operator's decision behind the explicit acknowledgement.
 */
export function livePreflight(input: LivePreflightInput): PreflightItem[] {
	return [
		credentialsItem(input.credentials),
		riskPolicyItem(input.riskPolicy),
		balanceItem(input.portfolio, input.productId),
		allocationItem(input.riskPolicy, input.strategyId),
		clockFeedItem(input.timeframe, input.userOrderFeed)
	];
}

function credentialsItem(credentials: CoinbaseCredentialsStatus | Unreadable): PreflightItem {
	if (unreadable(credentials)) {
		return {
			id: 'credentials',
			state: 'unknown',
			label: 'Coinbase credentials: Unknown (status could not be read)'
		};
	}
	return credentials.configured
		? {
				id: 'credentials',
				state: 'ok',
				label:
					'Coinbase credentials are configured (presence only; permissions are not checked here)'
			}
		: {
				id: 'credentials',
				state: 'attention',
				label: 'No Coinbase credentials configured. Set them in System → Settings.'
			};
}

function riskPolicyItem(policy: RiskPolicySnapshot | Unreadable): PreflightItem {
	if (unreadable(policy)) {
		return {
			id: 'risk-policy',
			state: 'unknown',
			label: 'Risk policy: Unknown (the policy could not be read)'
		};
	}
	return policy.source === 'published'
		? { id: 'risk-policy', state: 'ok', label: `Risk policy published · v${policy.version}` }
		: {
				id: 'risk-policy',
				state: 'attention',
				label: 'No risk policy published; the compiled default applies'
			};
}

function balanceItem(portfolio: Portfolio | Unreadable, productId: string): PreflightItem {
	const quote = productIdQuote(productId);
	if (unreadable(portfolio) || quote === null) {
		return {
			id: 'balance',
			state: 'unknown',
			label: `Available ${quote ?? 'quote'} balance: Unknown (the portfolio could not be read)`
		};
	}
	if (portfolio.demo || portfolio.connection.status !== 'connected') {
		return {
			id: 'balance',
			state: 'unknown',
			label: `Available ${quote} balance: Unknown (the portfolio is a demo snapshot, not Coinbase)`
		};
	}
	const asset = portfolio.assets.find((candidate) => candidate.currency === quote);
	if (asset === undefined) {
		return {
			id: 'balance',
			state: 'attention',
			label: `No ${quote} balance in the latest Coinbase portfolio snapshot`
		};
	}
	const positive = Number(asset.available) > 0;
	return {
		id: 'balance',
		state: positive ? 'ok' : 'attention',
		label: `${asset.available} ${quote} available on Coinbase (snapshot ${portfolio.as_of})`
	};
}

function allocationItem(
	policy: RiskPolicySnapshot | Unreadable,
	strategyId: string
): PreflightItem {
	if (unreadable(policy)) {
		return {
			id: 'allocation',
			state: 'unknown',
			label: 'Capital allocation for this strategy: Unknown (the policy could not be read)'
		};
	}
	const allocation = policy.allocations.find((item) => item.strategy_id === strategyId);
	return allocation === undefined
		? {
				id: 'allocation',
				state: 'attention',
				label: 'No capital allocation for this strategy in the effective risk policy'
			}
		: {
				id: 'allocation',
				state: 'ok',
				label: `Allocation for this strategy: ${allocation.allocated_quote} ${policy.quote_currency}`
			};
}

function clockFeedItem(
	timeframe: string,
	feed: { state: string } | null | Unreadable
): PreflightItem {
	const minutes = timeframeMinutes(timeframe);
	if (minutes !== null && minutes >= 60) {
		return {
			id: 'clock-feed',
			state: 'ok',
			label: `${timeframe} clock (live on 1h and slower clocks works without the user-order feed)`
		};
	}
	if (feed === null || unreadable(feed)) {
		return {
			id: 'clock-feed',
			state: 'unknown',
			label: `${timeframe} clock needs the user-order feed: feed state Unknown`
		};
	}
	return feed.state === 'connected'
		? { id: 'clock-feed', state: 'ok', label: `${timeframe} clock · user-order feed connected` }
		: {
				id: 'clock-feed',
				state: 'attention',
				label: `${timeframe} clock needs a connected user-order feed (currently ${feed.state})`
			};
}

export const PAPER_NOT_QUALIFYING_NOTE =
	"Paper results don't qualify a strategy for live; that's your call.";

/** Informational paper evidence line for the Live card (never a gate). */
export function paperEvidenceText(paperDeployments: readonly Deployment[]): string {
	if (paperDeployments.length === 0) {
		return `No paper deployment of this version. ${PAPER_NOT_QUALIFYING_NOTE}`;
	}
	const trades = paperDeployments.reduce(
		(sum, deployment) => sum + (deployment.ledger?.trade_count ?? 0),
		0
	);
	const count = paperDeployments.length;
	return `Paper: ${count} deployment${count === 1 ? '' : 's'} of this version, ${trades} closed trade${trades === 1 ? '' : 's'}. ${PAPER_NOT_QUALIFYING_NOTE}`;
}

export type SignalExplanation = {
	kind: 'not-evaluated' | 'not-matched' | 'undefined' | 'matched' | 'other';
	title: string;
	detail: string;
};

/**
 * Explain the latest completed-bar signal of one deployment (Why stage).
 *
 * `not_matched` is a neutral successful evaluation; `undefined` means the
 * rules could not be evaluated. Neither is inferred from missing rationale.
 */
export function latestSignalExplanation(deployment: Deployment): SignalExplanation {
	const bar = deployment.last_evaluated_bar;
	const timeframe = deployment.timeframe ?? '';
	if (bar === null) {
		return {
			kind: 'not-evaluated',
			title: 'No completed bar evaluated yet',
			detail: 'The runtime has not reported an evaluation for this deployment.'
		};
	}
	switch (deployment.last_signal) {
		case 'not_matched':
			return {
				kind: 'not-matched',
				title: 'No trade — conditions did not match',
				detail: `The completed ${timeframe} bar at ${bar} was evaluated and did not satisfy the entry rules.`
			};
		case 'undefined':
			return {
				kind: 'undefined',
				title: 'No trade — conditions could not be evaluated',
				detail: `The completed ${timeframe} bar at ${bar} could not be evaluated. Review available coverage or runtime mismatch details.`
			};
		case 'matched':
			return {
				kind: 'matched',
				title: 'Entry conditions matched',
				detail: `The completed ${timeframe} bar at ${bar} satisfied the entry rules. Whether an intent was persisted is shown by the trade reasons below.`
			};
		default:
			return {
				kind: 'other',
				title: `Signal: ${deployment.last_signal ?? 'unknown'}`,
				detail: `Reported for the completed ${timeframe} bar at ${bar}.`
			};
	}
}

export const DECISION_HISTORY_NOTE =
	'Only the latest completed-bar signal is kept per deployment. Full per-bar decision history is not recorded yet; trade reasons exist only for bars that persisted an order intent.';

/**
 * Library "Latest" pill: an open draft and published history are shown
 * together (`v3 · draft v4`), never collapsed into one status.
 */
export function libraryVersionLabel(entry: StrategyLibraryEntry): string {
	const latest = entry.latest_version;
	const publishedMax = entry.published_versions.reduce(
		(max, version) => Math.max(max, version.version),
		0
	);
	if (entry.status === 'draft') {
		const draft = `draft v${latest ?? 1}`;
		return publishedMax > 0 ? `v${publishedMax} · ${draft}` : `Draft v${latest ?? 1}`;
	}
	if (entry.status === 'archived') return `Archived v${latest ?? publishedMax}`;
	return `v${latest ?? publishedMax}`;
}
