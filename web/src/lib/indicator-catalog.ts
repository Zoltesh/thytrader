/**
 * Typed indicator catalog for the strategy builder.
 *
 * The catalog data (kinds, labels, categories, one-line help, input policy,
 * parameter bounds and defaults, output series, warmup formulas) is generated
 * from the Python registry into `generated/indicator-catalog.json` by
 * `scripts/export_indicator_catalog.py`; the backend test suite fails when the
 * checked-in file drifts. This module validates that file once at load, adds the
 * warmup formulas the builder evaluates locally (cross-checked against the
 * backend in `indicator-catalog.spec.ts`), parameter validation, search, and
 * readable labels such as `Supertrend(10, 3) · direction`.
 *
 * This module is the public barrel. The code lives in focused modules that never
 * import this barrel:
 * - `indicator-catalog-types.ts`: kinds, parameter names, and entry/spec types.
 * - `indicator-catalog-parse.ts`: validation of the generated catalog document.
 * - `indicator-catalog-entries.ts`: the catalog, lookups, defaults, and parameter access.
 * - `indicator-catalog-warmup.ts`: warmup formulas and bar-lag offsets.
 * - `indicator-catalog-validation.ts`: parameter, constraint, and input checks.
 * - `indicator-catalog-search.ts`: picker search and category grouping.
 * - `indicator-catalog-labels.ts`: readable indicator and operand labels.
 */
export {
	INDICATOR_CATEGORIES,
	INDICATOR_KINDS,
	INDICATOR_PARAMETER_NAMES,
	MAX_INDICATOR_OFFSET
} from './indicator-catalog-types';
export type {
	IndicatorCatalogEntry,
	IndicatorCategory,
	IndicatorField,
	IndicatorInputMode,
	IndicatorKindValue,
	IndicatorParameterName,
	IndicatorParameters,
	IndicatorParameterSpec
} from './indicator-catalog-types';
export { parseIndicatorCatalog } from './indicator-catalog-parse';
export {
	INDICATOR_CATALOG,
	catalogEntry,
	defaultInputFor,
	defaultParameters,
	findCatalogEntry,
	isIndicatorKind,
	kindLabel,
	readParameter,
	writeParameter
} from './indicator-catalog-entries';
export { indicatorWarmupBars, isValidOffset, offsetOf } from './indicator-catalog-warmup';
export {
	inputMatchesKind,
	isValidParameterValue,
	parameterProblem,
	parameterProblems
} from './indicator-catalog-validation';
export { groupedIndicatorKinds, searchIndicatorKinds } from './indicator-catalog-search';
export {
	indicatorDisplayLabel,
	offsetText,
	operandDisplayLabel,
	type DisplayableIndicator
} from './indicator-catalog-labels';
