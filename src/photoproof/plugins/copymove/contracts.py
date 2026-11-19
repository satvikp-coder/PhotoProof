"""Compatibility imports for the shared version-2 copy-move contracts.

The canonical types live in photoproof.contracts, so all stages can depend on
contracts without importing another detector. Version-1 serialized data is not
silently reinterpreted; see the contract migration documentation.
"""

from photoproof.contracts.copymove import (
    Cluster,
    Clusters,
    Keypoints,
    Mask,
    Matches,
    Region,
    Verified,
    VerifiedTransform,
)

__all__ = [
    "Cluster",
    "Clusters",
    "Keypoints",
    "Mask",
    "Matches",
    "Region",
    "Verified",
    "VerifiedTransform",
]
