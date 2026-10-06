/** Truthful API inventory metadata for hermetic browser route fixtures. */
export function inventoryPageFixture<T>(
	rows: T[],
	options: { limit?: number; offset?: number; total?: number; nextCursor?: string | null } = {}
) {
	return {
		deployments: rows,
		returned: rows.length,
		limit: options.limit ?? 200,
		offset: options.offset ?? 0,
		total: options.total ?? rows.length,
		has_more: options.nextCursor !== undefined && options.nextCursor !== null,
		next_cursor: options.nextCursor ?? null,
		as_of: '2026-10-06T00:00:00Z',
		order: 'created_at_desc_id_desc',
		fingerprint: 'hermetic-inventory-membership'
	};
}
