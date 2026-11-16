"""DistrictSync's wiring for the installed build's updater (plan 0054).

Wiring only: when to start the Velopack runtime, later when to check and what "healthy" means.
The manifest check, fetch, staging, hand-off and rollback live in the shared private package
``myb_desktop_updater`` (slice S3 onward) and are never written here.

Nothing in this package may be imported at module level by code that runs in the portable
build; ``src.main`` imports ``src.update.startup`` lazily, and that module imports ``velopack``
only inside the installed build.
"""
