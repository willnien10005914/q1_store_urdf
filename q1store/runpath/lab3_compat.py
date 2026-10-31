"""Kitless Isaac Lab 3 helpers for Q1 Store tutorial runs.

The standalone ``importers`` extra references PhysxSchema APIs that are often
unregistered outside full Isaac Sim. Patch ``add_joint_schemas`` to only apply
``UsdPhysics.DriveAPI`` so URDF→USD works with ``newton_mjwarp``.

Call ``patch_urdf_importer()`` explicitly, or rely on ``install_import_hook()`` /
``sitecustomize`` / ``lab3_runner`` so every tutorial run is covered even when
an old draft omits the call.
"""
from __future__ import annotations

import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec

_PATCHED = False
_HOOK_INSTALLED = False
_TARGET = "isaacsim.asset.importer.utils.impl.importer_utils"


def _safe_add_joint_schemas(stage) -> None:
    from pxr import UsdPhysics

    for prim in stage.Traverse():
        if not (prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.PrismaticJoint)):
            continue
        instance_name = "angular" if prim.IsA(UsdPhysics.RevoluteJoint) else "linear"
        if not prim.HasAPI(UsdPhysics.DriveAPI, instance_name):
            UsdPhysics.DriveAPI.Apply(prim, instance_name)


def patch_urdf_importer(force: bool = False) -> bool:
    """Replace ``add_joint_schemas`` with a PhysX-schema-free version.

    Returns True when the module was patched (or already patched).
    """
    global _PATCHED
    if _PATCHED and not force:
        return True
    mod = sys.modules.get(_TARGET)
    if mod is None:
        try:
            import isaacsim.asset.importer.utils.impl.importer_utils as mod  # noqa: F401
        except Exception:
            return False
        mod = sys.modules.get(_TARGET)
        if mod is None:
            return False
    mod.add_joint_schemas = _safe_add_joint_schemas
    _PATCHED = True
    return True


class _PatchingLoader(Loader):
    def __init__(self, loader: Loader):
        self._loader = loader

    def create_module(self, spec: ModuleSpec):
        create = getattr(self._loader, "create_module", None)
        return None if create is None else create(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        module.add_joint_schemas = _safe_add_joint_schemas
        global _PATCHED
        _PATCHED = True


class _ImporterUtilsFinder(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):  # noqa: ANN001
        if fullname != _TARGET:
            return None
        for finder in list(sys.meta_path):
            if finder is self:
                continue
            try:
                spec = finder.find_spec(fullname, path, target)
            except Exception:
                continue
            if spec is None or spec.loader is None:
                continue
            if not isinstance(spec.loader, _PatchingLoader):
                spec.loader = _PatchingLoader(spec.loader)
            return spec
        return None


def install_import_hook() -> bool:
    """Install a MetaPathFinder so the next ``importer_utils`` import is patched."""
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        if _TARGET in sys.modules:
            return patch_urdf_importer()
        return True
    sys.meta_path.insert(0, _ImporterUtilsFinder())
    _HOOK_INSTALLED = True
    if _TARGET in sys.modules:
        return patch_urdf_importer()
    return True
