"""Platform-independent policy tests complement actual Windows integration tests."""

from unittest.mock import patch

import pytest

from photoproof.contracts.exceptions import StorageError
from photoproof.core.storage import windows_acl as acl

DOMAIN_SID = "S-1-5-21-111111111-222222222-333333333-1234"


def test_domain_sid_serialized_without_username_resolution() -> None:
    sddl = acl.descriptor_sddl(DOMAIN_SID, directory=True)
    assert sddl.startswith(f"O:{DOMAIN_SID}D:P")
    assert f"(A;OICI;FA;;;{DOMAIN_SID})" in sddl
    assert "S-1-1-0" not in sddl and "S-1-5-11)" not in sddl
    with pytest.raises(StorageError, match="invalid user SID"):
        acl.descriptor_sddl("DOMAIN\\user", directory=True)


def test_unrecognized_ace_forms_and_inherit_only_aces_are_not_healthy() -> None:
    with patch.object(acl, "current_user_sid", return_value=DOMAIN_SID):
        normal = [
            acl.ACE(sid, acl.FULL_CONTROL, 3)
            for sid in (DOMAIN_SID, acl.SYSTEM_SID, acl.ADMINISTRATORS_SID)
        ]
        valid = acl.ACLSnapshot(DOMAIN_SID, True, tuple(normal), 16)
        assert acl.problems(valid, directory=True, protected=True) == ()
        for ace in [
            acl.ACE("S-1-1-0", acl.FILE_READ, 0x10),
            acl.ACE(DOMAIN_SID, acl.FULL_CONTROL, 0xB),
            acl.ACE(DOMAIN_SID, 0, 3, kind=9),
        ]:
            changed = acl.ACLSnapshot(DOMAIN_SID, True, (*normal, ace), 16)
            assert acl.problems(changed, directory=True)
