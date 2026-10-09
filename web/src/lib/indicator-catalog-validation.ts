/**
 * Parameter and input validation against the catalog: bounds, decimal text, unknown
 * parameters, and cross-parameter constraints. Re-exported by `indicator-catalog.ts`.
 */
import { catalogEntry, isBlank, numberOf, readParameter } from './indicator-catalog-entries';
import type {
	IndicatorCatalogEntry,
	IndicatorField,
	IndicatorKindValue,
	IndicatorParameterSpec
} from './indicator-catalog-types';

const DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;

function boundNumber(value: number | string | null): number | null {
	return value === null ? null : Number(value);
}

function describeRange(spec: IndicatorParameterSpec): string {
	const minimum = spec.minimum;
	const maximum = spec.maximum;
	if (minimum !== null && maximum !== null) {
		return spec.exclusive_minimum
			? `greater than ${minimum} and at most ${maximum}`
			: `between ${minimum} and ${maximum}`;
	}
	if (minimum !== null) {
		return spec.exclusive_minimum ? `greater than ${minimum}` : `at least ${minimum}`;
	}
	if (maximum !== null) return `at most ${maximum}`;
	return '';
}

function withinBounds(spec: IndicatorParameterSpec, value: number): boolean {
	const minimum = boundNumber(spec.minimum);
	const maximum = boundNumber(spec.maximum);
	if (minimum !== null && (spec.exclusive_minimum ? value <= minimum : value < minimum)) {
		return false;
	}
	return maximum === null || value <= maximum;
}

/** One parameter's problem, or null when the value satisfies its spec. */
export function parameterProblem(
	spec: IndicatorParameterSpec,
	value: unknown,
	subject: string
): string | null {
	const name = `${subject} ${spec.label.toLowerCase()}`;
	if (isBlank(value)) return spec.optional ? null : `${name} is required.`;
	if (spec.value_type === 'integer') {
		const parsed = typeof value === 'number' ? value : Number(value);
		const range = describeRange(spec);
		if (!Number.isInteger(parsed) || !withinBounds(spec, parsed)) {
			return range === '' ? `${name} must be an integer.` : `${name} must be an integer ${range}.`;
		}
		return null;
	}
	if (typeof value !== 'string' || !DECIMAL_PATTERN.test(value)) {
		return `${name} must be a plain decimal number.`;
	}
	if (!withinBounds(spec, Number(value))) return `${name} must be ${describeRange(spec)}.`;
	return null;
}

/** True when `value` satisfies one parameter spec (blank only when optional). */
export function isValidParameterValue(spec: IndicatorParameterSpec, value: unknown): boolean {
	return parameterProblem(spec, value, '') === null;
}

/**
 * Evaluate one catalog constraint chain such as `tenkan_period < kijun_period <
 * senkou_b_period` or `step <= max_step`. Unparseable chains are ignored; the
 * server validates the document either way.
 */
function constraintHolds(constraint: string, parameters: object): boolean {
	const tokens = constraint.trim().split(/\s+/);
	if (tokens.length < 3 || tokens.length % 2 === 0) return true;
	for (let index = 1; index < tokens.length; index += 2) {
		const operator = tokens[index];
		const left = numberOf(parameters, tokens[index - 1]);
		const right = numberOf(parameters, tokens[index + 1]);
		if (!Number.isFinite(left) || !Number.isFinite(right)) return true;
		if (operator === '<' && !(left < right)) return false;
		if (operator === '<=' && !(left <= right)) return false;
	}
	return true;
}

function constraintText(entry: IndicatorCatalogEntry, constraint: string): string {
	const labels = new Map<string, string>(
		entry.parameters.map((spec) => [spec.name, spec.label.toLowerCase()])
	);
	return constraint
		.trim()
		.split(/\s+/)
		.map((token) => labels.get(token) ?? token)
		.join(' ');
}

/**
 * Every parameter problem for one declaration: missing or out-of-range values,
 * non-decimal text, parameters the kind does not take, and broken constraints.
 */
export function parameterProblems(
	kind: IndicatorKindValue,
	parameters: object,
	subject: string
): string[] {
	const entry = catalogEntry(kind);
	const problems: string[] = [];
	const declared = new Set<string>(entry.parameters.map((spec) => spec.name));
	for (const [name, value] of Object.entries(parameters)) {
		if (!declared.has(name) && !isBlank(value)) {
			problems.push(`${subject} ${entry.label} does not take ${name}.`);
		}
	}
	for (const spec of entry.parameters) {
		const problem = parameterProblem(spec, readParameter(parameters, spec.name), subject);
		if (problem !== null) problems.push(problem);
	}
	if (problems.length > 0) return problems;
	for (const constraint of entry.constraints) {
		if (!constraintHolds(constraint, parameters)) {
			problems.push(`${subject} ${entry.label} needs ${constraintText(entry, constraint)}.`);
		}
	}
	return problems;
}

function sameFields(left: unknown, right: readonly IndicatorField[]): boolean {
	return (
		Array.isArray(left) &&
		left.length === right.length &&
		left.every((field, index) => field === right[index])
	);
}

/** True when `input` is what the kind accepts (one source, the locked tuple, or none). */
export function inputMatchesKind(entry: IndicatorCatalogEntry, input: unknown): boolean {
	if (entry.input_mode === 'none') return input === undefined || input === null;
	if (entry.input_mode === 'configurable') {
		return typeof input === 'string' && (entry.inputs as string[]).includes(input);
	}
	const locked = entry.default_input;
	if (locked === null) return false;
	return Array.isArray(locked) ? sameFields(input, locked) : input === locked;
}
