# Multi-Room Device Management

EchoSense treats the authenticated device identity as the only trusted input for physical
location. An alert request cannot select a classroom or school. For each new alert the backend
resolves:

```text
X-EchoSense-Device-Id + X-EchoSense-Device-Key
    -> edge_devices.classroom_id
    -> classrooms.school_id
    -> alerts.classroom_id + alerts.school_id + name snapshots
```

The alert FKs and name snapshots are written once. Reassigning a device changes only future
alerts. An exact replay of an existing event UUID returns the original alert with its original
attribution.

## Management API

All management routes require an `admin` bearer token. School-scoped administrators can access
only their school. Administrators explicitly marked `is_super_admin` may operate across schools.
Alert reviewers (`admin`, `staff`, and `counselor`) are likewise restricted to records whose
`school_id` exactly matches their school. Legacy alerts with no attributable school are hidden
from normal users and are inspectable only by an explicit super-admin. Logs, statistics, reports,
and audit-log reads use the same boundary.

- `POST /classrooms` creates a classroom.
- `GET /classrooms` lists classrooms, optionally filtered by `school_id` or `is_active`.
- `GET /classrooms/{classroom_id}` returns school and assigned-device metadata.
- `PATCH /classrooms/{classroom_id}` renames, deactivates, or reactivates a classroom.
- `POST /devices` registers a device and returns its generated key once.
- `GET /devices` filters by `school_id`, `classroom_id`, `is_active`, or `unassigned`.
- `GET /devices/{device_id}` returns safe device and assignment metadata.
- `PATCH /devices/{device_id}` updates compatible display/active metadata.
- `POST /devices/{device_id}/assign` assigns or reassigns a device.
- `POST /devices/{device_id}/unassign` retains school ownership but removes the classroom.
- `POST /devices/{device_id}/disable` and `/enable` control authentication.
- `POST /devices/{device_id}/rotate-key` immediately replaces the credential and returns the new
  key once.

An unassigned device is registered for provisioning but cannot create classroom alerts. An
inactive classroom likewise cannot receive a new assignment or accept alerts from an already
assigned device.

Key rotation has no grace period. The old key becomes invalid when the transaction commits, so
the physical device must receive the returned new key before authenticated sending can resume.

Push-recipient selection is restricted to the alert's backend-authoritative school while
preserving the existing role and token rules. Classroom-specific Staff Routing is not yet
implemented.

The migration preserves a deterministically known school even when a legacy classroom name is
blank or unknown; in that case `school_id` is set and `classroom_id` remains null. Ambiguous or
unknown school ownership remains fully unattributed. Fresh empty installations still require a
School to be provisioned manually before classroom management begins. Downgrading after valid
unassigned devices have been provisioned retains the known legacy nullability limitation; this
does not affect forward migration or normal Multi-Room operation.

## Development user seeding

`seed_users.py` creates one development user per invocation. The operator must explicitly supply
`ECHOSENSE_SEED_USER_PASSWORD` and either `--email` or `ECHOSENSE_SEED_USER_EMAIL`; the helper has
no credential defaults and never prints the plaintext password. The role defaults to `admin` with
`is_super_admin=false`, not a global administrator. Associate that account with an explicitly
provisioned school before using it for normal school administration. Run the helper again with
different explicit inputs to create additional development users.

A fresh installation that deliberately needs a bootstrap global administrator may use
`--role admin --super-admin`. The flag is rejected for non-admin roles. Store seed inputs in an
untracked local environment or secret manager, never in source control or shell scripts. The
bootstrap account must be reviewed and replaced or reduced to the intended long-term access after
initial provisioning.

## Required production account audit before revision 20260810_0009

Revision `20260810_0009` intentionally preserves compatibility by marking pre-existing
administrators as super-admins. Before running the migration, an authorized operator must use the
production database's read-only SQL console to inspect the current revision and administrator
identities:

```sql
SELECT version_num FROM alembic_version;

SELECT id, email, role
FROM users
WHERE lower(role) = 'admin'
ORDER BY id;
```

Compare every returned identity with the approved production administrator roster and the known
historical development-seed identities. Do not select or export `hashed_password`, push tokens,
device credentials, or authentication tokens. Any development-derived, unknown, or unnecessary
account must be removed, disabled, or have its credentials rotated through a separately approved
production procedure before migration. This audit is a mandatory gate; it does not automate any
account mutation.

If the database is already at or beyond revision `20260810_0009`, inspect the resulting privilege
state as well:

```sql
SELECT id, email, role, is_super_admin
FROM users
WHERE lower(role) = 'admin' OR is_super_admin IS TRUE
ORDER BY id;
```
