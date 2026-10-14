# 0139: Minimum alert severity for delivery; settings writes on a bind-mounted file

- Status: Accepted
- Date: 2026-10-14
- Relates to: [0115](0115-durable-safety-alerts-and-supervision.md),
  [0138](0138-live-alert-delivery.md), [0055](0055-yaml-settings-runtime-reloadable-yolo.md)

## Context

Enabling webhook delivery sends every open safety alert, warnings included, and a switch from
`none` re-sends every open alert previously recorded as `skipped`. On a fleet with several
paused books that is a burst of warnings to a channel a person reads, which teaches them to
ignore it. There was no way to send only urgent alerts.

Separately, `thytrader-runtime set-settings` returned HTTP 500 in the supported Compose
install: the settings writer renamed a temporary file over `thytrader.yaml`, which Compose
bind-mounts as a single file, and a rename over a mount point fails with `EBUSY`.

## Decision

1. **`notify_min_severity`** (YAML, `info` default, `warning`, `critical`; CLI
   `set-settings --notify-min-severity`) is the lowest safety-alert severity sent. The worker
   reads it at each delivery, so changes apply without a restart. Alerts below it are recorded
   as `skipped` with detail `below notify_min_severity=<value>: alert retained locally` through a
   new `skip_delivery` store operation that spends **no** retry attempt, never revokes an
   in-flight claim and never overwrites a `failed` row, so delivery-failure (and exhausted
   budget) evidence that health grades survives a later filter. If the minimum is lowered while
   a skipped alert is open, it is delivered with its full budget. The local feed, alert grading and supervision are unchanged; memory-lane
   `notify` messages are explicit sends and are not filtered.
2. **Settings I/O is locked and snapshot-based.** Every process (host or container) sees the
   same bind-mounted inode, so `flock` coordinates them. A reload reads the file once under a
   shared lock and parses and fingerprints exactly those bytes, publishing settings and
   fingerprint together. `replace()` holds an exclusive lock across reload, merge and write
   (retaking it if another writer swapped the inode meanwhile). The write is an atomic rename;
   when the rename fails with `EBUSY` or `EXDEV` (a single-file bind mount) the locked inode is
   rewritten in place and fsynced, so ThyTrader readers never see it half written. Other
   rename failures still raise. The exclusive lock uses a read-only descriptor, so an atomic
   rename never needs write access to the file itself. Where `flock` is unsupported, reads are
   unlocked and atomic renames still work, but an in-place rewrite is refused rather than done
   unlocked. Reload I/O errors keep the previous settings and report them like invalid content.
   Hand edits are not locked, so reloads stay tolerant: content
   that does not load (undecodable bytes, invalid YAML, a non-mapping or failed validation)
   keeps the last valid settings, logs once per file content, retries when the file changes
   and makes the `configuration` component `degraded` with `YAML_INVALID_PREVIOUS_IN_FORCE`
   (payload `yaml_invalid: true`). A successful write clears it. Only store construction still
   fails loudly on an invalid file.
3. `YamlSettingsWrite.notify_min_severity` is optional: omitted keeps the current value, so a
   client that predates the field (such as the browser settings form) never resets it. A write
   first reloads the file on disk, so "current" is never a stale cache of another process's
   write.

Ops contract v99 advertises `runtime_observability: alert_min_severity`; the `configuration`
report carries `notify_min_severity` and the `alert_delivery` detail names it.

## Consequences

- An operator can page on critical alerts only, with warnings still visible locally and in
  health. Choosing whether and where to deliver stays the user's decision; agents never enable
  delivery or send tests on their own initiative.
- `set-settings` works in Compose.

## Alternatives considered

- **Filter in the webhook receiver.** Rejected: every installation would reimplement it, and
  the alert row would claim a delivery that the receiver discarded.
- **Mount a settings directory instead of the file.** Rejected for now: it changes the install
  layout for every existing Compose user; the in-place fallback fixes the defect without it.
