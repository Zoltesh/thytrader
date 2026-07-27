import { describe, expect, it } from 'vitest';
import { formatUsd, permissionLabel } from './portfolio';

describe('portfolio presentation', () => {
	it('formats exact decimal strings as USD', () => {
		expect(formatUsd('98542.17')).toBe('$98,542.17');
	});

	it('formats detected permission labels for display', () => {
		expect(permissionLabel('transfer')).toBe('Transfer');
	});
});
