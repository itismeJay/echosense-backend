# Edge device authentication and classroom assignment

Required alert wording: “Unverified possible-aggression alert. Human review required.”
Device authentication identifies the approved sensor and its assigned classroom; it does not
identify a speaker or establish intent or guilt.

## Register a device

An authenticated administrator registers one device per Raspberry Pi. The
server generates the key and stores only its bcrypt hash.

```bash
curl -X POST "$BACKEND_URL/devices" \
  -H "Authorization: Bearer <ADMIN_JWT>" \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "classroom-101-pi",
    "display_name": "Room 101 EchoSense",
    "classroom_name": "Room 101",
    "school_name": "Example School"
  }'
```

The response contains `device_key` exactly once. Copy it directly into a
root-owned Raspberry Pi environment file; do not paste it into chat, tickets,
logs, shell history, or source control.

```bash
sudo install -m 600 -o root -g root /dev/null /etc/echosense-edge.env
sudoedit /etc/echosense-edge.env
```

Set placeholder values in that file:

```dotenv
ECHOSENSE_DEVICE_ID=classroom-101-pi
ECHOSENSE_DEVICE_KEY=<DEVICE_KEY>
```

Configure the edge service with
`EnvironmentFile=/etc/echosense-edge.env`, then restart it only after the
authenticated edge release is installed.

## Alert ingestion

The backend requires both headers:

```bash
curl -X POST "$BACKEND_URL/alerts/" \
  -H "Content-Type: application/json" \
  -H "X-EchoSense-Device-Id: classroom-101-pi" \
  -H "X-EchoSense-Device-Key: <DEVICE_KEY>" \
  -H "Idempotency-Key: 00000000-0000-4000-8000-000000000001" \
  -d '{
    "event_id": "00000000-0000-4000-8000-000000000001",
    "schema_version": 2,
    "trigger_type": "KEYWORD",
    "severity": "LOW",
    "severity_reasons": ["synthetic_test_reason"],
    "review_message": "Unverified possible-aggression alert. Human review required.",
    "device_identifier": "classroom-101-pi",
    "event_start_timestamp": "2026-08-04T00:00:00Z",
    "event_end_timestamp": "2026-08-04T00:00:00.500Z",
    "confidence": 0.50,
    "duration": 0.50,
    "transcribed_text": "Synthetic controlled test transcript.",
    "language": "en",
    "yamnet_ran": false,
    "test_mode": false
  }'
```

Missing, unknown, or incorrect credentials return HTTP 401 with
`{"detail":"Invalid device credentials"}`. A known disabled device returns 403
with the same generic detail. Authentication errors must retain the SQLite
outbox event and use its existing backoff; they must never print credentials.

The request body cannot select its trusted device or assignment. New alert
responses add nullable `device_id`, `device_code`, `device_display_name`,
`classroom_name`, and `school_name`. Classroom and school are snapshots taken
on first ingestion. Historical alerts remain readable with null device
context. Existing `event_id`, evidence, severity, review notice, timestamps,
and notification behavior are unchanged.

## Disable or rotate

Disable a lost device without deleting it:

```bash
curl -X PATCH "$BACKEND_URL/devices/<DEVICE_UUID>" \
  -H "Authorization: Bearer <ADMIN_JWT>" \
  -H "Content-Type: application/json" \
  -d '{"is_active":false}'
```

Rotate a compromised key:

```bash
curl -X POST "$BACKEND_URL/devices/<DEVICE_UUID>/rotate-key" \
  -H "Authorization: Bearer <ADMIN_JWT>"
```

Store the new one-time key, restart the edge service, and verify delivery. The
old key stops working immediately. List and detail routes never return a key or
hash.

## Production rollout and rollback

1. Back up the database and deploy migrations through `20260804_0008` with the backend.
2. Register the approved device and store its one-time key securely.
3. Configure the Raspberry Pi environment and restart the edge service.
4. With explicit approval, send one harmless controlled ingestion test.
5. Confirm one alert/device association and no duplicate alert or notification.
6. Proceed to a microphone test only after separate approval.

To roll back before authenticated alerts are operational, restore the previous
backend release and run:

```bash
ALEMBIC_DATABASE_URL=postgresql://... alembic downgrade 20260729_0006
```

Downgrade removes the device table and the three nullable alert assignment
columns; it does not delete historical alert rows. Export device assignments
first if they must be retained. If edge authentication is already configured,
coordinate application rollback because the prior backend accepts no device
headers as authentication.
