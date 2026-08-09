# Local development

This stack is intentionally separate from production. It never needs the Render URL or Neon
credentials. PostgreSQL binds only to Mac loopback; FastAPI binds to all Mac interfaces so a
Raspberry Pi on the same trusted LAN can reach it.

| Environment | Backend | Database |
| --- | --- | --- |
| Local | `http://<MAC-LAN-IP>:8000` | Docker PostgreSQL `echosense_dev` on `127.0.0.1:55432` |
| Production | `https://echosense-backend-75h3.onrender.com` | Neon PostgreSQL |

Application source does not change when switching. Local commands load `.env.local`; Render
continues to supply its own production environment. Never copy production database credentials,
JWT secrets, push tokens, or device keys into the local files.

## First setup

Use Python 3.11 and the existing development requirements:

```bash
python3.11 -m venv .venv-local
.venv-local/bin/pip install -r requirements-dev.txt
python3.11 scripts/init_local_env.py
./scripts/local_db.sh up
```

The environment generator creates `.env.local` with private, random local credentials and mode
`0600`; it never displays them. Both `.env.local` and the one-time `.env.local.device` key file are
gitignored. The Compose database has a persistent named volume and a healthcheck. `local_db.sh up`
waits for health and upgrades only the guarded loopback development database to Alembic head.

Start FastAPI in a second terminal:

```bash
./scripts/dev.sh
```

On the first setup, seed while the backend is running:

```bash
.venv-local/bin/python scripts/seed_local.py
```

The seed is idempotent. It bootstraps the local admin, creates staff and counselor accounts through
the real user API, and registers `classroom-pi-01` through the real device API. Its school and room
assignment are `EchoSense Development` and `Test Classroom`. The one-time device key is stored in
`.env.local.device` and is never printed. If the database volume is deliberately removed, also
remove the now-stale key file before reseeding; neither action is part of normal daily use.

Run the controlled device-auth and schema-v2 check once the seed exists:

```bash
.venv-local/bin/python scripts/verify_local.py
```

It submits one privacy-safe `TEST` event with no transcript or audio, checks 401/401/200/403 device
authentication behavior, restores the device to active, verifies trusted classroom/device
snapshots, verifies the row in local PostgreSQL, and checks identical retry/changed retry behavior.
Local controlled mode deliberately skips Expo delivery.

## Daily use

```bash
./scripts/local_db.sh up
./scripts/dev.sh
curl --fail --silent http://127.0.0.1:8000/health
./scripts/local_db.sh down
```

Run `local_db.sh down` after stopping Uvicorn with Ctrl-C. It preserves the PostgreSQL volume. Do
not add `--volumes` unless intentionally discarding all local data.

## Mac LAN address and Raspberry Pi URL

`dev.sh` detects the current private address and adds its port-3000 origin to CORS for that process.
It does not hardcode or write the address. To inspect it directly on macOS:

```bash
interface_name="$(route -n get default | awk '/interface:/{print $2}')"
ipconfig getifaddr "$interface_name"
```

The Pi backend URL is `http://<MAC-LAN-IP>:8000`. Check both paths while Uvicorn is running:

```bash
curl --fail --silent http://127.0.0.1:8000/health
curl --fail --silent "http://$(.venv-local/bin/python scripts/lan_ip.py):8000/health"
```

Both should return `{"status":"ok"}`. LAN access also requires both devices on the same network,
no wireless client isolation, and macOS Firewall permission for the Python/Uvicorn process. The
database itself remains unavailable to the LAN because Compose publishes it only on `127.0.0.1`.

## Tests and migrations

The Compose initialization also creates the isolated `echosense_test` database. Migrate it and run
validation without ever inheriting the development or production application database:

```bash
./scripts/test_local.sh
.venv-local/bin/ruff check .
.venv-local/bin/ruff format --check .
```

## Future local mobile push

Local phone push is not required for backend development. A future dev mobile build will need an
environment-configured API base URL pointing to `http://<MAC-LAN-IP>:8000`, permission for local
cleartext HTTP if its Android network policy requires it, the phone on the same LAN, and a dev Expo
push token registered to a local user. Expo delivery also needs outbound internet and, if the Expo
project requires it, a local `EXPO_ACCESS_TOKEN`. Prefer a distinct development app/project and
token; do not copy production mobile configuration or credentials. Deliberately configure a single
controlled local recipient before enabling sends.
