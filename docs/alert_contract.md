# EchoSense finalized Phase 2 alert contract

Contract version: schema version `2`, backend migration `20260804_0008`.

Every record is an **unverified possible-aggression alert**. Human review is
required. The system does not determine intent, identify a speaker, establish
guilt, or retain playable audio.

## Ingestion and authentication

`POST /alerts/` accepts finalized Phase 2 events from a registered edge device.
It requires all three headers:

```text
Content-Type: application/json
X-EchoSense-Device-Id: <registered device_code>
X-EchoSense-Device-Key: <device key>
Idempotency-Key: <body event_id>
```

The device code is trimmed and looked up exactly. The key is checked against
the stored bcrypt hash. Missing, unknown, or incorrect credentials return the
same HTTP 401 detail; disabled devices return HTTP 403. The request body cannot
override the trusted device, classroom, or school assignment. A supplied
`device_identifier` must equal the authenticated device code.

The body `event_id` is a required UUID. A missing, malformed, or mismatched
`Idempotency-Key` returns HTTP 422. There is no bearer token for edge ingest;
bearer JWT authentication remains separate for frontend and admin routes.

## Required request fields

- `event_id`: UUID
- `schema_version`: integer `2`
- `trigger_type`: `KEYWORD`, `ACOUSTIC`, or `TEST`
- `severity`: `LOW`, `MEDIUM`, or `HIGH`, with compatible case-insensitive input
- `severity_reasons`: 1–50 non-empty strings, at most 200 characters each
- `review_message`: exactly `Unverified possible-aggression alert. Human review required.`
- one of `device_identifier` or non-empty `device_source`; it cannot override
  the authenticated device identity
- `event_start_timestamp`: timezone-aware timestamp
- `event_end_timestamp`: timezone-aware timestamp not earlier than the start

`test_mode` defaults to false. `TEST` and `test_mode=true` must appear together.
Test ingestion also requires `ECHOSENSE_ALLOW_TEST_ALERTS=true`; it is false by
default and on the declared Render service.

## Finalized evidence fields

The backend stores these fields without recalculating detector decisions:

- `severity_evidence`
- `monitored_terms`
- `monitored_word_detected`
- `monitored_word_occurrences`
- `acoustic_trigger_evidence`
- `detailed_acoustic_evidence`
- `tone_evidence`
- `repetition_evidence`
- `direct_address_evidence`
- `laughter_context`
- `transcript` / `transcribed_text`
- `transcription_status`
- `processing_latency`
- `dropped_data_metrics`
- `collector_statuses`
- `event_delivery_summary`
- `extension_count`, `extension_reasons`, and `maximum_duration_reached`
- `pre_trigger_seconds`, `post_trigger_seconds`, and `trigger_timestamp`
- optional untrusted `device_source` metadata

Structured evidence is bounded by depth, collection size, string size, total
characters, and finite numeric values. Monitored-word confidence and YAMNet
scores must be in the range 0–1. Unknown top-level fields are rejected.

`monitored_terms` is detector evidence and is stored as submitted.
`matched_terms` is the existing normalized relationship to the backend
dictionary. One is never silently converted into the other. When
`matched_terms` is supplied, its existing dictionary and duplicate checks
remain in force.

## Legacy compatibility mapping

The existing response fields remain available to deployed frontend clients:

| Finalized/legacy input | Existing alert field behavior |
|---|---|
| `transcript` or `transcribed_text` | Stored in `alerts.transcribed_text` |
| explicit `confidence` | Stored unchanged |
| confidence omitted | Maximum monitored-word confidence, then YAMNet score, then `0.0` compatibility fallback |
| explicit `duration` | Stored unchanged |
| duration omitted | Derived from event end minus event start |
| `location` | Accepted for compatibility but ignored; registered classroom is stored |
| `detected_words`, categories, hard/soft hits | Stored only when explicitly submitted |
| `monitored_terms` | Stored separately as finalized detector evidence |
| `matched_terms` | Stored relationally after dictionary validation |

The `0.0` confidence fallback is only a legacy display compatibility value; it
does not invent positive evidence. Consumers of schema version 2 should use
the finalized evidence fields.

## Recursive privacy policy

The entire request is inspected recursively, including dictionaries nested in
lists. The backend rejects keys representing raw/playable audio or debug speech,
including `raw_audio`, `raw_pcm`, `pcm`, `pcm_samples`, `audio_bytes`,
`audio_blob`, `audio_base64`, `wav`, `waveform_bytes`, `recorded_audio`,
`raw_vosk_text`, `vosk_partial`, `debug_transcript`, `partial_transcript`, and
`audio_debug`.

`waveform_snapshot` remains allowed as a maximum 256-point, nonnegative,
non-reconstructive UI visualization. It is not raw audio.

## Duplicate behavior and fingerprint

The database unique constraint on `alerts.event_id` remains the final race-safe
guard. The route also checks before insertion and recovers the winner after a
unique-constraint `IntegrityError`.

The SHA-256 `request_fingerprint` covers every normalized `AlertCreate` field,
including event metadata, evidence, compatibility fields, matched-term requests,
and reported device metadata. It excludes only legacy body `location`, because
that field is untrusted and replaced by the registered classroom. Dictionary
keys are sorted, UUIDs and enums are normalized, timestamps are normalized to
UTC, and list order is preserved. Database IDs, `created_at`, trusted device
assignment, delivery state, and push state are not request fields and are not
included.

- Same device + same event ID + same fingerprint: return the original row.
- Same device + same event ID + different fingerprint: HTTP 409, no mutation/push.
- Different device + same event ID: HTTP 409, no mutation/push.
- Concurrent identical inserts: one database row; the losing transaction returns
  the winner after rollback.
- A historical event with no fingerprint fails closed as a conflicting duplicate.

Only the transaction that inserts a new row schedules a notification.

## Storage and response

New and accepted duplicate responses return `delivery_status: "stored"`. This
means PostgreSQL contains the alert. It is not the edge SQLite outbox state and
does not mean Expo or a phone received anything.

Responses expose the current nullable `push_status` (`pending`, `accepted`,
`partial`, `rejected`, `failed`, or `skipped`). A null value means the push
state is unavailable. The canonical `transcript` response field mirrors the
retained `transcribed_text` compatibility field; both are nullable for
historical alerts.

`GET /alerts/` and `GET /alerts/{id}` remain JWT reviewer-only and return both
the finalized fields and existing compatibility fields. The list remains newest
first and accepts optional `event_id`, evidence filters, `skip >= 0`, and
`limit` from 1 to 200. Omitting `limit` preserves the prior unbounded response
shape for existing clients.

There are no WebSockets or server-sent events; clients poll.

## Notification payload

Normal alert pushes contain only:

```json
{
  "type": "classroom_alert",
  "alertId": 123,
  "event_id": "00000000-0000-4000-8000-000000000001",
  "severity": "high",
  "severityLevel": "HIGH",
  "trigger_type": "KEYWORD",
  "route": "/alert/123",
  "is_test": false
}
```

Transcript, terms, evidence, classroom, identity, credentials, and tokens are
excluded. LOW and MEDIUM use Android channel `echosense-phase3-alerts`; HIGH
uses `echosense-high-alerts`. Every message requests `sound: "default"`.

An ingested synthetic event uses title `EchoSense Alert — TEST` and body
`TEST possible verbal-aggression event. Human review required.` The separate
`/users/provider-test/*` endpoints remain provider-only tests, use Android
channel `echosense-alerts` with `sound: "default"`, and create no alert.

## Notification reliability

This phase retains the in-process background task. It now validates and
deduplicates Expo tokens, parses each Expo ticket, distinguishes accepted,
partial, rejected, failed, and skipped attempts, and stores the first accepted
ticket ID plus attempt count/error/submission time on the alert. There is no
durable worker, automatic retry, Expo receipt polling, phone receipt signal, or
user-open telemetry. Process termination can still lose a pending attempt.

Expo ticket acceptance means only that Expo accepted the submission. Phone
receipt, display, default-sound playback, and user interaction require separate
mobile/device verification.

## Mobile push-token registration

`POST /users/push-token` requires a valid user bearer token. Non-empty values
must be structurally valid Expo or legacy Exponent push tokens and are trimmed
before storage. An empty or whitespace-only value detaches the current token by
storing null. Tokens are not written to application logs.

The current database model stores one token per user, so registering another
physical device for the same account replaces the previous device's token.
Multi-device delivery for one user is not supported in this phase.

## Migration and rollback

Schema order is `20260730_0007` (registered devices) followed by
`20260804_0008` (finalized event contract and push-attempt state):

```bash
ALEMBIC_DATABASE_URL=postgresql://... alembic upgrade head
```

Downgrade the finalized contract while retaining alert rows:

```bash
ALEMBIC_DATABASE_URL=postgresql://... alembic downgrade 20260730_0007
```

The downgrade removes the new evidence/fingerprint/push columns, so export any
required finalized evidence first. Alembic is required; keep
`RUN_LEGACY_STARTUP_MAINTENANCE=false`.

## CORS and HTTPS

`ECHOSENSE_CORS_ORIGINS` is a comma-separated origin list. The finalized list is
`http://localhost:3000`, `http://192.168.1.92:3000`, and
`https://echosense-frontend.vercel.app`. Wildcard origins are rejected while
credentialed CORS is enabled. Deployment infrastructure must terminate HTTPS;
the application does not do so itself.
