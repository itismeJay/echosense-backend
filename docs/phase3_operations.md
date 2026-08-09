# Phase 3 backend operations

Use only local, disposable databases for development and tests. Never point
`ECHOSENSE_TEST_DATABASE_URL` at the application database: the test fixture
truncates all application tables before every test.

## Local database and migration

Create separate development and test databases using a local PostgreSQL admin
account appropriate to the workstation. The test database name must contain
`test`; it must not be the database selected by `DATABASE_URL`:

```bash
createdb echosense_dev
createdb echosense_test
ALEMBIC_DATABASE_URL=postgresql://<user>:<password>@localhost:5432/echosense_dev \
  alembic upgrade head
ALEMBIC_DATABASE_URL=postgresql://<user>:<password>@localhost:5432/echosense_test \
  alembic upgrade head
```

Migration tests create and drop temporary databases and therefore require a
test role with database-create permission. Runtime tests require the base test
database to be migrated through `20260804_0008`.

## Start and register a development device

Set a strong local `SECRET_KEY`, an async runtime `DATABASE_URL`, configured
localhost CORS origins, and keep legacy maintenance disabled:

```dotenv
DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/echosense_dev
SECRET_KEY=<strong-local-secret>
RUN_LEGACY_STARTUP_MAINTENANCE=false
ECHOSENSE_CORS_ORIGINS=http://localhost:3000,http://192.168.1.92:3000,https://echosense-frontend.vercel.app
ECHOSENSE_ALLOW_TEST_ALERTS=true
```

Start the API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Sign in as an existing local administrator, then register a device:

```bash
curl -X POST http://localhost:8000/devices \
  -H "Authorization: Bearer <ADMIN_JWT>" \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "phase3-development-pi",
    "display_name": "Phase 3 Development Pi",
    "classroom_name": "Synthetic Classroom",
    "school_name": "Synthetic School"
  }'
```

The response displays `device_key` once. Store it outside source control and do
not put it in tickets or logs. Use the rotate-key endpoint described in
`device_authentication.md` if it is exposed.

## Submit a synthetic Phase 3 alert

Synthetic ingest is disabled unless `ECHOSENSE_ALLOW_TEST_ALERTS=true`. Use a
unique UUID for both `event_id` and `Idempotency-Key`:

```bash
curl -X POST http://localhost:8000/alerts/ \
  -H "Content-Type: application/json" \
  -H "X-EchoSense-Device-Id: phase3-development-pi" \
  -H "X-EchoSense-Device-Key: <DEVICE_KEY>" \
  -H "Idempotency-Key: 00000000-0000-4000-8000-000000000123" \
  -d '{
    "event_id": "00000000-0000-4000-8000-000000000123",
    "schema_version": 2,
    "trigger_type": "TEST",
    "severity": "LOW",
    "severity_reasons": ["synthetic_delivery_test"],
    "review_message": "Unverified possible-aggression alert. Human review required.",
    "device_identifier": "phase3-development-pi",
    "event_start_timestamp": "2026-08-04T00:00:00Z",
    "event_end_timestamp": "2026-08-04T00:00:00.500Z",
    "transcript": "Synthetic test transcript.",
    "transcription_status": "complete",
    "monitored_terms": [],
    "monitored_word_detected": false,
    "test_mode": true,
    "yamnet_ran": false,
    "language": "en"
  }'
```

Repeat the exact request to verify the same alert is returned. Change an
immutable field with the same UUID to verify HTTP 409. Query it through the
reviewer API:

```bash
curl "http://localhost:8000/alerts/?event_id=00000000-0000-4000-8000-000000000123" \
  -H "Authorization: Bearer <REVIEWER_JWT>"
```

## Tests

```bash
ECHOSENSE_TEST_DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/echosense_test \
  pytest -q tests/test_phase3_delivery.py tests/test_edge_device_authentication.py \
  tests/test_edge_audio_event_alerts.py tests/test_alert_evidence_and_authorization.py \
  tests/test_alert_severity_contract.py tests/test_notification_targeting.py \
  tests/test_provider_test_sender.py

ECHOSENSE_TEST_DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/echosense_test \
  pytest -q tests/test_render_migrate.py

ECHOSENSE_TEST_DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/echosense_test \
  pytest -q
```

## Push verification and recovery

Use controlled-test mode before any provider request. An accepted Expo ticket
means only provider submission acceptance. A physical phone is required to
verify receipt, banner display, Android channel selection, iOS/default sound,
and navigation. Do not use a broadcast production configuration for tests.

Push attempt state is best-effort on the alert row. There is no durable worker,
automatic retry, or receipt polling. If a process stops with a pending attempt,
the alert remains stored and must be inspected manually.

To roll the finalized contract back while retaining alert rows:

```bash
ALEMBIC_DATABASE_URL=postgresql://<user>:<password>@localhost:5432/echosense_test \
  alembic downgrade 20260730_0007
```

Export finalized evidence first because the downgrade removes the new columns.
Deployment must terminate HTTPS outside the application.
