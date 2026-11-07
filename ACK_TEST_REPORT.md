# ACK Stabilization Test Report

## Scope

Validation is performed on `ack-stabilization-20261107`, based on `ack`. No
hardware was flashed, no production service/database was accessed, and no
deployment was performed. The `cms1` branch was not changed. No commit was
created.

## Automated coverage

Run the complete VVS Python test suite from the repository root:

```powershell
python -m unittest discover -s 'cms1-deploy-kit\vvs\scripts' -p 'test_*.py'
```

The full suite passed **40 tests**.

The release-blocker regression suite covers:

- Whole-payload transaction rollback after a later-table integrity failure and
  successful replay/idempotence.
- Payload prevalidation and dry-run behavior, including no clone-alert
  escalation for rolled-back dry-run data.
- Local offline-queue completion semantics independent of sync ACKs.
- Shared ACK vectors for positive/negative ACK, wrong transfer ID, duplicate
  critical keys, nested substitutes, missing fields, wrong types, malformed
  JSON, and trailing data.
- ACK phase gating before END, between chunks, after timeout, and after a
  subsequent transfer begins; matching NACK, stale/duplicate ACK, and no
  checkpoint advancement on invalid responses.
- Bounded multi-batch continuation, durable watermark recovery after restart,
  failure in a later batch, lost ACK after CMS2 commit, duplicate delivery,
  same-timestamp updates, deletions, and oversized-record diagnostics and
  recovery.
- Receiver batch bounds, expected chunk sizing, SHA-256, atomic reconciliation,
  and idempotent replay.
- Publisher frame construction/pacing and immediate retry after an explicit
  gateway TX error.
- CMS2 serial-port configuration enforcement and reconnect after open/read
  failures.
- CMS2 mirror-only initialization, which does not install source-journal
  triggers.
- Existing CMS1 serial framing and request-correlation behavior.

The suite uses temporary SQLite databases and mocked serial behavior; it does
not validate actual RF propagation, radio interoperability, or hardware timing.

The CMS2 installer passed `bash -n`. Deployment shell scripts and systemd
units use LF line endings through `cms1-deploy-kit/.gitattributes`.

## Firmware validation

The firmware build commands are:

```powershell
pio run -d cms1_gateway -e esp32dev
pio run -d cms2_gateway -e nodemcuv2
```

Both final builds succeeded:

- CMS1 `esp32dev`: success; 21,600 bytes RAM and 283,841 bytes flash.
- CMS2 `nodemcuv2`: success; 28,360 bytes RAM and 271,287 bytes flash.

The C++ ACK parser is included and compiled by the CMS1 firmware build. Shared
JSON vectors are executed against the Python parser; the current environment
has no native C++ test runner, so firmware vector execution remains a
hardware/native-test limitation. The source and Arduino copies were compared
and match:

```powershell
Compare-Object (Get-Content cms1_gateway/src/main.cpp) `
  (Get-Content cms1_gateway/arduino/cms1_gateway/cms1_gateway.ino)
Compare-Object (Get-Content cms1_gateway/src/sync_ack_parser.h) `
  (Get-Content cms1_gateway/arduino/cms1_gateway/sync_ack_parser.h)
Compare-Object (Get-Content cms2_gateway/src/main.cpp) `
  (Get-Content cms2_gateway/arduino/cms2_gateway/cms2_gateway.ino)
```

`git diff --check` passed. The current branch is
`ack-stabilization-20261107`. Build success verifies compilation only. It does
not exercise the CMS1 ACK parser at runtime, CMS2-to-CMS1 RF delivery, or
serial adapter selection. Those require controlled hardware tests.

## Remaining release-candidate checks

Before any redeployment:

1. Verify systemd unit parsing and inspect the CMS2 unit's expanded
   `EnvironmentFile`, user, group, and executable paths on the target OS.
2. Perform a controlled paired-radio transfer with the exact deployed
   firmware/Python versions. Capture CMS1 and CMS2 serial logs and verify the
   transfer ID and ACK outcome end-to-end.
3. Test disconnect/reconnect, unrelated CH2 packets, malformed/wrong-ID and
   stale ACKs, negative ACK, firmware TX failure, ACK loss after a successful
   commit, replay, and a multi-batch transfer. Confirm retries do not lose
   source rows or duplicate database records.
4. Measure CMS2 reconciliation and ACK delivery durations for realistic
   small, typical, and largest-supported payloads. Confirm the full receive,
   commit, ACK RF airtime, and scheduling delay fit inside CMS1's 15 s firmware
   wait window. The mock suite cannot establish this.
5. Confirm the persistent `/dev/serial/by-id/` device path and `dialout`
   permissions on the actual CMS2 host.
6. Verify CMS1 sequence checkpoint behavior, oversized-row diagnostics, and
   CMS2 row counts after transfer; force reconciliation failure and verify
   the whole CMS2 transaction rolls back and CMS1 retries without advancing
   its durable cursor.

## Limitations

No native unit test currently runs the C++ ACK parser/state machine. No
physical timing, RF packet loss, antenna, device-node, or target systemd
behavior was tested. The firmware/Python change should therefore be treated
as ready for controlled hardware validation, not as evidence of production
redeployment readiness.
