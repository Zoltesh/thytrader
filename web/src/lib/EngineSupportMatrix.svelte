<script lang="ts">
	import { ENGINE_SUPPORT } from './strategy-insight';
</script>

<table class="support-matrix" aria-label="Engine support matrix">
	<thead>
		<tr>
			<th scope="col">Setting</th>
			<th scope="col">V1</th>
			<th scope="col">V2</th>
			<th scope="col">V3</th>
			<th scope="col">V4</th>
		</tr>
	</thead>
	<tbody>
		{#each ENGINE_SUPPORT as row (row.label)}
			<tr>
				<td>
					<strong>{row.label}</strong>
					<small>{row.note}</small>
				</td>
				<td
					><span class:support-yes={row.v1} class:support-no={!row.v1}
						>{row.v1 ? 'Supported' : 'Unsupported'}</span
					></td
				>
				<td
					><span class:support-yes={row.v2} class:support-no={!row.v2}
						>{row.v2 ? 'Supported' : 'Unsupported'}</span
					></td
				>
				<td
					><span class:support-yes={row.v3} class:support-no={!row.v3}
						>{row.v3 ? 'Supported' : 'Unsupported'}</span
					></td
				>
				<td
					><span class:support-yes={row.v4} class:support-no={!row.v4}
						>{row.v4 ? 'Supported' : 'Unsupported'}</span
					></td
				>
			</tr>
		{/each}
	</tbody>
</table>

<style>
	.support-matrix {
		width: 100%;
		border-collapse: collapse;
		font-size: 11px;
	}
	th,
	td {
		padding: 8px 7px;
		border-top: 0;
		border-bottom: 1px solid var(--line-2);
		font-size: inherit;
		text-align: left;
		vertical-align: top;
	}
	th {
		color: var(--muted);
		font-weight: 600;
	}
	th:not(:first-child),
	td:not(:first-child) {
		width: 74px;
		text-align: center;
	}
	strong {
		display: block;
		color: var(--text);
		font-weight: 550;
	}
	small {
		display: block;
		margin-top: 2px;
		color: var(--faint);
		line-height: 1.35;
	}
	.support-yes,
	.support-no {
		display: inline-block;
		border-radius: 999px;
		padding: 3px 7px;
		font-size: 10px;
		font-weight: 600;
	}
	.support-yes {
		background: color-mix(in srgb, var(--accent) 14%, transparent);
		color: var(--pos);
	}
	.support-no {
		background: color-mix(in srgb, var(--neg) 14%, transparent);
		color: var(--neg);
	}
</style>
