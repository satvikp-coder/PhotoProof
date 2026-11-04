with open('CHANGELOG.md', 'r') as f:
    content = f.read()

old = "deduplication.\n\n### Changed"
new = """deduplication.

- plugins/copymove/clustering/` implements copy-move clustering (#22).
  Groups matched keypoint pairs into clusters using hierarchical clustering
  (Ward linkage) or DBSCAN on displacement vectors. Produces per-cluster
  bounding boxes, displacement vectors, and sizes.

### Changed"""

if old in content:
    content = content.replace(old, new)
    with open('CHANGELOG.md', 'w') as f:
        f.write(content)
    print('REPLACED')
else:
    print('NOT FOUND')