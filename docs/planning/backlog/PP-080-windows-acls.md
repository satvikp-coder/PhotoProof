---
id: PP-080
title: "security(storage): restrict runtime directory access on Windows"
milestone: M6
labels: [type:security, priority:P2, area:security, area:storage, effort:small, status:ready]
depends_on: []
---
## Summary

Apply and verify owner-only ACLs on `ARE/` on Windows, where POSIX modes (`0700`,
`0400`) have no effect.

## Problem Statement

Found in the code review: `RuntimeStorage.ensure_layout` and `insecure_dirs` only
handle POSIX permissions. On Windows, runtime data, including uploaded evidence,
inherits the parent folder's ACL (often readable by other local users), and the
`runtime.permissions` health check always reports OK.

## Objective

Owner-only access to `ARE/` on Windows, applied on creation and verified by the health
check.

## Technical Background

Use `icacls <dir> /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F"` via `subprocess`
(argument list), or the Win32 security APIs via `ctypes`. Read-only blobs map to
`FILE_ATTRIBUTE_READONLY` plus an ACL without write rights.

## Functional Requirements

- `ensure_layout` applies owner-only ACLs on Windows.
- `insecure_dirs` detects ACL entries that grant access to other principals.
- `runtime.permissions` SAFE repair re-applies the ACLs.
- Blobs are marked read-only on Windows too.

## Technical Implementation

`core/storage/store.py` with a small Windows-only helper. Prefer `ctypes`; document if
`icacls` is used.

## Integration Requirements

Health check output is unchanged on POSIX.

## Security and Edge Cases

Domain accounts. Folders on network shares (ACLs may be unsupported, so warn rather than
fail). Never remove SYSTEM or Administrators if that would break backups (document the
choice).

## Testing Requirements

Windows CI tests: a created directory has exactly the owner ACE (plus documented system
ACEs); an added ACE is detected and repaired.

## Acceptance Criteria

- [ ] Runtime directories are owner-only on Windows (CI test).
- [ ] `runtime.permissions` detects and repairs broadened ACLs on Windows.
- [ ] Blobs are read-only on Windows.

## Expected Outcome

The same evidence-protection guarantees on all supported operating systems.

## Documentation Requirements

Security model (privilege restrictions), operations runtime docs.

## Dependencies

None.

## Priority and Effort

P2, small.
