/**
 * Portfolios (ADR 0088, ADR 0091): typed API client and pure view helpers.
 *
 * A portfolio is a set of sleeves (one strategy each, with a capital weight)
 * under shared limits, with manager settings and an append-only journal. Every
 * mutation is revision-guarded; a stale revision is a 409 the page answers by
 * reloading. Money and fractions are canonical decimal strings: sums and
 * comparisons stay exact (BigInt), and floats appear only in chart geometry.
 * Deploying starts one bot per sleeve; strategies place every trade. The
 * manager agent only proposes (rebalance, pause, resume, add a sleeve); nothing
 * here places an order.
 *
 * This module is the public barrel. The code lives in focused modules that never
 * import this barrel:
 * - `portfolios-types.ts`: API payload and request types.
 * - `portfolios-api.ts`: HTTP client, structured errors, and error readers.
 * - `portfolios-decimal.ts`: exact decimal-string math and allocation checks.
 * - `portfolios-format.ts`: fixed copy, labels, and number/date text.
 * - `portfolios-charts.ts`: allocation bars and equity-curve geometry.
 * - `portfolios-view.ts`: tabs, actions, proposals, backtest and picker view logic.
 */
export { QUOTE_CURRENCIES } from './portfolios-types';
export type {
	AssetAllocation,
	AssetExposure,
	BacktestJobStatus,
	BacktestProblem,
	BacktestRunInput,
	BasketLeg,
	CorrelationPair,
	JournalChange,
	JournalDetail,
	JournalEntry,
	JournalKind,
	JournalPage,
	ManagerPermissions,
	ManagerSettings,
	OverlapPair,
	PlannedSleeve,
	Portfolio,
	PortfolioActionResponse,
	PortfolioAllocation,
	PortfolioBacktestAccepted,
	PortfolioBacktestCosts,
	PortfolioBacktestDetail,
	PortfolioBacktestJob,
	PortfolioBacktestListing,
	PortfolioBacktestListResponse,
	PortfolioBacktestResult,
	PortfolioBacktestSummary,
	PortfolioBasket,
	PortfolioBreaker,
	PortfolioCorrelation,
	PortfolioCreateInput,
	PortfolioCurveMetrics,
	PortfolioDeployment,
	PortfolioDeploymentState,
	PortfolioDialogAction,
	PortfolioEquityPoint,
	PortfolioExposure,
	PortfolioLimits,
	PortfolioListResponse,
	PortfolioMode,
	PortfolioOverlap,
	PortfolioSleeve,
	PortfolioSleeveResult,
	PortfolioUpdateInput,
	Proposal,
	ProposalChange,
	ProposalEvidence,
	ProposalKind,
	ProposalListResponse,
	ProposalResponse,
	ProposalStatus,
	QuoteCurrency,
	SetWeightsInput,
	SleeveAddInput,
	SleeveBook,
	SleeveDeployment,
	SleeveIssueCode,
	SleeveOutcome,
	SleeveOutcomeKind
} from './portfolios-types';
export {
	addSleeve,
	backtestProblems,
	createPortfolio,
	decideProposal,
	errorText,
	fetchFillComparisons,
	fetchPortfolio,
	fetchPortfolioBacktest,
	fetchPortfolioBacktestJob,
	fetchPortfolioDeployment,
	isRevisionConflict,
	isStorageUnavailable,
	listJournal,
	listPortfolioBacktestJobs,
	listPortfolioBacktests,
	listPortfolios,
	listProposals,
	portfolioAction,
	portfolioApiError,
	PortfolioApiError,
	portfolioErrorCode,
	removeSleeve,
	resetPortfolioBreaker,
	setWeights,
	startPortfolio,
	startProblems,
	submitPortfolioBacktest,
	updatePortfolio
} from './portfolios-api';
export {
	checkAllocation,
	fractionToPercentInput,
	multiplyDecimals,
	percentInputToFraction,
	quoteInput,
	remainingWeight,
	roundDecimal,
	shiftDecimal,
	sumFractions
} from './portfolios-decimal';
export type { AllocationCheck } from './portfolios-decimal';
export {
	baseAsset,
	botStatusText,
	breakerLabel,
	coefficientText,
	CONFLICT_RELOADED,
	contributionPoints,
	deploymentStateLabel,
	drawdownPercent,
	durationText,
	jobProgressText,
	journalActorText,
	journalKindLabel,
	largestAssetText,
	LIMITS_NOTE,
	LIMITS_ORDER,
	MANAGER_NEVER,
	MANAGER_NOTE,
	modeLabel,
	pausedByBreaker,
	portfolioSubtitle,
	proposalKindLabel,
	proposalStatusText,
	quoteText,
	ratioText,
	signedPercent,
	signedQuote,
	sleeveIssueText,
	sleevePositionText,
	START_NOTE,
	utcMinute,
	weightPercent,
	windowText
} from './portfolios-format';
export { allocationAriaLabel, allocationBars, curvePaths } from './portfolios-charts';
export type { AllocationBar, ChartPaths } from './portfolios-charts';
export {
	approvalNeedsLiveAck,
	askWhyPrompt,
	bestSleeve,
	isJobActive,
	pairCoefficient,
	parseTab,
	pickerOptions,
	PORTFOLIO_TABS,
	portfolioActions,
	portfolioRunPnl,
	sleeveBots
} from './portfolios-view';
export type { PickerOption, PortfolioTab } from './portfolios-view';
