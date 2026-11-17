with open("src/photoproof/plugins/copymove/clustering/algorithm.py") as f:
    content = f.read()

old = """    elif method == "dbscan":
        clusters = _dbscan_clustering(
            displacements, eps, min_matches_per_cluster, keypoints  # type: ignore[arg-type]
        )"""
new = """    elif method == "dbscan":
        clusters = _dbscan_clustering(
            normalized_pairs, displacements, eps, min_matches_per_cluster, keypoints
        )"""

if old in content:
    content = content.replace(old, new)
    with open("src/photoproof/plugins/copymove/clustering/algorithm.py", "w") as f:
        f.write(content)
    print("REPLACED")
else:
    print("NOT FOUND")
    lines = content.splitlines()
    for i, line in enumerate(lines[239:243], start=240):
        print(f"{i}: {line!r}")
