# ADR-026: Installed-wheel configuration and runtime roots

## Status

Implemented on the unmerged issue79 feature branch. A local fresh-environment
installation smoke and full local regression have passed; GitHub matrix validation
and main integration remain pending. This does not claim
an installed wheel is supported by current main before integration.

## Decision

Choose issue79 option1: support installed wheels. At build time, package the actual
app.toml, settings.yaml and configs tree under photoproof/_defaults. Do not introduce
another version literal. Include the same sources in sdists so rebuilding a wheel
retains defaults. Package discovery remains fixed trusted code; configuration cannot
add import locations or plugins.

`photoproof --root PATH COMMAND` selects a configuration/runtime home. Precedence is
explicit CLI root, the embedding caller's explicit root, allowlisted PHOTOPROOF_HOME,
then checkout root when running source, or a platform user data directory when
installed. PHOTOPROOF_HOME must be absolute. Installed defaults are under the user's
home (Windows AppData/Local/PhotoProof, macOS Library/Application Support/PhotoProof,
or Linux .local/share/photoproof), never a shared temporary directory. Selection and
bootstrap do not create these directories.

Installed app.toml is always read from the distribution; a stale runtime-home
identity copy cannot shadow an upgraded wheel. Init does not copy installed identity
into editable runtime configuration. Source checkout identity behavior remains unchanged.
Read explicit settings/plugin configuration first, otherwise fixed packaged defaults. A
present malformed operator file fails rather than silently falling back. An existing
profiles directory defines the complete profile set; it is not silently merged with
packaged profiles. Plugin configuration fingerprints hash the file actually loaded,
including packaged fallbacks. Built-in defaults cannot choose executable code.

`photoproof --root PATH init` copies missing defaults exclusively with owner-only
file mode where supported. It never overwrites an existing file or link, and does
not write runtime evidence. The launcher keeps its source-checkout behavior. Health
checks measure the nearest existing ancestor when a new runtime home does not yet
exist; normal preflight owns runtime directory creation and permissions.

Accept the src/photoproof layout or the actual package location recorded in this
installed distribution's metadata. A different imported package or mismatched
identity still fails. Wheel installation does not relax decoder isolation, evidence
integrity, plugin policy or result semantics.

## Validation required

Build the wheel, create a fresh virtual environment outside the checkout, install
hash-pinned runtime wheels and this distribution, remove PYTHONPATH overrides, and
run status, init and an actual generated-image analysis. Assert the imported package
is in that environment, configuration copies remain intact on repeat init, a real
manifest is written, and original input bytes remain unchanged. The CI install
smoke must use these same boundaries; a successful build alone is insufficient.


The initial local fresh-wheel check exercised the standard profile on a generated
32x32 PNG. Startup, decoded preview, immutable evidence and manifest persistence
passed, while the run reported INCOMPLETE for the existing copy-move interchange
mismatch tracked by PR163/#21. The smoke records failed plugins explicitly and does
not certify detector accuracy or whole-product completion. Pipeline health and
packaging functionality remain distinct; further core fixes must be integrated and
tested on main before claiming a complete forensic application.
