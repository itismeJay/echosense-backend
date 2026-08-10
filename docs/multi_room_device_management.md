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
