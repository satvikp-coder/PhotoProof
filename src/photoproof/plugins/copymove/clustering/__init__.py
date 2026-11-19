"""Bounded spatial and displacement clustering of candidate correspondences."""

METADATA = {
    "id": "copymove.clustering",
    "name": "Copy-move Candidate Clustering",
    "description": "Groups verified-artifact candidate pairs into unverified geometric hypotheses.",
    "version": "2.0.0",
    "api_version": "1.7.0",
    "priority": 30,
    "required": False,
    "enabled_default": True,
    "requires": ("copymove.matching", "copymove.sift_keypoints"),
    "after": (),
    "entry_point": "processor:run",
    "inputs": ("copymove.matches", "copymove.keypoints", "image.geometry"),
    "outputs": ("copymove.clusters", "copymove.cluster_stats"),
    "timeout_seconds": 60,
    "supported_formats": (),
    "deterministic": True,
    "config_schema": "schema:Config",
    "python_dependencies": ["numpy"],
}
