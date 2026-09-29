# 0079: Four-destination workstation shell, Agent side panel, command palette, and design tokens

- Status: Accepted
- Date: 2026-09-29
- Supersedes in part: [0053](0053-workstation-ia-write-only-coinbase-credentials.md) (first-class
  top-level Research, Deploy, and Journals destinations; Chat as a top-level nav page)
- Relates to: [0001](0001-sveltekit-frontend.md), [0030](0030-agent-e2e-primary-surface.md),
  [0051](0051-in-app-operator-chat.md), [0054](0054-trade-reason-journals.md),
  [0055](0055-yaml-settings-runtime-reloadable-yolo.md),
  [0061](0061-application-trust-boundary.md)

## Context

ADR 0053 gave every workstation page a top-level link. The top bar grew to twelve links in three
verb groups (Watch / Work / Govern) plus a static context pill. Operators said the bar read as a
site map, not a workstation. Research, Backtests, and Deploy are all steps on one strategy.
Journals, Audit, Memory, and Settings are opened rarely. Chat was a separate page, so the agent
could not see the page the operator was looking at.

Colors were hardcoded in about 26 files (about 130 distinct hex values). A light theme was
impossible, and some small labels failed WCAG AA contrast.

## Decision

**Information architecture.** A left rail (about 220px) has four primary destinations:

| Rail item  | Route          | Also current for                                   |
| ---------- | -------------- | -------------------------------------------------- |
| Home       | `/`            | (exact)                                            |
| Strategies | `/strategies`  | `/strategies/*`, `/research`, `/backtests`, `/deploy` |
| Portfolio  | `/deployments` | `/deployments/*`                                   |
| Trade      | `/trade`       |                                                    |

A collapsible **System** group at the bottom of the rail holds Settings (`/settings`), Audit log
(`/audit`), Journal (`/journals`), and Memory & why-trade (`/memory`). It opens by itself on those
routes. Every existing route and deep link still resolves. The rail uses
`<nav aria-label="Primary navigation">` with `aria-current="page"`. The top bar shows a
`section / page` breadcrumb, a command-palette trigger, the Agent toggle, and a theme toggle. The
static context pill is removed. The rail shows no worker-health line yet: no cheap existing
endpoint supplies one, and the shell must not fake status.

Research, Backtests, and Deploy fold under Strategies here. A later slice turns them into stages of
a strategy workspace. Deployments is the Portfolio destination.

**Agent side panel.** The operator chat (ADR 0051) is one component, `OperatorChatPanel`. The top
bar's Agent button opens it in a right-side panel (about 380px) on every route. The panel shows a
"Looking at: <page>" line. Its open or closed state is saved per viewer in `localStorage`. `/chat`
stays as the full-page view of the same component, so deep links and tests keep working. The panel
does not render on `/chat`. Safety is unchanged in both hosts:

- The LLM key is write-only.
- Every mutation waits under "Confirmation required".
- Live start, live resume, and live place-order need the understand-live checkbox.
- CSRF follows ADR 0061.

The panel gives the agent no new authority.

**Command palette.** ⌘K / Ctrl+K or the top-bar trigger opens a modal dialog. It lists every
destination, every System page, the folded pages (Research, Backtests, Deploy), and these actions:

- Open agent
- New strategy (goes to the library's create controls)
- Place an order
- Full-page chat

It only navigates or opens the panel. It never runs a mutation. It uses dialog semantics: focus
stays inside, Escape closes, focus returns to the trigger, and arrow keys move a combobox/listbox.
The global handler catches only the ⌘K / Ctrl+K chord. On macOS it ignores Ctrl+K inside text
fields, where that key deletes to the end of the line. Live search over strategies and deployments
is deferred.

**Design tokens and theme.** `web/src/app.css` defines CSS custom properties on `:root` (dark
default) and `:root[data-theme='light']`. They cover surfaces (`--bg`, `--rail`, `--surface`,
`--surface-2`, `--hover`, lines), text (`--text`, `--muted`, `--faint`), brand (`--accent*`), live
(`--live*`), status (`--pos`, `--neg`, `--danger-*`, `--warn*`, `--info*`, `--code`), overlays,
type, radius, and spacing. Component styles use tokens only. Canvas charts read tokens at runtime
and re-apply them when the theme changes. An inline script in `app.html` sets `data-theme` before
first paint. It uses the stored choice (`thytrader.theme`, wrapped in try/catch), or else
`prefers-color-scheme`. Text tokens meet WCAG AA on every surface in both themes. Geist and Geist
Mono ship as pinned `@fontsource-variable` packages, not a runtime CDN, so the app stays
local-first. Tabular numerals are on by default.

## Consequences

- Operators see four places to go. Rarely used pages stay one click away in System or ⌘K.
- The agent is reachable from every page and knows which page the operator is on. Its gates match
  the CLI.
- A light theme works everywhere. New UI must use tokens, not raw hex.
- ADR 0053's credential decisions (write-only Coinbase secrets, HTTP/CLI) are unchanged. Only its
  first-class top-level nav requirement for Research, Deploy, and Journals, and Chat as a nav page,
  are superseded.
- Page content is otherwise unchanged. Pages use the compact `PageHead` header. The strategy
  workspace and portfolio redesign are later slices.

## Alternatives considered

- **Keep grouped top-bar links:** rejected. They scale badly and hide the strategy lifecycle.
- **Drop `/chat`:** rejected. Deep links, skills, and tests reference it. It stays as the
  full-page view.
- **A CSS framework or component library for theming:** rejected. The app stays
  dependency-light, with plain CSS custom properties and Svelte 5.
- **Google Fonts CDN:** rejected. A local-first app must not need the network to render.
