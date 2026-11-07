# CMS1–CMS2 Synchronization Protocol

## Scope and wire profile

CMS1 publishes a point-in-time database mirror to CMS2 over the CH2 LoRa link.
CMS1's Python publisher is called by `gate_listener.py`, which owns the CMS1
gateway serial port. CMS2's `lora_sync_client.py` owns the CMS2 gateway serial
port and reconciles the received tables into its local SQLite database.

Both gateway firmware images explicitly configure 433.5 MHz on CH2, SF12,
125 kHz bandwidth, coding rate 4/5, preamble length 8, sync word `0x12`,
explicit headers, CRC disabled, and normal IQ. CMS1 builds a bounded JSON
batch, slices it into 100-byte chunks, Base64-encodes each slice, and includes
the transfer ID on every frame. `SYNC_BEGIN` carries the byte size, chunk
count, and SHA-256 of that batch.

Each serialized batch is capped at 4 KiB and at 41 chunks. CMS2 checks the
declared bounds, chunk indexes and lengths, Base64 encoding, assembled byte
count, digest, and JSON before reconciling. A batch includes `_sync` metadata
(`schema`, `after_seq`, and `through_seq`) and may include `_deletes`; all
upserts and deletions are applied in one CMS2 SQLite transaction. The eight
business tables in `scripts/reconciliation.py` are mirrored. The
`offline_queue` and CMS1 synchronization journal/checkpoint tables are not
part of the transfer.

CMS1's local journal uses monotonically increasing sequence IDs, not
timestamps or primary keys as a cursor. Initialization creates additive
`sync_change_log`, `sync_state`, and `sync_errors` tables and change triggers,
then seeds a one-time journal of existing rows. Each acknowledged batch
atomically persists its `through_seq`, prunes journal entries through that
sequence, and resolves any size diagnostics it delivered. CMS2's
`--mirror-only` database initialization deliberately does not install these
CMS1-only triggers.

For journaled batches, CMS2 accepts the incoming CMS1 row when its timestamp
equals the local timestamp, because the journal sequence establishes that the
source mutation is new. Payloads without `_sync` retain the existing
strict-newer timestamp merge behavior.

## Successful transfer sequence

```mermaid
sequenceDiagram
    participant P as CMS1 Python publisher
    participant G1 as CMS1 gateway firmware
    participant G2 as CMS2 gateway firmware
    participant R as CMS2 Python receiver
    participant DB as CMS2 SQLite database

    P->>G1: C2:SYNC_BEGIN (serial)
    G1->>G2: SYNC_BEGIN (CH2 LoRa)
    P->>G1: C2:SYNC_CHUNK (serial, repeated)
    G1->>G2: SYNC_CHUNK (CH2 LoRa, repeated)
    P->>G1: C2W:SYNC_END (serial)
    G1->>G2: SYNC_END (CH2 LoRa)
    Note over G1: Blocking TX completes; wait up to 15 s for a matching ACK
    G2->>R: C2:SYNC_BEGIN / SYNC_CHUNK / SYNC_END (serial)
    R->>R: Validate chunk count, size, digest, and JSON
    R->>DB: Reconcile all included tables in one transaction
    DB-->>R: Commit succeeds
    R->>G2: C2:SYNC_ACK {id, ok:true} (serial)
    G2->>G1: SYNC_ACK {id, ok:true} (CH2 LoRa)
    G1->>P: C2:SYNC_ACK {id, ok:true} (serial)
    P->>P: Atomically persist through_seq and prune acknowledged journal rows
```

The CMS1 firmware uses a complete JSON parser for ACK responses. It accepts
only one complete object with exactly one top-level `type`, `id`, and Boolean
`ok`; duplicate critical fields, wrong types, nested substitutes, malformed
or trailing data are rejected. A matching positive or negative ACK ends the
firmware wait; a wrong-ID, malformed, or unrelated frame does not.

For a failed database reconciliation, CMS2 rolls back the transaction and
sends a matching `SYNC_ACK` with `ok:false`. CMS1 does not advance its durable
cursor. The Python publisher accepts a positive ACK only while waiting for
the matching active transfer's END response. A NACK, malformed/duplicate
field, wrong ID, stale ACK, or ACK received before END cannot update the
checkpoint.

Timeout retries resend the active batch with its original transfer ID.
If CMS2 committed but the ACK was lost, a publisher restart reads the last
durable `acked_seq` and re-creates the next unacknowledged batch, possibly with
a new transfer ID and with newer state for changed rows. LWW upserts and
idempotent deletes make replay safe. A later source update remains journaled
after an earlier batch's cursor, so it is sent in a following batch.
`SYNC_TX_ERROR` from CMS1 firmware causes the existing immediate retry path.

## Timing and timeout ownership

| Timer | Starts and measures | Enforced by | Coordination and possible expiry |
|---|---|---|---|
| CMS1 firmware ACK window (15 s) | Starts after blocking `LoRa.endPacket()` completes for `SYNC_END`; measures arrival of a complete valid ACK with the active transfer ID and Boolean `ok` | CMS1 firmware `C2W` receive loop | CMS2 must finish serial intake, validation, reconciliation/commit, serial ACK write, and RF delivery inside the window. It can expire first if processing, RF contention/loss, or ACK airtime exceeds 15 s. |
| CMS1 Python ACK deadline | Starts from the host's monotonic timestamp before writing `C2W`; includes serial command transmission time, estimated END RF airtime, the firmware's 15 s window, and a 1 s scheduling margin | CMS1 Python publisher | The deadline is deliberately later than the firmware window so a normal firmware timeout can be observed first. Scheduling stalls longer than the margin can still make the host retry before it reads the firmware's timeout line. |
| CMS1 serial read timeout | Starts on each `readline()` and waits up to 0.2 s for a newline-terminated modem line | CMS1 `gate_listener.py` serial configuration | A timeout returns no line; it does not extend the RF window. Python polls the publisher again in the main loop. |
| CMS2 serial read timeout | Starts on each receiver `readline()` and waits up to 1 s for a newline-terminated modem line | CMS2 `pyserial` connection | An idle read returns empty bytes and is not considered a disconnect. A disconnect causes reconnection with bounded backoff. |
| CMS2 firmware reply timeout (2 s) | Used only by CMS2 firmware's `C2W:` command path | CMS2 firmware | The synchronization receiver sends an ordinary `C2:` ACK command, not `C2W:`. This timer does not gate synchronization ACK transmission. |
| Publisher poll interval (30 s) | Starts after an empty poll or completion/abandonment of a batch | CMS1 Python publisher | This schedules the next journal scan; it is not a response timeout. |

Per-frame publisher pacing uses calculated SX127x airtime for the configured
SF12/BW125/CR4/5 profile, UART 8N1 line time at 115200 baud, and a 0.25 s
guard. The airtime calculation enables low-data-rate optimization, uses an
8-symbol preamble, explicit header mode, and disabled CRC, matching the
firmware configuration. Pacing prevents Python from buffering subsequent
commands while the firmware's synchronous LoRa transmit is still active; it
does not prove radio delivery.

The 15-second receiver window is a configured bound, not a measured guarantee
for every database or host. The 4 KiB batch ceiling bounds receiver assembly
memory and retry scope; it does not make the total synchronization fast at
SF12. A 100-byte chunk takes roughly 7.9 seconds of RF airtime in the configured
profile, so a maximum-size batch can take several minutes. Controlled paired
device tests must measure CMS2 reconciliation and ACK delivery. If a valid
batch cannot be committed and acknowledged within the current window, add a
measured progress/lease protocol rather than changing timeout constants
arbitrarily.

## Compatibility and deployment constraints

- CMS1 firmware and its Python publisher/listener form a release pair: the
  firmware implements `C2W`, matching-ID ACK filtering, and TX error reporting;
  Python implements the paced sender, deadline, retry, and watermark behavior.
- CMS2 firmware retains the existing command and packet framing. Its radio
  profile is now explicit, but the CMS2 Python receiver must be updated with
  the CMS1 publisher to obtain the matching bounds, receiver validation,
  reconnect behavior, and transactional reconciliation.
- The frame sequence remains `SYNC_BEGIN`, `SYNC_CHUNK`, `SYNC_END`, and
  `SYNC_ACK`. The payload adds `_sync` and optional `_deletes`; therefore
  CMS1 publisher and CMS2 receiver Python must be upgraded together.
- CMS1 requires an additive local SQLite migration to create the journal,
  cursor, diagnostics, and triggers. CMS2 requires no new business-table
  columns; its installer invokes `init_db.py --mirror-only` to avoid creating
  unused source-journal triggers.
- Mixed-version deployment is not certified. Use matched release files on
  both hosts and both firmware images during controlled integration testing.
- CMS1 firmware and Python publisher/listener must be paired because strict
  firmware filtering and Python phase/checkpoint behavior jointly define ACK
  success. CMS2 firmware framing is unchanged; the strict parser and batching
  payload changes are on the CMS1 firmware and Python endpoint, and the CMS2
  Python receiver.
- No production or physical radio validation has been performed. Firmware
  compilation alone does not establish protocol interoperability.
