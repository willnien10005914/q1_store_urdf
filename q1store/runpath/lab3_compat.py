"""Kitless Isaac Lab 3 helpers for Q1 Store tutorial runs.

Call ``patch_urdf_importer()`` inside ``launch_simulation(...)`` before spawning
URDF assets. Full Isaac Sim is not required for newton_mjwarp, but the
standalone ``importers`` extra still references PhysxSchema APIs that are absent
from usd-exchange-only environments.
"""
from __future__ import annotations

_PATCHED = False


def patch_urdf_importer() -> bool:
    """Replace ``add_joint_schemas`` when PhysxSchema is unavailable. Returns True if patched."""
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from pxr import PhysxSchema  # noqa: F401

        if getattr(PhysxSchema, "JOINT_API", None) or getattr(PhysxSchema, "PhysxJointAPI", None):
            _PATCHED = True
            return False
    except Exception:
        pass
    try:
        import isaacsim.asset.importer.utils.impl.importer_utils as importer_utils
        from pxr import UsdPhysics
    except Exception:
        return False

    def add_joint_schemas(stage) -> None:
        for prim in stage.Traverse():
            if not (prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.PrismaticJoint)):
                continue
            instance_name = "angular" if prim.IsA(UsdPhysics.RevoluteJoint) else "linear"
            if not prim.HasAPI(UsdPhysics.DriveAPI, instance_name):
                UsdPhysics.DriveAPI.Apply(prim, instance_name)

    importer_utils.add_joint_schemas = add_joint_schemas
    _PATCHED = True
    return True
