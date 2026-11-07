# ACK Release-Blocker Corrections

## Status and scope

These corrections are on `ack-stabilization-20261107`, based on `ack`. The
`cms1` branch was not changed. No commit, push, deployment, firmware flash,
hardware access, or production database access was performed. A recoverable
pre-correction checkpoint is stored outside the repository at:

`C:\Users\User\.copilot\session-state\b34b632d-e842-40b5-afbf-9cd21a835b70\files\ack-blocker-pre-corrections`

The work addresses the strict ACK parsing and unbounded/non-persistent
synchronization blockers. It preserves the earlier atomic SQLite
reconciliation, payload digest validation, matching transfer IDs, LoRa-aware
pacing, serial reconnect behavior, explicit serial-port configuration, and
local-only offline queue semantics.

## Root causes and corrections

### 1. Firmware/Python ACK ambiguity

**Root cause:** CMS1 firmware previously located `type`, `id`, and `ok` using
substring searches. Duplicate keys, nested keys, malformed/trailing data, or
wrong field types could be interpreted differently by firmware and Python.
Python's `json.loads()` also discarded duplicate-key evidence before the ACK
handler received a dictionary.

**Correction:**

- `cms1_gateway/src/sync_ack_parser.h` implements a complete JSON grammar
  parser for the radio ACK response. It validates one complete object and
  complete input consumption, decodes escaped critical keys/strings, and
  counts only top-level `type`, `id`, and `ok`. Duplicate critical fields,
  nested substitutes, malformed JSON, incorrect types, and wrong transfer
  IDs do not satisfy the wait. It returns distinct positive, negative,
  unrelated, wrong-transfer, and malformed outcomes.
- Both CMS1 firmware source copies use that parser for received ACKs.
  Matching NACKs are forwarded to Python and terminate the firmware wait as
  failures; unrelated, malformed, and wrong-ID packets remain ignored until
  timeout.
- `gate_listener.py` passes the original CH2 JSON text into
  `SyncPublisher.handle_ack()` and strictly decodes UTF-8 rather than
  discarding invalid bytes.
- The publisher's ACK parser rejects duplicate critical keys, requires the
  active transfer ID and `awaiting_ack` END phase, requires `ok` to be a
  Boolean, and persists state only for `ok is True`. ACKs before END, between
  chunks, after timeout, from an earlier transfer, or with malformed/duplicate
  critical fields cannot advance the cursor.
- Shared vectors in `cms1_gateway/test/ack_protocol_vectors.json` cover both
  Python and firmware parser cases. Python executes the vectors; firmware is
  compile-validated. There is no host-native C++ vector runner in the current
  toolchain.

### 2. Bounded and resumable transfer

**Root cause:** The prior publisher serialized a complete delta and rejected
it above the receiver's 4 MiB limit. It used an in-memory timestamp watermark,
so process restart returned to the epoch. Timestamp-only positions are also
ambiguous where timestamps have one-second resolution or updates reuse an
existing timestamp.

**Correction:**

- CMS1 initialization adds three local tables: `sync_change_log`,
  `sync_state`, and `sync_errors`. Triggers journal inserts, updates, primary
  key changes, and deletes for the eight mirrored business tables.
- On the first migration, existing rows are journaled once for initial
  bootstrap. The durable cursor is the acknowledged journal sequence, not a
  timestamp or primary key.
- Each batch is independently serialized and limited to **4 KiB** and
  **41 chunks** of at most 100 raw bytes. The cap bounds RAM/retry scope and
  aligns with the existing LoRa frame size; it is not a throughput guarantee.
  With the configured SF12 radio profile, each full encoded chunk takes
  roughly 7.9 seconds of airtime, so a maximum-size batch can take about
  5.4 minutes of airtime before processing and ACK overhead.
- Every payload contains `_sync` metadata (`schema`, `after_seq`,
  `through_seq`), changed table rows, and optional `_deletes`. Each batch has
  its own transfer ID and SHA-256 digest and uses the existing
  `SYNC_BEGIN → SYNC_CHUNK → SYNC_END → SYNC_ACK` frame sequence.
- For journal batches, CMS2 accepts the incoming CMS1 row on equal timestamps
  (as well as newer timestamps). This is necessary because the journal
  sequence, not timestamp precision, identifies a newer source mutation.
  Legacy payloads without `_sync` retain the previous strict-newer LWW rule.
- CMS2 validates size, expected chunk count, chunk lengths, indexes, encoding,
  digest, JSON and synchronization metadata. It reconciles the complete
  batch, including deletes, in one transaction. A positive ACK is emitted
  only after commit; a failure rolls back before a negative ACK is sent.
- After a matching positive ACK, CMS1 atomically updates `acked_seq`, prunes
  journal rows through the acknowledged sequence, and resolves any delivered
  oversized-row diagnostic. Failure, NACK, timeout, malformed ACK, wrong ID,
  and out-of-phase ACK leave the persisted cursor unchanged.
- If CMS2 commits but its ACK is lost, CMS1 retains the prior sequence and
  replays the pending changes. After process restart the transfer ID may be
  new, but the batch is rebuilt from the same unacknowledged journal interval.
  LWW upserts and deletes are idempotent. Later source changes remain above
  the acknowledged cursor and are sent in a later batch.
- A row that cannot fit on its own is recorded persistently in `sync_errors`
  with table, primary key, serialized size, and reason. CMS1 logs an
  operator-visible error and does not skip it or advance past it. Correcting
  or deleting the row allows a later acknowledged batch to resolve the
  diagnostic.

CMS1's migration is additive and changes no business-table columns or
timestamp semantics. CMS2's mirror database does not need the source journal;
the CMS2 installer now invokes `init_db.py --mirror-only` so CMS2 does not
create unused change triggers or accumulate an unused journal.

## Affected files

- `cms1_gateway/src/main.cpp`
- `cms1_gateway/src/sync_ack_parser.h`
- `cms1_gateway/arduino/cms1_gateway/cms1_gateway.ino`
- `cms1_gateway/arduino/cms1_gateway/sync_ack_parser.h`
- `cms1_gateway/test/ack_protocol_vectors.json`
- `cms2_gateway/arduino/cms2_gateway/cms2_gateway.ino` (added as the matching
  Arduino source copy; no CMS2 protocol behavior changed)
- `cms1-deploy-kit/vvs/lora/gate_listener.py`
- `cms1-deploy-kit/vvs/lora/lora_sync_server.py`
- `cms1-deploy-kit/vvs/lora/lora_sync_client.py`
- `cms1-deploy-kit/vvs/scripts/init_db.py`
- `cms1-deploy-kit/vvs/scripts/reconciliation.py`
- `cms1-deploy-kit/vvs/scripts/test_gate_listener_transport.py`
- `cms1-deploy-kit/install-cms2-client.sh`
- `cms1-deploy-kit/README-CMS1.md`
- `ACK_IMPLEMENTATION_REPORT.md`
- `ACK_PROTOCOL_DOCUMENTATION.md`
- `ACK_TEST_REPORT.md`
- `ACK_RELEASE_BLOCKER_CORRECTIONS.md`

CMS2 firmware framing is unchanged; its existing source and Arduino copies
were not modified for this blocker. No database migration or timestamp
semantics change is required on CMS2.

## Compatibility, regression risks, and rollout

- CMS1's new publisher payload contains `_sync` and can contain `_deletes`;
  deploy the CMS2 Python receiver at the same time. An old receiver rejects
  the additional payload structure and does not support deletion application.
- Deploy CMS1 firmware and Python publisher/listener together. CMS1 firmware's
  strict ACK parser intentionally ignores responses that the prior substring
  checks could have accepted.
- CMS2 firmware's radio framing remains compatible, but both firmware images
  still require a paired-radio test for actual packet delivery and timing.
- The 4 KiB/41-chunk cap intentionally makes a large synchronization many
  batches. Total airtime may be long at SF12; this work bounds each unit of
  work but does not increase throughput.
- SQLite migration creates triggers and journal entries on CMS1. Confirm
  adequate disk space and back up the CMS1 database before a controlled
  rollout. The sequence journal can grow while CMS1 is offline from CMS2; it
  is pruned only through acknowledged batches.
- If an oversized row is encountered, synchronization is deliberately
  blocked at that journal position until an operator corrects or deletes the
  record. No automatic skip policy is provided.
- No mixed-version deployment is certified. No production deployment is
  approved by this report.

## Required validation and implementation order

1. Apply the additive CMS1 journal/checkpoint initialization and mirror-only
   CMS2 initialization.
2. Deploy strict parser and Python phase/ID validation as a matched CMS1
   sender release.
3. Deploy the bounded CMS2 receiver and atomic deletion/upsert reconciliation
   with the CMS1 Python sender; keep the existing firmware frame sequence.
4. Run all Python tests, both firmware builds, firmware-copy comparisons, and
   `git diff --check`.
5. Before any production redeployment, run paired CMS1/CMS2 hardware tests for
   positive/NACK responses, timeout/retry, loss of ACK after commit,
   duplicate delivery, large multi-batch synchronization, oversized-row
   recovery, serial disconnect/reconnect, and realistic ACK timing.

## Software validation status

The final software validation passed 40 Python tests, both PlatformIO builds,
installer shell syntax, source/Arduino-copy comparisons, and
`git diff --check`; details are in `ACK_TEST_REPORT.md`. Software tests and
firmware compilation do not validate RF delivery, hardware timing, or target
systemd behavior. Verdict: **READY FOR CONTROLLED HARDWARE TESTING**. This is
not approval for production redeployment.
