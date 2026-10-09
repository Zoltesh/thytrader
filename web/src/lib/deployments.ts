/**
 * Deployments (bots): typed API client and canonical book helpers.
 *
 * This module is the public barrel. The code lives in focused modules that never
 * import this barrel:
 * - `deployments-types.ts`: deployment, book, order, fill, page, performance and twin types.
 * - `deployments-http.ts`: the JSON request helper (CSRF session and mutation headers).
 * - `deployments-books.ts`: canonical positions and books, capital text, book-total checks.
 * - `deployments-inventory.ts`: fenced inventory pages and the complete fail-closed walk.
 * - `deployments-api.ts`: ledger pages, performance, lifecycle commands, orders and twins.
 */
export { positionStateLabel, type PositionState } from '$lib/position-state';
export type {
	Deployment,
	DeploymentBookTotals,
	DeploymentCapital,
	DeploymentFill,
	DeploymentInstrumentRuntime,
	DeploymentLedgerPage,
	DeploymentListPage,
	DeploymentOrder,
	DeploymentPosition,
	DeploymentTwinLink,
	DeploymentTwinResponse,
	OperatorPerformance,
	OperatorPerformanceReport
} from './deployments-types';
export {
	bookTotalsReconcile,
	canonicalBooks,
	canonicalPositions,
	capitalSummary,
	fillProductId,
	orderProductId
} from './deployments-books';
export {
	inventoryPageFromBody,
	listAllDeployments,
	listDeployments,
	listDeploymentsPage,
	listStrategyDeployments
} from './deployments-inventory';
export {
	createDeployment,
	fetchDeployment,
	fetchDeploymentPerformance,
	fetchDeploymentTwin,
	linkDeploymentTwin,
	listDeploymentFills,
	listDeploymentOrders,
	pauseDeployment,
	placeDiscretionaryOrder,
	resetBreakerLatches,
	resumeDeployment,
	stopDeployment,
	unlinkDeploymentTwin
} from './deployments-api';
