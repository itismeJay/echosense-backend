# EchoSense backend alert contract

Contract version: backend API 1.0 with migration `20260729_0006`.

Every record represents an **unverified possible-aggression alert**. Human
review is required. Severity prioritizes human review based on observable
transcript and acoustic evidence. It does not confirm bullying, determine
intent, identify a speaker, or establish guilt.

## Ingestion

`POST /alerts/` remains unauthenticated for compatibility with the deployed
edge. This is a known security limitation; device authentication should be
introduced through a backward-compatible credential rollout.

The request content type is JSON. Unknown fields are rejected. In particular,
`raw_audio`, `audio`, `audio_bytes`, and audio sample arrays are not contract
fields. The existing `waveform_snapshot` is retained only as a bounded,
40-point-by-convention derived visualization (maximum 256 non-negative
amplitude points); it is not an audio recording and cannot be played back.

### Exact accepted payload

| Field | Type | Required | Source / example | Privacy | Persisted | List | Detail | Compatibility |
|---|---|---:|---|---|---:|---:|---:|---|
| `event_id` | UUID or null | No | Edge outbox / UUID | Delivery metadata | Yes | Yes | Yes | Null for legacy; non-null values are unique |
| `severity` | string | Yes | Edge / `high` or `HIGH` | Detection evidence | Yes, as `HIGH` | Yes, legacy lowercase | Yes, legacy lowercase | Case-insensitive input; only LOW/MEDIUM/HIGH |
| `severity_evidence` | object or null | No | Edge severity decision | Sensitive detection evidence | Yes, JSONB | Yes | Yes | Null means unavailable; never fabricated |
| `confidence` | finite number, 0–1 | Yes | Edge / `0.91` | Detection evidence | Yes | Yes | Yes | Existing field |
| `duration` | finite seconds, 0–3600 | Yes | Edge / `1.75` | Detection evidence | Yes | Yes | Yes | Existing field |
| `location` | string or null, ≤200 | No | Configured edge location | Sensitive source metadata | Yes | Yes | Yes | Defaults to `Classroom` when omitted |
| `transcribed_text` (`transcript` alias) | exact string or null, ≤10,000 | Conditional | Finalized edge transcript | Highly sensitive | Yes | Yes | Yes | Required and nonblank when `event_id` is present; optional for legacy requests without it |
| `detected_words` | string array or null, ≤100 items/100 chars | No | Legacy edge matcher | Sensitive detection evidence | Yes | Yes | Yes | Existing JSON-text column |
| `matched_terms` | object array, ≤50 | No | Edge plus monitored dictionary | Sensitive detection evidence | Yes, relationally | Yes | Yes | Each term must resolve to the server dictionary |
| `categories` | string array or null, ≤50 | No | Edge / term categories | Sensitive detection evidence | Yes | Yes | Yes | Existing JSON-text column |
| `language` | `fil`, `ceb`, `en`, `mixed`, `unknown` | No | Edge language classifier | Detection evidence | Yes | Yes | Yes | Defaults to `unknown` |
| `language_confidence` | number 0–1 or null | No | Edge language classifier | Detection evidence | Yes | Yes | Yes | Null means unavailable |
| `yamnet_ran` | boolean or null | No | Edge acoustic classifier | Detection evidence | Yes | Yes | Yes | Null is legacy/unknown |
| `yamnet_class` | string or null, ≤200 | No | Edge / `Speech`, `NotRun` | Detection evidence | Yes | Yes | Yes | Validated with `yamnet_ran` |
| `yamnet_score` | finite number or null | No | Edge classifier | Detection evidence | Yes | Yes | Yes | Required in 0–1 range when YAMNet ran |
| `emotion` | string or null, ≤100 | No | Legacy edge tone label | Detection evidence, not intent | Yes | Yes | Yes | Existing field; must not be interpreted as intent |
| `rms` | finite number or null | No | Edge acoustic summary | Detection evidence | Yes | Yes | Yes | Existing field |
| `energy_variance` | finite number or null | No | Edge acoustic summary | Detection evidence | Yes | Yes | Yes | Existing field |
| `zero_crossing_rate` | finite number or null | No | Edge acoustic summary | Detection evidence | Yes | Yes | Yes | Existing field |
| `peak_to_average` | finite number or null | No | Edge acoustic summary | Detection evidence | Yes | Yes | Yes | Existing field |
| `waveform_snapshot` | integer array or null, ≤256 | No | Derived edge visualization | Sensitive derived metadata | Yes | Yes | Yes | Existing field; raw/audio arrays remain prohibited |
| `hard_hits` | string array or null, ≤50 | No | Legacy edge matcher | Sensitive detection evidence | Yes | Yes | Yes | Existing field |
| `soft_hits` | string array or null, ≤50 | No | Legacy edge matcher | Sensitive detection evidence | Yes | Yes | Yes | Existing field |
| `duration_gate` | string or null, ≤20 | No | Edge decision metadata | Detection evidence | Yes | Yes | Yes | Existing field |
| `required_duration` | finite seconds, 0–3600 or null | No | Edge decision metadata | Detection evidence | Yes | Yes | Yes | Existing field |

The backend does not recalculate aggression, intent, guilt, or severity. It
validates the submitted level and evidence consistency.

### Severity evidence

```json
{
  "level": "HIGH",
  "reasons": ["term_category:self_harm_directive"],
  "term_categories": {
    "self_harm_directive": ["matched phrase"]
  },
  "supporting_evidence": [
    "laughter_or_excitement_marker_present"
  ]
}
```

`level` accepts supported case variants and must equal the normalized request
severity. A mismatch is rejected with HTTP 422. `reasons` contains 1–50
non-empty strings. Category maps, phrase lists, and supporting evidence are
bounded to 50 entries/items, individual strings to 200 characters, and the
combined evidence text to 16,000 characters. Unexpected nested keys are
rejected.

An omitted `severity_evidence` is stored as SQL `NULL` and returned as JSON
`null`. This is the truthful legacy/unavailable state. The backend does not
fabricate reasons for old alerts.

### Severity migration and response compatibility

The database stores exactly `LOW`, `MEDIUM`, or `HIGH` and enforces this with
`ck_alerts_severity`. Migration `20260729_0006` first refuses unsupported
historical values, normalizes supported values to uppercase, adds the check,
then adds nullable JSONB `severity_evidence`.

For gradual client rollout:

- Request `severity` accepts lowercase, uppercase, and mixed-case supported
  forms.
- Existing response field `severity` remains `low`, `medium`, or `high` so the
  deployed web and mobile parsers remain compatible.
- Additive response field `severity_level` is canonical `LOW`, `MEDIUM`, or
  `HIGH`.
- `severity_evidence.level` is canonical uppercase.
- Missing request `severity` remains HTTP 422, matching the pre-phase
  production contract. The backend never silently defaults it to LOW.
- Old clients safely ignore `severity_level`, `severity_evidence`, and
  `review_notice`; new clients should prefer `severity_level`.

List and detail responses include:

```json
{
  "severity": "high",
  "severity_level": "HIGH",
  "severity_evidence": {
    "level": "HIGH",
    "reasons": ["term_category:self_harm_directive"],
    "term_categories": {
      "self_harm_directive": ["matched phrase"]
    },
    "supporting_evidence": []
  },
  "review_notice": "Unverified possible-aggression alert. Human review required."
}
```

All prior response fields remain present. Alert list/detail/analytics routes
remain authenticated for `admin`, `staff`, and `counselor` roles. Additional
response fields are additive.

## Storage: evidence versus delivery metadata

Detection evidence consists of the exact transcript, monitored/detected terms,
language, YAMNet result, tone/acoustic summaries, bounded derived waveform
visualization, duration, severity, and severity evidence.

Delivery metadata consists of `event_id`, database `id`, `created_at` (the
backend receive/create time), location/source metadata, and alert `status`.
There is currently no notification-attempt table, provider receipt store, or
durable backend push queue. Recipient tokens stay on user records and are not
returned by alert APIs.

`event_id` is the idempotency key. The unique database constraint and
conflict-recovery lookup ensure repeated delivery returns the existing alert.
Only the transaction that creates and commits the row schedules a push task,
so a duplicate request does not intentionally schedule another notification.
Legacy requests without `event_id` cannot be deduplicated.

## Notification behavior

| Severity | Title | Body | Expo priority |
|---|---|---|---|
| LOW | Possible classroom concern | A low-severity unverified alert requires staff review. | normal |
| MEDIUM | Possible verbal-aggression indicators | A medium-severity unverified alert requires staff review. | normal |
| HIGH | High-priority classroom alert | Strong possible-aggression indicators were detected. Prompt human review is recommended. | high |

Push payloads contain the alert ID and both compatible lowercase and canonical
uppercase severity values. They do not contain transcript, matched terms,
location, student identity, guilt, or intent claims. Expo supports `normal` and
`high` delivery priority, but priority is only a provider scheduling hint.

Recipient selection is unchanged. Normal mode targets only existing users with
push tokens. Controlled-test mode fails closed and targets only the configured
user when that exact user has a token. No new role or recipient is added.

Alert persistence commits before recipient resolution and push scheduling.
Provider exceptions are contained and logged by exception type without tokens
or payloads, so push failure does not roll back or delete the alert. Invalid or
missing tokens are omitted/fail closed by existing selection behavior.

Remote push notification still requires connectivity and successful delivery
by the notification provider. Immediate notification delivery is not
guaranteed.

Backend push remains an in-process background task. Process termination can
lose an unsent attempt; there is no durable retry, provider receipt validation,
or persisted notification audit yet. This differs from the edge SQLite outbox,
which durably retries delivery of the alert to this backend.

## Privacy and security constraints

- No raw audio, audio bytes, playable recordings, or full audio sample arrays
  are accepted or stored.
- Exact transcripts are persisted once on the alert record for authenticated
  human review; routine notification and operational logs omit them.
- Evidence arrays/strings and nested objects have explicit bounds; the
  deployment platform should additionally enforce an HTTP body-size limit.
- Push tokens and secrets are never included in alert responses or logs.
- Read routes remain authenticated and role-restricted.
- Edge ingestion is currently unauthenticated and CORS remains broadly
  configured. These are known limitations retained to avoid breaking the
  deployed edge. A credential rollout and origin restriction are recommended.
- Notification delivery is an attempt, not a guarantee.

## Rollback

Downgrade from `20260729_0006` to `20260728_0005`:

```bash
ALEMBIC_DATABASE_URL=postgresql://... alembic downgrade 20260728_0005
```

The downgrade drops `severity_evidence`, removes the severity check, and
returns the three supported database values to lowercase for the previous
application. Export any newly collected severity evidence before downgrade if
it must be retained. Application rollback and database downgrade should be
coordinated; running the old application against the new uppercase database
would break lowercase analytics.
