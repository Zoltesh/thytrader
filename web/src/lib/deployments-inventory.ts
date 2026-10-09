/**
 * Deployment inventory reads: fenced offset/cursor pages, the complete fail-closed walk,
 * and one strategy's deployments. Re-exported by `deployments.ts`.
 */
import { request } from './deployments-http';
import type { Deployment, DeploymentListPage } from './deployments-types';

/**
 * Complete discretionary/strategy inventory.
 *
 * This used to return the API's default 50-row page. It now follows the stable
 * snapshot and throws if the walk cannot be completed, so a caller cannot treat
 * a prefix as the fleet.
 */
export async function listDeployments(): Promise<Deployment[]> {
	return listAllDeployments();
}

/**
 * Fetch one offset page of the deployment inventory.
 *
 * `hasMore` is `returned === limit`, the only signal the bounded list contract
 * provides; a page shorter than the limit is the end of the inventory.
 */
export async function listDeploymentsPage(
	limit: number,
	offset: number,
	options: { strategyId?: string; asOf?: string; cursor?: string } = {}
): Promise<DeploymentListPage> {
	const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
	if (options.strategyId !== undefined) params.set('strategy_id', options.strategyId);
	if (options.asOf !== undefined && options.asOf !== '') params.set('as_of', options.asOf);
	if (options.cursor !== undefined) params.set('cursor', options.cursor);
	const body = await request<{
		deployments: Deployment[];
		returned?: number;
		has_more?: boolean;
		as_of?: string;
		total?: number;
		order?: string;
		fingerprint?: string;
		next_cursor?: string | null;
	}>(`/api/v1/deployments?${params.toString()}`);
	return inventoryPageFromBody(body, limit);
}

/** Validate one inventory page. A missing `has_more` on a full page stays incomplete. */
export function inventoryPageFromBody(
	body: {
		deployments?: Deployment[];
		returned?: number;
		has_more?: boolean;
		as_of?: string;
		total?: number;
		order?: string;
		fingerprint?: string;
		next_cursor?: string | null;
	},
	limit: number
): DeploymentListPage {
	if (!Array.isArray(body.deployments)) {
		throw new Error('Deployment inventory response omitted its rows.');
	}
	const returned = body.deployments.length;
	if (returned > limit) {
		throw new Error(`Deployment inventory exceeded the requested page limit (${limit}).`);
	}
	if (body.returned !== undefined && body.returned !== returned) {
		throw new Error(
			`Deployment inventory is inconsistent: the server counted ${body.returned} rows but sent ${returned}.`
		);
	}
	if (
		typeof body.has_more !== 'boolean' ||
		typeof body.as_of !== 'string' ||
		!Number.isInteger(body.total) ||
		(body.total ?? -1) < 0 ||
		body.order !== 'created_at_desc_id_desc' ||
		typeof body.fingerprint !== 'string' ||
		body.returned === undefined ||
		!('next_cursor' in body)
	) {
		throw new Error('Deployment inventory omitted checked pagination metadata; incomplete.');
	}
	const hasMore = body.has_more;
	if (
		(hasMore && (typeof body.next_cursor !== 'string' || body.next_cursor.length === 0)) ||
		(!hasMore && body.next_cursor !== null)
	) {
		throw new Error('Deployment inventory cursor contradicts has_more; incomplete.');
	}
	if (hasMore && returned === 0) {
		throw new Error('Deployment inventory returned an empty page while claiming more rows.');
	}
	return {
		deployments: body.deployments,
		hasMore,
		asOf: body.as_of ?? null,
		total: body.total ?? null,
		order: body.order ?? null,
		fingerprint: body.fingerprint ?? null,
		nextCursor: body.next_cursor ?? null
	};
}

/** Backend page ceiling for the bounded deployment inventory read. */
const INVENTORY_PAGE_SIZE = 200;
/** Hard stop so a misbehaving server cannot keep a consumer paging forever. */
const MAX_INVENTORY_PAGES = 25;

/**
 * Fetch the complete deployment inventory, cursor/offset page by page.
 *
 * Selection flows that must never silently truncate (exact-version runtime
 * grouping, other-version discovery) follow `hasMore` until a short page ends
 * the inventory. Fails closed at the page cap instead of presenting a prefix.
 */
export async function listAllDeployments(
	onPage?: (rows: Deployment[]) => void,
	options: { strategyId?: string } = {}
): Promise<Deployment[]> {
	const rows: Deployment[] = [];
	let first: DeploymentListPage | null = null;
	let cursor: string | undefined;
	const seenIds = new Set<string>();
	const seenCursors = new Set<string>();
	for (let page = 0; page < MAX_INVENTORY_PAGES; page += 1) {
		const result = await listDeploymentsPage(INVENTORY_PAGE_SIZE, 0, { ...options, cursor });
		if (
			result.fingerprint === null ||
			result.asOf === null ||
			result.total === null ||
			result.order !== 'created_at_desc_id_desc'
		) {
			throw new Error('Deployment inventory omitted its membership fence; incomplete.');
		}
		if (first === null) first = result;
		if (
			result.fingerprint !== first.fingerprint ||
			result.total !== first.total ||
			result.asOf !== first.asOf
		) {
			throw new Error('Deployment inventory changed during paging; incomplete. Restart read.');
		}
		for (const row of result.deployments) {
			if (!row.id || seenIds.has(row.id))
				throw new Error('Deployment inventory duplicated or omitted identity; incomplete.');
			seenIds.add(row.id);
		}
		rows.push(...result.deployments);
		if (!result.hasMore) {
			if (result.nextCursor !== null || rows.length !== first.total)
				throw new Error('Deployment inventory omitted rows; incomplete.');
			onPage?.([...rows]);
			return rows;
		}
		if (!result.nextCursor || seenCursors.has(result.nextCursor))
			throw new Error('Deployment inventory missing/repeated cursor; incomplete.');
		seenCursors.add(result.nextCursor);
		cursor = result.nextCursor;
	}
	throw new Error('Deployment inventory truncated: exceeded the 25-page fetch cap.');
}

/** Every deployment of one strategy (`?strategy_id=`), followed across bounded pages. */
export async function listStrategyDeployments(strategyId: string): Promise<Deployment[]> {
	const rows = await listAllDeployments(undefined, { strategyId });
	// Defensive: keep only rows the server says belong to this strategy.
	return rows.filter((deployment) => deployment.strategy_id === strategyId);
}
