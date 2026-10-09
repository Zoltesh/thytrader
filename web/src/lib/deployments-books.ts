/**
 * Canonical books of one deployment: positions and instrument runtimes in product order
 * (falling back to the legacy single-position fields), capital text, and book-total
 * reconciliation. Re-exported by `deployments.ts`.
 */
import type {
	Deployment,
	DeploymentFill,
	DeploymentInstrumentRuntime,
	DeploymentOrder,
	DeploymentPosition
} from './deployments-types';

const WORKING_ORDER_STATUSES = new Set(['pending', 'open', 'unknown']);

export function canonicalPositions(deployment: Deployment): DeploymentPosition[] {
	if (deployment.positions && deployment.positions.length > 0) {
		return [...deployment.positions].sort((left, right) =>
			left.product_id.localeCompare(right.product_id)
		);
	}
	if (deployment.position) {
		return [
			{
				...deployment.position,
				product_id: deployment.position.product_id || deployment.product_id,
				protection_status: deployment.position.protection_status ?? 'unknown',
				compatibility_focus: true
			}
		];
	}
	return [];
}

export function canonicalBooks(deployment: Deployment): DeploymentInstrumentRuntime[] {
	if (deployment.instrument_runtimes && deployment.instrument_runtimes.length > 0) {
		return [...deployment.instrument_runtimes].sort((left, right) =>
			left.product_id.localeCompare(right.product_id)
		);
	}
	return [
		{
			product_id: deployment.product_id,
			phase: deployment.phase,
			last_evaluated_bar: deployment.last_evaluated_bar,
			last_signal: deployment.last_signal,
			pending_entry_bars: deployment.pending_entry_bars,
			bars_held: deployment.bars_held,
			cooldown_bars_remaining: 0
		}
	];
}

export function capitalSummary(deployment: Deployment): string | null {
	const capital = deployment.capital;
	if (!capital) {
		return null;
	}
	const parts: string[] = [];
	if (capital.allocated_capital) {
		parts.push(`allocated ${capital.allocated_capital}`);
	}
	if (deployment.mode === 'live') {
		if (capital.venue_available_quote) {
			parts.push(`venue ${capital.venue_available_quote}`);
		} else {
			parts.push('venue unknown');
		}
	}
	if (capital.performance_equity) {
		parts.push(`equity ${capital.performance_equity}`);
	}
	if (capital.inventory_cost) {
		parts.push(`inventory ${capital.inventory_cost}`);
	}
	return parts.length > 0 ? parts.join(' · ') : null;
}

export function bookTotalsReconcile(deployment: Deployment): boolean {
	const totals = deployment.book_totals;
	if (!totals) return true;
	const working = deployment.orders.filter((order) =>
		WORKING_ORDER_STATUSES.has(order.status)
	).length;
	return (
		totals.open_books === canonicalPositions(deployment).length &&
		totals.working_orders === working &&
		totals.fill_count === deployment.fills.length
	);
}

export function orderProductId(deployment: Deployment, order: DeploymentOrder): string {
	return order.product_id || deployment.product_id;
}

export function fillProductId(deployment: Deployment, fill: DeploymentFill): string {
	return fill.product_id || deployment.product_id;
}
