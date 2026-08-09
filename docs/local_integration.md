# Local integration with Neon

This mode replaces only Render during integration testing:

```text
Raspberry Pi -> Mac FastAPI :8000 -> existing Neon database
```

| Mode | Backend | Database |
| --- | --- | --- |
| Local isolated | Mac FastAPI | Docker PostgreSQL |
| Local integration | Mac FastAPI | Existing Neon |
| Production | Render FastAPI | Existing Neon |

It uses the same FastAPI application as production. It does not start, stop, migrate, seed, reset,
or otherwise manage Neon. The isolated Docker PostgreSQL mode remains available separately through
`./scripts/local_db.sh up` and `./scripts/dev.sh`.

Render declares its database, migration URL, JWT secret, controlled-recipient values, and Expo token
as private `sync: false` environment variables. `render.yaml` supplies the non-secret production
policy values. The integration initializer copies the existing private `.env` Neon `DATABASE_URL`
and `SECRET_KEY` into a separate gitignored file without displaying them:

```bash
.venv-local/bin/python scripts/init_integration_env.py
```

The generated `.env.integration` is mode `0600`. It deliberately contains no
`ALEMBIC_DATABASE_URL` or test database URL, disables legacy startup maintenance and synthetic
alerts, and keeps production CORS behavior. If an Expo token is present in the private source file,
it is copied without display; otherwise it remains unset. Push delivery is not exercised by this
setup check.

Start the Neon-backed backend:

```bash
./scripts/dev_integration.sh
```

Before Uvicorn starts, the script verifies that the configured URL is Neon and exactly matches the
existing private `.env`, refuses migration/test URLs, and checks connectivity, migration head,
required tables, finalized alert columns, and existing data inside a PostgreSQL read-only
transaction. It never invokes Alembic.

Expected startup status:

```text
EchoSense Local Integration
Backend: http://<MAC-LAN-IP>:8000
Database: Neon
Environment: integration
```

While it is running:

```bash
curl --fail --silent http://127.0.0.1:8000/health
curl --fail --silent "http://$(.venv-local/bin/python scripts/lan_ip.py):8000/health"
```

Stop Uvicorn with Ctrl-C. No Docker command is required for integration mode. The local PostgreSQL
container can remain running because integration mode does not reference it.
