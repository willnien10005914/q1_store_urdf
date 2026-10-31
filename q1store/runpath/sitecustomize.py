"""Auto-loaded when ``q1store/runpath`` is on PYTHONPATH (tutorial launches).

Installs the kitless PhysX URDF importer patch for every run, including old
drafts that never call ``lab3_compat.patch_urdf_importer()``.
"""
try:
    import lab3_compat

    lab3_compat.install_import_hook()
except Exception as exc:  # noqa: BLE001 — never block interpreter startup
    print(f"[q1] lab3_compat hook skipped: {exc}", flush=True)
