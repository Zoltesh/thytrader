/**
 * Deployment detail view model: exact-version identity, status/eligibility,
 * performance, evidence links, lifecycle dialogs, and quote display.
 *
 * Derived helpers are pure functions over the canonical API payload so the
 * detail page and unit tests share one interpretation of the contract. Nothing
 * here invents state: absent values render as explicit unknowns.
 */
import { canonicalPositions, type Deployment, type DeploymentOrder } from './deployments';

export const LIFECYCLE_INSTRUCTION_LABELS: Record<string, string> = {
	none: 'Entries enabled',
	stop_new_entries: 'Stop new entries',
	flatten: 'Flatten requested',
	managed_shutdown: 'Managed stop'
};

export type LifecycleAction = 'pause' | 'resume' | 'stop' | 'flatten' | 'reset-breakers';

export type LifecycleDialog = {
	action: LifecycleAction;
	title: string;
	/** Paragraph lines under the title; consequence-specific per action. */
	body: string[];
	/** Long-running confirm button label. */
	confirm: string;
	/** In-flight confirm label. */
	pending: string;
	/** Marks the confirm button with the destructive treatment. */
	danger: boolean;
	/** Whether this dialog selects between managed stop and flatten. */
	chooseStopMode: boolean;
};

const BREAKER_LABELS = {
	daily_loss: 'Daily-loss',
	drawdown: 'Drawdown'
} as const;

function breakerLabel(deployment: Deployment): string {
	const latched: string[] = [];
	if (deployment.daily_loss_latched) latched.push(BREAKER_LABELS.daily_loss);
	if (deployment.drawdown_latched) latched.push(BREAKER_LABELS.drawdown);
	return latched.join(' and ');
}

/** Note appended to resume dialogs when a latched breaker keeps entries blocked. */
export function resumeBreakerNote(deployment: Deployment): string | null {
	const latched = breakerLabel(deployment);
	return latched === ''
		? null
		: `${latched} breaker is latched. Resume changes the lifecycle to Running, but new entries remain blocked until the latch is reset separately.`;
}

function quoteOf(deployment: Deployment): string {
	return productIdQuote(deployment.product_id) ?? 'unknown quote';
}

/** Build the accessible title for a lifecycle confirmation dialog. */
export function lifecycleDialog(deployment: Deployment, action: LifecycleAction): LifecycleDialog {
	const modeLabel = deployment.mode === 'live' ? 'Live' : 'Paper';
	const market = marketLabel(deployment.product_id);
	const permanent =
		deployment.mode === 'live'
			? 'Live trading cannot be re-armed from this deployment after it is stopped.'
			: 'This deployment cannot be resumed after it is stopped.';
	switch (action) {
		case 'pause':
			return {
				action,
				title: `Pause ${modeLabel.toLowerCase()} deployment?`,
				body: [
					`New entries are blocked while paused. Existing inventory and protective orders remain active and continue to be supervised on ${market}.`
				],
				confirm: 'Pause entries',
				pending: 'Pausing…',
				danger: false,
				chooseStopMode: false
			};
		case 'resume': {
			const body = [
				`New risk-increasing orders may be submitted when the next eligible completed ${deployment.timeframe ?? ''} bar is processed. Existing inventory and protection are unchanged by this command.`
			];
			const breakerNote = resumeBreakerNote(deployment);
			if (breakerNote !== null) body.push(breakerNote);
			return {
				action,
				title: `Resume ${modeLabel.toLowerCase()} deployment?`,
				body,
				confirm: 'Resume entries',
				pending: 'Resuming…',
				danger: deployment.mode === 'live',
				chooseStopMode: false
			};
		}
		case 'stop':
			return {
				action,
				title: `Stop ${modeLabel.toLowerCase()} deployment?`,
				body: [
					permanent,
					`${deployment.status === 'stopped' ? 'Residual' : 'Open'} books: ${canonicalPositions(deployment).length} · working orders: ${workingOrderCount(deployment)} · snapshot ${deployment.updated_at}`
				],
				confirm: 'Stop with protection',
				pending: 'Stopping…',
				danger: true,
				chooseStopMode: true
			};
		case 'flatten':
			return {
				action,
				title: 'Flatten stopped deployment?',
				body: [
					'Submit marketable exits for any remaining inventory, then cancel remaining orders. This changes the shutdown instruction from Managed stop to Flatten; it does not restart the deployment.',
					'Exit price and fees are not guaranteed. Exits and cancellations are applied asynchronously by the worker.'
				],
				confirm: 'Flatten remaining exposure',
				pending: 'Requesting flatten…',
				danger: true,
				chooseStopMode: false
			};
		case 'reset-breakers': {
			const latched = breakerLabel(deployment);
			return {
				action,
				title: 'Reset breaker latches?',
				body: [
					`${latched} breaker ${latched.includes(' and ') ? 'latches are' : 'latch is'} currently blocking new entries.`,
					'Resetting clears the latch so entries may follow on the next eligible completed bar on this deployment. It does not change the lifecycle state, cancel protection, or undo the loss or drawdown that tripped the breaker. Verify the cause before resetting.'
				],
				confirm: 'Reset latches',
				pending: 'Resetting…',
				danger: false,
				chooseStopMode: false
			};
		}
	}
}

/** Outcome sentence for an accepted lifecycle mutation, before refresh confirms it. */
export function lifecycleAcceptedMessage(action: LifecycleAction): string {
	switch (action) {
		case 'pause':
			return 'Deployment paused. New entries are blocked; existing protection remains active.';
		case 'resume':
			return 'Deployment resumed. Entries may follow on the next eligible completed bar.';
		case 'stop':
			return 'Managed stop accepted. Protection remains active while residual exposure settles.';
		case 'flatten':
			return 'Flatten request accepted. Verifying remaining inventory and orders…';
		case 'reset-breakers':
			return 'Breaker latches reset. Entry eligibility follows the latest snapshot.';
	}
}

/**
 * Whether the "flatten remaining exposure" follow-up may be offered.
 *
 * Requires a stopped deployment whose instruction is still managed shutdown
 * with residual positions or working orders — read from a fresh snapshot,
 * never inferred from an older one.
 */
export function canOfferFlatten(deployment: Deployment): boolean {
	return (
		deployment.status === 'stopped' &&
		deployment.lifecycle_command === 'managed_shutdown' &&
		(canonicalPositions(deployment).length > 0 || workingOrderCount(deployment) > 0)
	);
}

/** Count of risk-increasing orders still resting on this snapshot. */
export function workingOrderCount(deployment: Deployment): number {
	return deployment.orders.filter(
		(order) => order.status === 'pending' || order.status === 'open' || order.status === 'unknown'
	).length;
}

/**
 * Human market label for a Coinbase product id.
 *
 * `UNI-USDC` renders `UNI / USDC` by reading the quote from the id itself —
 * never a blanket USD→USDC substitution. Ids without a dash render unchanged.
 */
export function marketLabel(productId: string): string {
	const quote = productIdQuote(productId);
	if (quote === null) return productId;
	return `${productId.slice(0, productId.length - quote.length - 1)} / ${quote}`;
}

/** Quote currency of a `BASE-QUOTE` product id, or null when there is no dash. */
export function productIdQuote(productId: string): string | null {
	const separator = productId.indexOf('-');
	if (separator === -1) return null;
	return productId.slice(separator + 1);
}

/** Label a quote-denominated amount with the product's actual quote currency. */
export function quoteAmountLabel(amount: string | null | undefined, productId: string): string {
	if (amount === null || amount === undefined || amount === '') return 'unknown';
	return `${amount} ${quoteOf({ product_id: productId } as Deployment)}`;
}

/** Sentence describing the last evaluated bar; unknown stays unknown. */
export function lastEvaluatedText(deployment: Deployment): string {
	const bar = deployment.last_evaluated_bar;
	if (bar === null || bar === undefined) return 'No completed bar evaluated yet.';
	const signal = deployment.last_signal ?? 'unknown';
	return `Completed bar ${bar} evaluated; signal: ${signal}.`;
}

/**
 * Status and entry-eligibility lines for the detail header.
 *
 * Eligibility derives from visible state only: pause, latched breakers, and
 * lifecycle instruction. `Unavailable` is rendered when the contract is
 * incomplete rather than guessing safe.
 */
export function eligibility(deployment: Deployment): {
	status: string;
	instruction: string;
	eligibility: string;
} {
	const instruction =
		LIFECYCLE_INSTRUCTION_LABELS[deployment.lifecycle_command] ?? deployment.lifecycle_command;
	let eligibility: string;
	if (deployment.status === 'stopped') {
		eligibility = 'Stopped — no entries';
	} else if (deployment.daily_loss_latched && deployment.drawdown_latched) {
		eligibility = 'Blocked by daily-loss and drawdown breakers';
	} else if (deployment.daily_loss_latched) {
		eligibility = 'Blocked by daily-loss breaker';
	} else if (deployment.drawdown_latched) {
		eligibility = 'Blocked by drawdown breaker';
	} else if (deployment.status === 'paused') {
		eligibility = 'Blocked by pause';
	} else if (deployment.status === 'running') {
		eligibility = 'Eligible';
	} else {
		eligibility = 'Unavailable';
	}
	return { status: deployment.status, instruction, eligibility };
}

/** Net performance sentence from the fill ledger, or an honest absent note. */
export function ledgerPerformanceText(deployment: Deployment): string {
	const ledger = deployment.ledger;
	if (!ledger) return 'Ledger summary unavailable.';
	const trades = `${ledger.trade_count} closed trade${ledger.trade_count === 1 ? '' : 's'}`;
	if (ledger.total_net_pnl === null || ledger.total_return_fraction === null) {
		return `${trades}. Net performance not yet computed.`;
	}
	return `${trades} · net P&L ${ledger.total_net_pnl} ${quoteOf(deployment)} · return ${(Number(ledger.total_return_fraction) * 100).toFixed(2)}%`;
}

/** The deployment's fingerprint, or an explicit unknown for discretionary rows. */
export function fingerprintText(deployment: Deployment): string {
	return deployment.strategy_fingerprint ?? 'unknown (discretionary)';
}

/**
 * Deployments of this same strategy running a different published fingerprint.
 *
 * The version-mixup guard: the current version's evidence is filtered by exact
 * fingerprint; everything else lands here so it stays discoverable but never
 * mixed into this version's runtime rows.
 */
export function otherVersionDeployments(
	deployments: Deployment[],
	deployment: Deployment
): Deployment[] {
	if (deployment.strategy_id === null || deployment.strategy_fingerprint === null) return [];
	return deployments.filter(
		(candidate) =>
			candidate.strategy_id === deployment.strategy_id &&
			candidate.id !== deployment.id &&
			candidate.strategy_fingerprint !== null &&
			candidate.strategy_fingerprint !== deployment.strategy_fingerprint
	);
}

/** Newest-first pagination state for orders/fills cursor pages. */
export type LedgerPaging<T> = {
	rows: T[];
	nextCursor: string | null;
};

/** Link to the immutable strategy version that exactly matches this fingerprint. */
export function strategyVersionLink(
	deployment: Deployment
): { href: string; fingerprint: string } | null {
	if (deployment.strategy_id === null || deployment.strategy_fingerprint === null) return null;
	return {
		href: `/deploy?strategy=${encodeURIComponent(deployment.strategy_id)}&strategy_fingerprint=${encodeURIComponent(deployment.strategy_fingerprint)}`,
		fingerprint: deployment.strategy_fingerprint
	};
}

/** Link to the strategy identity on Deploy (picker-level, not version-exact). */
export function strategyIdentityLink(deployment: Deployment): string | null {
	if (deployment.strategy_id === null) return null;
	return `/deploy?strategy=${encodeURIComponent(deployment.strategy_id)}`;
}

/**
 * Resolve the fingerprint a launch surface should preselect.
 *
 * An explicit `?strategy_fingerprint=` request is honored only when it really
 * is a published version of this strategy. An unknown explicit fingerprint
 * fails closed (`selected: ''`, `blocked: true`) so no launch can silently run
 * against the latest version; without the query parameter the latest version is
 * the normal default.
 */
export function resolveRequestedFingerprint(
	requested: string,
	published: { strategy_fingerprint: string }[]
): { selected: string; blocked: boolean; notice: string | null } {
	if (requested === '') {
		return {
			selected: published[published.length - 1]?.strategy_fingerprint ?? '',
			blocked: false,
			notice: null
		};
	}
	const match = published.find((version) => version.strategy_fingerprint === requested);
	if (match !== undefined) {
		return { selected: requested, blocked: false, notice: null };
	}
	if (published.length === 0) {
		return {
			selected: '',
			blocked: true,
			notice:
				'The requested version is not published for this strategy, and no published version exists; nothing can be launched.'
		};
	}
	return {
		selected: '',
		blocked: true,
		notice:
			'The requested version is not published for this strategy. Launch is blocked so a different immutable version cannot be started by mistake. Pick a published version explicitly to continue.'
	};
}

/** Order display row collapsing the API product fallback into one value. */
export function orderRowLabel(deployment: Deployment, order: DeploymentOrder): string {
	return order.product_id || deployment.product_id;
}

/** Currency suffix for a provenance-labeled performance amount, or '' when unknown. */
export function performanceCurrencySuffix(
	currency: 'USD' | 'USDC' | 'USDT' | null | undefined
): string {
	return currency === null || currency === undefined ? '' : ` ${currency}`;
}

/**
 * Whether the operator report drawdown figure carries a caveat.
 *
 * Paper/live drawdown is computed from fill-event marks, not a bar equity
 * curve, so it understates intra-bar drawdown; state that instead of
 * presenting it as a backtest-grade number.
 */
export function drawdownIsCaveated(performance: {
	mode: string;
	maximum_drawdown_fraction: string | null;
}): boolean {
	return performance.mode !== 'backtest' && performance.maximum_drawdown_fraction !== null;
}

/**
 * One-line operator performance reading with explicit provenance.
 *
 * Never relabels an unknown currency and never presents an incomplete mark as
 * final: unknown provenance reads `unknown quote`, and `mark_complete: false`
 * appends the open-position caveat.
 */
export function performanceReportText(performance: {
	mode: string;
	currency: 'USD' | 'USDC' | 'USDT' | null;
	trade_count: number | null;
	total_net_pnl: string | null;
	total_return_fraction: string | null;
	mark_complete: boolean | null;
}): string {
	if (performance.total_net_pnl === null || performance.total_return_fraction === null) {
		return 'Net performance not yet computed from the fill ledger.';
	}
	const currency = performanceCurrencySuffix(performance.currency) || ' unknown quote';
	const trades =
		performance.trade_count === null
			? 'trade count unknown'
			: `${performance.trade_count} closed trade${performance.trade_count === 1 ? '' : 's'}`;
	const markNote =
		performance.mark_complete === false ? ' · open inventory without a mark — not final' : '';
	return `${trades} · net P&L ${performance.total_net_pnl}${currency} · return ${(Number(performance.total_return_fraction) * 100).toFixed(2)}%${markNote}`;
}

/** Exact-version evidence links: every link carries the fingerprint. */
export type EvidenceLink = { label: string; href: string };

/**
 * Evidence links for this exact version.
 *
 * Backtests and research are filtered by the deployment's exact fingerprint so
 * the operator sees this version's evidence only. Discretionary deployments
 * without a fingerprint have no version-scoped evidence.
 */
export function exactVersionEvidenceLinks(deployment: Deployment): EvidenceLink[] {
	const fingerprint = deployment.strategy_fingerprint;
	if (fingerprint === null) return [];
	return [
		{
			label: 'Backtests of this version',
			href: `/backtests?strategy_fingerprint=${encodeURIComponent(fingerprint)}`
		},
		{
			label: 'Research for this version',
			href: `/research?strategy=${encodeURIComponent(deployment.strategy_id ?? '')}&strategy_fingerprint=${encodeURIComponent(fingerprint)}`
		}
	];
}

/**
 * Whether the shown evidence can be called comprehensive.
 *
 * The operator performance report and the bounded promotion surface see only a
 * window of history; the page must never claim completeness from them.
 */
export const EVIDENCE_BOUNDED_NOTE =
	'Evidence shown is bounded (recent history and persisted studies only); it is not a comprehensive record.';
