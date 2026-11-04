"""RoboParty-aligned motion-tracking pipeline for Q1 Store.

Follows the RP1 training docs (BeyondMimic + HHTools retarget) rather than
MediaPipe-only geometric mimic:

* https://roboparty.com/documents/roboto-01?doc=training%2Findex&lang=en
* https://github.com/Roboparty/human-humanoid-tools
* https://github.com/Roboparty/roboparty_train
* https://github.com/Roboparty/RP1

Store still trains Q1 Leg in Isaac Lab; the change is the **reference contract**
(root trajectory + optional body fields) and the **import path** from HHTools
CSV/PKL exports.
"""
from .paths import discover_roboparty, status as roboparty_status
from .hhtools_import import parse_hhtools_csv, parse_hhtools_upload, motion_payload_from_hhtools
from .reference_bm import build_beyondmimic_reference
from .youtube_pipeline import run_youtube_to_motion, slice_hhtools_csv, qa_leg_motion

__all__ = [
    "discover_roboparty",
    "roboparty_status",
    "parse_hhtools_csv",
    "parse_hhtools_upload",
    "motion_payload_from_hhtools",
    "build_beyondmimic_reference",
    "run_youtube_to_motion",
    "slice_hhtools_csv",
    "qa_leg_motion",
]
