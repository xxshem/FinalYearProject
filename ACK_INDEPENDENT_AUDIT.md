# ACK Stabilization Independent Technical Audit

## A. Overall assessment

**Overall: REQUIRES ADDITIONAL SOFTWARE CORRECTIONS.**

The one-payload SQLite reconciliation path is substantially safer than the
baseline: it validates before writing, applies table changes through one
connection/transaction, rolls back failures, and sends a positive ACK only
after `reconcile_blob()` returns from commit. The Python suite passes when
rerun, the SX127x airtime calculation matches the configured profile, and the
CMS2 serial path is explicitly configurable.

The implementation is not yet a release candidate because:

1. CMS1 firmware uses ad-hoc substring parsing while Python uses the standard
   JSON parser. Their handling of duplicate/ambiguous fields can diverge, so a
   response accepted by firmware can be interpreted as a positive ACK by
   Python when the firmware-side first `ok` value was negative. The ACK has no
   authentication.
2. The sender rejects a delta larger than 4 MiB without batching or advancing
   its watermark. It will repeat the same failed query every 30 seconds. A
   full 4 MiB transfer would take about 95 hours at the current per-frame
   schedule, before any retries.
3. Each large CH2 frame blocks CMS1's only radio for about 7.9 seconds, and
   the final frame can keep it off CH1 for another 15 seconds while waiting
   for an ACK. That service impact and the 15-second processing budget have
   not been measured on the target hardware.

No physical LoRa communication, production database synchronization, CMS2
recovery, or target systemd behavior was tested. Do not deploy to production.

## B. Git baseline verification

### Verified refs and state

- Current branch: `ack-stabilization-20261107`.
- Original `ack` and current `HEAD`: `6d10a1d08d6ff0daf25f8e9d2ddf68754e34a034`.
- Original `cms1`: `b12238a5cea277b9cdd1fdcbc26bbf11c4ed1105`.
- Merge-base of current `HEAD` and `ack`: the `ack` commit above.
- There is **no implementation commit**. The implementation is an unstaged
  working-tree diff on a branch whose `HEAD` still equals `ack`.
- The `cms1` branch ref remains at its original commit; no branch switch,
  commit, merge, or push occurred during this audit. The shared working tree
  contains modifications to files that also exist on `cms1`; this is not a
  claim that the working tree is identical to that branch.
- The rerun test command did not alter tracked or untracked project files.

### Complete pre-audit working-tree file list

Tracked modifications (14):

1. [`cms1-deploy-kit/README-CMS1.md`](cms1-deploy-kit/README-CMS1.md)
2. [`cms1-deploy-kit/install-cms2-client.sh`](cms1-deploy-kit/install-cms2-client.sh)
3. [`cms1-deploy-kit/systemd/vvs-sync-client@.service`](cms1-deploy-kit/systemd/vvs-sync-client@.service)
4. [`cms1-deploy-kit/vvs/lora/lora_sync_client.py`](cms1-deploy-kit/vvs/lora/lora_sync_client.py)
5. [`cms1-deploy-kit/vvs/lora/lora_sync_server.py`](cms1-deploy-kit/vvs/lora/lora_sync_server.py)
6. [`cms1-deploy-kit/vvs/scripts/offline_queue.py`](cms1-deploy-kit/vvs/scripts/offline_queue.py)
7. [`cms1-deploy-kit/vvs/scripts/reconciliation.py`](cms1-deploy-kit/vvs/scripts/reconciliation.py)
8. [`cms1-deploy-kit/vvs/scripts/test_gate_listener_transport.py`](cms1-deploy-kit/vvs/scripts/test_gate_listener_transport.py)
9. [`cms1_gateway/README.md`](cms1_gateway/README.md)
10. [`cms1_gateway/arduino/cms1_gateway/cms1_gateway.ino`](cms1_gateway/arduino/cms1_gateway/cms1_gateway.ino)
11. [`cms1_gateway/src/main.cpp`](cms1_gateway/src/main.cpp)
12. [`cms2_gateway/README.md`](cms2_gateway/README.md)
13. [`cms2_gateway/arduino/cms2_gateway/cms2_gateway.ino`](cms2_gateway/arduino/cms2_gateway/cms2_gateway.ino)
14. [`cms2_gateway/src/main.cpp`](cms2_gateway/src/main.cpp)

Untracked before this audit (4):

- [`ACK_IMPLEMENTATION_REPORT.md`](ACK_IMPLEMENTATION_REPORT.md)
- [`ACK_PROTOCOL_DOCUMENTATION.md`](ACK_PROTOCOL_DOCUMENTATION.md)
- [`ACK_TEST_REPORT.md`](ACK_TEST_REPORT.md)
- [`cms1-deploy-kit/.gitattributes`](cms1-deploy-kit/.gitattributes)

The tracked diff against `ack` is 806 insertions and 159 deletions across
those 14 files. The four untracked files are not included in `git diff ack`;
the reports enumerate the implementation files, including `.gitattributes`.
No other changed source file was found. The two Arduino copies match their
corresponding `src/main.cpp` files.
This audit adds only [`ACK_INDEPENDENT_AUDIT.md`](ACK_INDEPENDENT_AUDIT.md);
no source, firmware, database, or deployment file was modified during the
audit.

## C. Database transaction audit

### PASS — Incoming multi-table reconciliation is atomic

[`_validate_blob`](cms1-deploy-kit/vvs/scripts/reconciliation.py#L53) rejects
unknown tables, non-list rows, unknown/empty row columns, missing primary
keys/timestamps, invalid timestamps, and duplicate primary keys before any
payload writes. It checks the recipient's table columns with `PRAGMA`.

[`reconcile_blob`](cms1-deploy-kit/vvs/scripts/reconciliation.py#L161) opens
one connection, validates, executes `BEGIN IMMEDIATE`, then calls the
connection-taking `_lww_upsert` for each included table. In this path no
per-table helper commits. A write/commit exception reaches rollback and is
re-raised. Dry-run rolls the transaction back. SQLite `CHECK`, `UNIQUE`, and
the schema's relationship-integrity triggers remain active.

The CMS2 receiver calls `reconcile_blob()` in
[`SyncReceiver.handle`](cms1-deploy-kit/vvs/lora/lora_sync_client.py#L70).
That method returns only after commit/close; the serial ACK is written later
in `_process_serial_line`. Any exception escaping reconciliation becomes a
negative ACK. The existing failure test forces a later table's integrity
failure and verifies earlier writes were rolled back.

The one-table public helper
[`lww_upsert`](cms1-deploy-kit/vvs/scripts/reconciliation.py#L139) does open
and commit its own transaction. It is not called by `reconcile_blob`; direct
callers therefore get a per-table transaction, not whole-payload atomicity.
No current caller of this helper was found outside this module.

### WARNING — Clone-alert callbacks are best-effort, post-commit effects

`_lww_upsert` catches and logs exceptions from its `on_insert` callback, and
`reconcile_blob` catches and logs exceptions from the external
`alert_callback` after commit. These callbacks currently collect/emit clone
alerts, not table writes, so their failure does **not** make the table
reconciliation incomplete. It can, however, commit the row while omitting an
external alert side effect. If alert delivery is required for correctness,
it needs its own durable outbox/retry mechanism; it should not be represented
as transaction failure after the database has committed.

### WARNING — Timestamp ties can prevent a newer value from winning

`_lww_upsert` updates a row only when `incoming_ts > local_ts`
([`reconciliation.py`](cms1-deploy-kit/vvs/scripts/reconciliation.py#L103)).
The `updated_at` triggers in
[`init_db.py`](cms1-deploy-kit/vvs/scripts/init_db.py#L52) use SQLite
`CURRENT_TIMESTAMP`, which has one-second resolution. Two source changes to
one key in the same second can have equal timestamps; if the older value has
already reached CMS2, the newer equal-timestamp value is kept out indefinitely
by subsequent equal-timestamp replays. The current model needs a higher
precision or monotonic revision/tie-breaker, and a test that syncs two
different values with equal timestamps.

### Callback and constraints conclusion

No swallowed callback exception can skip an incoming table write: the internal
callback only appends clone-alert metadata and the write itself is executed
before the callback. Current schema triggers protect the vehicle/gate
references used by verification and entry/exit rows even though this
connection does not explicitly enable SQLite `PRAGMA foreign_keys`. The
receiver's installer invokes `init_db.py`, which installs the timestamp and
integrity triggers.

## D. Offline queue audit

### PASS — Option A is consistent with the implemented payload contract

The only production queue insertion is
[`gate_listener.py`](cms1-deploy-kit/vvs/lora/gate_listener.py#L132), after
the verification database transaction has committed. The source tables
`verification_logs`, `entry_exit`, and related metrics are what the publisher
serializes; `offline_queue` is not in the sender's `TABLES` and CMS2 does not
consume the queue. The changed
[`enqueue`](cms1-deploy-kit/vvs/scripts/offline_queue.py#L10) marks new rows
`done` locally and no longer implies remote receipt. Removing the old
post-ACK `mark_sent` behavior is correct for this Option A contract.

There is no production caller of `pending()` or `mark_failed()` in the current
tree. The dashboard lists `offline_queue` as a generic table; no queue worker
or production cleanup path was found. Test scripts delete their own test
records.

### WARNING — Queue status, metrics, and retention need explicit ownership

- Existing `pending`/`sent` rows are not migrated. Since the remote
  `mark_sent` caller was removed and no queue consumer remains, old pending
  rows can remain pending indefinitely.
- `gate_listener.py` records `queue_size` by counting pending rows before
  enqueue. New rows are `done`, so this metric no longer measures pending
  remote delivery; historical pending rows can still make it nonzero.
- The queue remains visible in the dashboard and `stats()` still reports
  pending/sent/failed/done, even though no production processor changes those
  statuses. It has no production retention/cleanup policy.
- Queue insertion is in a separate transaction after the canonical
  verification rows commit. An enqueue error is logged and the verification
  response can still succeed, so the queue row itself is not guaranteed. This
  does not lose the canonical verification records that CMS2 mirrors, but it
  means the queue cannot be treated as an authoritative audit log.
- `INSERT OR IGNORE` plus the schema uniqueness key can suppress repeated
  same-token operations in one timestamp second.

Before release, label this as a local bookkeeping view and define legacy
status/retention behavior, or restore a deliberately designed queue consumer.
Do not present `done` as proof of CMS2 synchronization.

## E. ACK/NACK protocol audit

### PASS — Normal well-formed transaction flow

1. The publisher creates one UUID transfer ID in
   [`_build_transfer`](cms1-deploy-kit/vvs/lora/lora_sync_server.py#L95).
   That ID is included in BEGIN, each CHUNK, and END. Timeout retries reuse
   the same frozen frame list/ID; a newly built transfer gets a new ID.
2. CMS1 firmware sends ordinary frames on CH2 and uses `C2W:` for END. It
   synchronously transmits END, waits for a response, and returns to CH1 on
   ACK or timeout.
3. CMS2 gateway forwards CH2 frames to the serial receiver. The Python
   receiver checks declared bounds, Base64 chunks/indexes, assembled length,
   SHA-256, UTF-8/JSON, then runs the full reconciliation transaction.
4. On commit it builds `SYNC_ACK {id, ok:true}`. On any failure it builds
   `SYNC_ACK {id, ok:false, error:...}`. It sends this on serial; CMS2
   firmware transmits it on CH2.
5. CMS1 firmware checks response type, transfer ID, and Boolean `ok`, then
   forwards the response to Python. Unrelated ordinary packets are consumed
   and ignored while the receive loop continues.
6. Python `handle_ack` advances the in-memory watermark only for a matching
   response with `ok is True`. A matching well-formed NACK is treated as
   failure, leaves the watermark unchanged, and clears the transfer for a
   later rescan. Wrong IDs and ACKs received after transfer state has cleared
   are ignored.

### FAIL — Firmware and Python can interpret an ambiguous ACK differently

In CMS1 firmware,
[`jsonStringField`](cms1_gateway/src/main.cpp#L29) and
[`jsonBoolField`](cms1_gateway/src/main.cpp#L55) search raw text for the first
substring matching a field name; they do not parse a top-level JSON object or
reject duplicate keys. `isSyncAckFor` only verifies that the first `ok`
occurrence is Boolean; it does not compare the parsed Boolean value.

Python subsequently parses the forwarded text with `json.loads`, whose
duplicate-key behavior can select a different occurrence. A malformed or
ambiguous response can therefore pass the firmware's ID/type/bool gate while
Python interprets its `ok` as true and advances the watermark. This violates
the requirement that a NACK never become success. The radio response is also
unauthenticated; the transfer ID is transmitted over the air and is not a
secret.

**Correction:** use a strict JSON parser in firmware (or a bounded parser that
validates the entire top-level object and rejects duplicate/unknown critical
fields), emit a canonical validated ACK, and require Python to accept ACKs
only in the post-END awaiting-ACK state. If the RF environment is not trusted,
authenticate ACKs and transfer frames with a keyed MAC and replay protection.
Add cross-language tests for duplicate keys, nested keys, malformed objects,
and false ACKs. Do not use a raw substring parser for an authoritative commit
signal.

### WARNING — ACK acceptance is not phase-gated in Python

[`SyncPublisher.handle_ack`](cms1-deploy-kit/vvs/lora/lora_sync_server.py#L169)
matches type/ID/value but does not require `awaiting_ack` or that END was
sent. Under the current firmware normal C2 traffic is only delivered to the
publisher during END's receive wait, but this is an implicit transport
assumption and is not encoded in the publisher state machine. An unexpected
matching positive ACK while the transfer is active can advance the watermark.
Require END-sent/awaiting-ACK state and test ACKs received before BEGIN,
between chunks, after END, after timeout, and after clear.

### Tested logical outcomes vs. not tested

- Matching positive ACK: Python path advances the watermark.
- Matching well-formed NACK: Python path does not advance it.
- Wrong ID: ignored by firmware and Python.
- Old/duplicate ACK after clear: ignored because the active ID is cleared.
- Unrelated valid packet: firmware continues waiting.
- Malformed packet: Python serial JSON parser rejects invalid JSON, but the
  firmware's raw parser can prematurely end the wait for ambiguous text.
- ACK immediately before/after timeout: inferred from firmware control flow;
  no unit test drives the C++ state machine at either boundary.

No native test exercises `isSyncAckFor` or the complete firmware ACK loop.

## F. Timeout and retry analysis

### PASS — Timer ownership and ordinary retry path

- CMS1 firmware's `SYNC_ACK_TIMEOUT_MS` is 15,000 ms
  ([`main.cpp`](cms1_gateway/src/main.cpp#L25)). It starts after blocking
  `LoRa.endPacket()` for END and measures arrival of a matching ACK/NACK.
- The publisher computes its deadline from the time before the END serial
  write, adding the full command UART time, estimated END RF airtime, the
  firmware's 15 seconds, and a one-second margin
  ([`lora_sync_server.py`](cms1-deploy-kit/vvs/lora/lora_sync_server.py#L165)).
  This aligns the host deadline after the firmware window rather than
  starting both timers at the same event.
- CMS1 serial reads use a 0.2-second timeout in `gate_listener.py`; CMS2
  serial reads use 1 second. These are polling/read bounds, not transaction
  deadlines.
- On ACK timeout the publisher retries the full transfer up to three times
  with the same transfer ID and does not advance the watermark. A positive
  ACK advances it; a NACK does not. Replaying a committed transfer is
  idempotent for rows whose PK/timestamp values are unchanged.
- CMS2 firmware's 2-second `REPLY_TIMEOUT_MS` applies only to its own
  `C2W:` path; this receiver sends an ordinary `C2:` ACK command, so that
  timer does not gate the sync response.

### FAIL — Over-limit deltas permanently block watermark progress

The sender serializes the entire delta before chunking. If it exceeds
`MAX_TRANSFER_BYTES` it logs and schedules another poll, without splitting
the payload or advancing the watermark
([`_build_transfer`](cms1-deploy-kit/vvs/lora/lora_sync_server.py#L103)).
Because the watermark remains unchanged, the same oversized delta is selected
again. The receiver limit matches the sender, but matching limits do not
provide a path to synchronize a database delta above 4 MiB.

**Operational consequence:** after a sufficiently long outage, or when the
initial delta after process start exceeds 4 MiB, all later rows remain
unsynchronized until data is manually pruned or software changes. The
publisher watermark is also in memory and restarts at 1970 on each
`SyncPublisher` construction, so every listener restart reselects the full
history; it is not a durable checkpoint.

**Correction:** transfer bounded batches with per-batch acknowledged
checkpoints and a durable watermark. Ensure a single bad/large row is
reported as a persistent operator-visible fault rather than silently
retrying forever. Test overflow, restart, batch boundaries, and recovery.

### WARNING — The 15-second ACK window is unproven for receiver work

The 15-second window starts after CMS1's END RF transmission completes. The
receiver must then finish SQLite validation/write/commit, forward the ACK
over USB, and transmit its RF packet before the window expires. A representative
69-byte ACK is about 2.96 seconds of airtime at this profile, leaving roughly
12 seconds for CMS2 commit, serial handling, and scheduling. There is no
measurement showing that the largest expected database reconciliation fits.
If it does not, CMS1 returns to CH1 before the ACK arrives; retries then repeat
the full transfer.

**Correction:** benchmark commit and ACK-return latency on target-class CMS2
hardware over representative row counts. Use bounded batches or an explicit
progress/lease protocol if the measured worst case cannot fit; do not simply
increase the timeout. Test timeout-boundary behavior and ensure a late ACK
cannot satisfy a later transfer.

### WARNING — No overall transfer/assembly deadline or resume point

The ACK deadline starts only after END. There is no overall timeout while
thousands of chunks are being sent, no per-chunk acknowledgment/resume point,
and no expiry for a partial `SyncReceiver.transfer` if END never arrives.
The receiver retains partial state until another BEGIN or process restart.
Retries after a completed attempt resend every frame.

## G. LoRa airtime and transmission analysis

### PASS — Profile and formula

Both gateway builds set CH2 to 433.5 MHz, SF12, BW125 kHz, coding rate 4/5,
preamble 8, sync word `0x12`, CRC disabled, and normal IQ. They use the same
`sandeepmistry/LoRa@^0.8.0` dependency. The installed library's
`endPacket(bool async=false)` waits for TX-done by default; both firmware
transmitters call it without `async=true`.

The Python formula uses explicit header mode, CRC off, coding-rate index 1,
8 preamble symbols, and LDRO on because the SF12/BW125 symbol duration is
32.768 ms. Independent calculation gives:

- 217 bytes: 7.708672 s.
- 218 bytes: 7.872512 s.
- A full 100-byte raw chunk becomes a 221-byte JSON/Base64 RF frame with the
  current 32-character ID and five-digit chunk index: 7.872512 s.

The sender calculates airtime from each serialized RF JSON frame, not from
the pre-Base64 chunk. It separately accounts for the complete `C2:`/`C2W:`
serial command and newline at 115200 baud, then adds a 0.25-second guard.
The largest typical frame fits the SX127x 255-byte limit. The current airtime
unit test checks 217/218 bytes and a pacing test checks that the next command
isn't written early.

### FAIL — Maximum declared transfer is operationally impractical; larger deltas cannot progress

At 4 MiB, 100-byte chunks require 41,944 chunk frames. The 221-byte full-chunk
frames alone occupy about 91.7 hours of radio airtime. Including the
publisher's 0.25-second guard and about 20 ms serial time per frame yields
approximately 94.9 hours, plus BEGIN/END and any retries. This is a calculation
from the implemented profile, not a physical measurement.

The entire delta is held and chunked in memory; no batching or resumable
checkpoint exists. Any delta above 4 MiB is rejected as described in section
F. A lost/corrupt chunk is detected only at END, after the remaining frames
have been sent, and retry starts from BEGIN.

**Correction:** set an operational batch size based on measured airtime and
gate availability, checkpoint each committed batch, and support continuation
instead of advertising 4 MiB as a practical single-transfer capacity. Test
maximum configured batches and packet-loss recovery.

### WARNING — Long off-channel periods can interrupt gate service

CMS1 switches to CH2 for each command and only returns to CH1 after the
blocking RF transmit. A full-size chunk therefore leaves its single radio
unable to receive CH1 traffic for about 7.9 seconds. After END, it can remain
on CH2 for the full 15-second ACK wait. On timeout, this repeats for retries.
The 0.25-second inter-frame guard is a CH1 opportunity, not continuous
availability. The changed pacing prevents serial command buildup; it does not
make these radio outages shorter.

No gate-request retry/timeout behavior or acceptable service interruption was
measured. Schedule synchronization in an approved low-traffic window or
otherwise define/test a bounded service-impact policy before deployment.

### WARNING — Frame processing margin is not hardware-verified

The guard follows the calculated radio and UART durations, but there is no
TX_DONE host notification. The 0.25-second margin must also cover firmware
dispatch, USB-UART latency, CMS2 serial forwarding, Python scheduling, and
Base64 processing. Those components are likely small for these frames but
have not been measured on the actual gateways/hosts.

**Correction:** measure full serial-to-radio turnaround on both boards under
load; set pacing from the observed worst-case completion or add an explicit
TX-complete signal rather than relying solely on the estimated airtime.

## H. CMS2 serial configuration audit

### PASS — CMS2 no longer defaults to `/dev/ttyUSB0`

`lora_sync_client.py` reads `VVS_SERIAL_PORT` and exits clearly if it is
missing. The installer requires a nonempty `/dev/...` assignment before
installing the service. The systemd template loads the per-user environment
file and grants `dialout`; the receiver logs open/read failures and reconnects
with exponential backoff capped at 30 seconds. Documentation instructs the
operator to use the actual `/dev/serial/by-id/` path and explicitly says not
to copy the placeholder adapter name.

### WARNING — Target device and systemd recovery are not verified

No CMS2 USB adapter was inspected, so no by-id identifier or udev mapping is
verified. The optional `EnvironmentFile=-...` means a missing file lets the
process start without a port; it exits, and systemd retries until the
configured start limit is hit. Adding the file later may require an explicit
service restart/reset. The installer prevents this in the normal install
path, but target systemd parsing, permissions, and reconnect behavior remain
unverified.

CMS1 is separate: its `gate_listener.py` still defaults to `/dev/ttyUSB0`
when `VVS_SERIAL_PORT` is absent, and the CMS1 README retains that device-name
example. The CMS2 fix does not make the CMS1 serial path persistent.

## I. Automated test coverage assessment

### Independent rerun

Ran:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest -v cms1-deploy-kit.vvs.scripts.test_gate_listener_transport
```

Result: **28 tests passed**. The suite includes gate authorization/transport
regressions; schema and timestamp migration checks; actual SQLite rollback on
a later-table error, replay/idempotence, prevalidation, dry-run, local queue
status; Python receiver chunk assembly/checksum/incomplete-transfer/limit
checks; Python ACK ID matching and retry; frame-size/airtime/pacing; and
mocked serial reconnect/configuration.

### WARNING — Coverage does not establish protocol/hardware correctness

- ACK/NACK, malformed, wrong-ID, stale, duplicate, and timeout-boundary
  behavior is not tested against the C++ parser/state machine.
- No test asserts that matching NACK leaves the watermark unchanged; the
  behavior follows current Python code but deserves a direct regression test.
- No test covers duplicate JSON keys/field ambiguity, phase gating, or ACK
  authentication.
- No test covers whole-transfer cap overflow, batch progress, persistence of
  watermark across restart, or a maximum-size transfer.
- Reconciliation tests cover rollback and replay, but not all constraints,
  equal-timestamp conflicts, callback-failure semantics, or a commit failure.
- The receiver success integration test patches `reconcile_blob`; only the
  rollback test exercises the real receiver/reconciliation path together.
- Serial tests use mocks. No physical RF link, gateway serial forwarding,
  systemd unit parsing, USB by-id path, or real database performance was
  tested.
- The airtime test checks formula constants but cannot verify antenna,
  frequency accuracy, oscillator tolerance, RF losses, or airtime on hardware.

**Recommended tests before any hardware deployment:** pure/native tests for
firmware ACK parsing and state transitions; Python matching-NACK and
pre-/post-END state tests; cross-parser ambiguity rejection; same-second LWW
conflict; large-delta batching/restart; and a controlled paired-radio test
measuring end-to-end commit/ACK latency and CH1 service interruption.

## J. Backward compatibility assessment

### PASS — Database schema and wire frame names

There is no diff to `database/schema.sql` against `ack` or `cms1`, and no
database migration is required by these changes. The existing frame names
`SYNC_BEGIN`, `SYNC_CHUNK`, `SYNC_END`, and `SYNC_ACK`, transfer-ID field,
CH1/CH2 frequencies, and serial prefixes remain. CMS2 firmware changes only
make radio profile settings explicit; its serial framing is unchanged.

### FAIL — Matched release components are required

- `cms1` branch firmware does not implement the new matching-ID ACK behavior
  and 15-second receive contract. The `ack` baseline firmware used a
  10-second ACK window; the new publisher computes against 15 seconds plus
  END airtime/UART. Mixing either old CMS1 firmware with the new publisher,
  or the new publisher with the old receiver contract, is not supported.
- The current CMS2 Python receiver fails when `VVS_SERIAL_PORT` is unset.
  The older CMS2 template supplied `/dev/ttyUSB0`; the new receiver must ship
  with the new service/environment file and a configured real path.
- The transfer wire schema is unchanged, so old/new CMS2 firmware may be
  wire-compatible if its actual radio defaults match. That does not make a
  mixed release certified; no deployed firmware or host version was verified.

Deploy CMS1 firmware plus its Python publisher/listener as a matched set.
Deploy CMS2 Python receiver, its service/environment configuration, and the
chosen firmware together for controlled testing. CMS1 firmware and Python
must be updated together. CMS2 Python/service must be updated together.

## K. Remaining risks

| Classification | File/section | Technical reason and consequence | Recommended correction |
|---|---|---|---|
| **FAIL** | [`cms1_gateway/src/main.cpp`](cms1_gateway/src/main.cpp#L29), `jsonStringField`, `jsonBoolField`, `isSyncAckFor` | Raw substring parsing does not enforce one valid top-level JSON value or unique keys; firmware and Python can select different field values. An ambiguous negative response can be forwarded and interpreted as success, advancing the watermark. | Strict whole-object parsing, reject duplicate critical fields, canonical ACK forwarding, phase-gate ACK in Python, and authenticate RF ACKs if the channel is not trusted. |
| **FAIL** | [`lora_sync_server.py`](cms1-deploy-kit/vvs/lora/lora_sync_server.py#L103), `_build_transfer` | Deltas over 4 MiB are not split, and their watermark never advances. The same oversize delta is retried indefinitely every 30 seconds. Watermark also resets to epoch on process restart. | Durable acknowledged watermark and bounded resumable batches; surface permanent oversize failures. |
| **WARNING** | [`reconciliation.py`](cms1-deploy-kit/vvs/scripts/reconciliation.py#L103), `_lww_upsert`; [`init_db.py`](cms1-deploy-kit/vvs/scripts/init_db.py#L52) | Strict timestamp comparison plus one-second `CURRENT_TIMESTAMP` can suppress different same-second values. | Use higher precision or a monotonic revision/tie-breaker; add conflict regression tests. |
| **WARNING** | [`lora_sync_server.py`](cms1-deploy-kit/vvs/lora/lora_sync_server.py#L169), `handle_ack` | ACK acceptance is not limited to after END/while awaiting ACK; current behavior relies on firmware routing. | Enforce transfer state and test out-of-phase responses. |
| **WARNING** | [`lora_sync_client.py`](cms1-deploy-kit/vvs/lora/lora_sync_client.py#L27), `SyncReceiver` | Partial transfers have no expiration; a missing END can retain receiver memory until another BEGIN/restart. | Add a bounded inactivity/transfer lifetime and verify clean reset/retry. |
| **WARNING** | [`offline_queue.py`](cms1-deploy-kit/vvs/scripts/offline_queue.py#L10), [`gate_listener.py`](cms1-deploy-kit/vvs/lora/gate_listener.py#L120) | New entries are done, old pending entries are not migrated, pending queue-size metrics can be stale, queue insertion is post-commit, and production cleanup is absent. | Define queue as local-only, correct dashboard/metric semantics, and define migration/retention policy. |
| **WARNING** | [`cms1_gateway/src/main.cpp`](cms1_gateway/src/main.cpp#L192), ACK wait | A single radio is unavailable on CH1 during each blocking frame and the post-END wait; verification traffic may be missed. | Measure the service impact and schedule sync/define a tested bounded-impact policy. |
| **WARNING** | [`lora_sync_client.py`](cms1-deploy-kit/vvs/lora/lora_sync_client.py#L16), systemd unit and installer | Config is verified syntactically but not against a real CMS2 by-id node; optional EnvironmentFile can produce restart rate limiting if missing. | Verify actual path, permissions, systemd behavior, and recovery on CMS2. |
| **NOT TESTED** | Both gateway firmware and CMS2 host | No physical RF, actual ACK latency, serial reconnect, max-sized DB, or target-system deployment was exercised. | Complete paired-device hardware validation and retain serial/timing evidence before deployment. |

### Final verdict

This verdict is about acceptance of the current implementation, not evidence
that physical hardware has failed. Do not proceed to release-candidate
hardware testing with the current ambiguous ACK parser and non-progressing
over-limit behavior. After those corrections and the identified regression
tests, conduct controlled, non-production hardware validation. Production
deployment remains unapproved until that validation demonstrates reliable
reconciliation, measured ACK timing, serial recovery, and acceptable CH1
service impact.

REQUIRES ADDITIONAL SOFTWARE CORRECTIONS.
