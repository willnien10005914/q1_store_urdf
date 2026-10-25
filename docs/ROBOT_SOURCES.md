# Robot sources and bundled evidence

Q1 Store keeps a self-contained snapshot of the two user-provided robot projects.
The native URDF bytes, inertial values, mesh scales and referenced STL files are
preserved. The browser model is a geometric preview; it does not execute physics.

| Type | Source project | Native model | Policy control contract |
| --- | --- | --- | --- |
| 雙足 / leg | [q1_leg_urdf](https://github.com/willnien10005914/q1_leg_urdf) | `urdf/q1_inertia/quanta_robot_legged.urdf` | 23 controlled joints; 29 movable joints including 6 locked wrist/finger joints |
| 雙輪 / wheel | [q1_wheel_urdf](https://github.com/willnien10005914/q1_wheel_urdf) | `urdf/q1_inertia/quanta_robot_wheeled.urdf` | 23 controlled joints; 27 movable joints including 4 mimic linkage joints |

The generated [manifest](../assets/robots/manifest.json) records the exact source
commit, URDF SHA-256, every referenced mesh SHA-256, joint limits and axis vectors,
mass summed from source inertials, controlled joint names, and sync time. A source
checkout with unrelated local changes is reported as `source_dirty`; hashes still
identify the exact copied files. The original source checkouts are untouched by the
default sync operation. Only URDF-referenced meshes are copied.

```bash
python3 scripts/sync_robots.py --source-root ~/will
# Explicitly update source checkouts first; refuses dirty sources and non-FF merges.
python3 scripts/sync_robots.py --source-root ~/will --pull
```

`--pull` uses each checkout's existing upstream branch. Review the resulting
manifest and browser previews after updates; a new URDF is not automatically
compatible with an existing policy. The store never applies a leg checkpoint to
wheel or automatically migrates legacy action/observation contracts.

## Included motion examples

- **Leg Sidestep:** `docs/results/q1_leg_sidestep_6144_8000.mp4` from the leg project,
  recorded with its selected `model_7800.pt` PPO. The source evidence records a
  7.78-second native Isaac Sim / PhysX rollout and independent simulation evaluation
  survival of 31/32. The bundled [evidence](../assets/examples/leg/evidence.json)
  preserves checkpoint and video hashes and removes the original author's absolute
  machine path. It does not establish real hardware performance. Its URDF hash is
  compared to the current manifest rather than assuming compatibility.
- **Wheel Skate:** `docs/results/q1_skate_cubemars_iter3800.mp4` from the wheel
  project. This is a historical Isaac Sim / RSL-RL PPO recording using the legacy
  24-action, 92-observation model. It is explicitly incompatible with the current
  native 23-action wheel URDF. The [evidence](../assets/examples/wheel/evidence.json)
  links the source README and records video/checkpoint hashes. It is provided as a
  catalog example, not as a successful new training job.
- **MediaPipe reference:** the leg project's `docs/reference/sidestep_motion.json`
  and `sidestep_landmarks.json` are included for retargeting reference playback.
  Their source is the [first seven seconds of the source YouTube clip](https://youtu.be/KKhF516rSyo).
  The motion metadata records 306/306 accepted source frames, model/video hashes,
  monocular assumptions and 20 Hz native joint mapping. This is reference motion,
  not a PPO output. The original human video and MediaPipe model are not bundled.

## User-selected robot video excerpts

The store also includes five short, silent reference excerpts from the two URLs
selected by the user. They are source demonstrations of other robots, not Q1
training results or evidence of Q1 hardware performance.

| Target | Source and publisher | Original ranges | Bundled excerpts |
| --- | --- | --- | --- |
| Q1 Leg | [Unitree Introducing: H2-X Kung Fu Legend Guardian](https://youtu.be/Y3zb4o47yqA), Unitree Robotics | 33–36 s; 36–39 s; 39–41.8 s | `assets/video-examples/leg/h2x-{guard,punch,turn-kick}.{mp4,jpg,gif}` |
| Q1 Wheel | [Meet China's AgiBot X2-N, a shape-shifting robot](https://youtu.be/TV6ctYnA9Io), China SCIO | 24–28 s; 37–40.4 s | `assets/video-examples/wheel/x2-{slope,turn}.{mp4,jpg,gif}` |

The [video example catalog](../assets/video-examples/catalog.json) records source
URLs, titles, publishers, excerpt ranges, source video hashes and excerpt MP4
hashes. MP4/poster dimensions are 640×360; GIFs are 480×270. Original watermarks
remain visible and the excerpts have no audio. Full originals and the full yt-dlp
metadata stay in an ignored external cache and are not shipped in this repository.

[prepare_video_examples.py](../scripts/prepare_video_examples.py) reproduces the
five selected clips and their GIFs. It uses ffmpeg and optionally yt-dlp; the
[video tutorial](VIDEO_TRAINING_EXAMPLES.md) gives the commands and maps local clip
seconds to original-video time. It never trains a policy or changes URDF assets.

MediaPipe's human pose model is not validated for these robot appearances. Each
excerpt is marked `robot_video_reference`, `requires_manual_review=true`,
`mediapipe_human_model_validated=false`, and `newly_trained=false`. Detection may
miss the robot or follow a background person. Joint, scale, contact and motion
references require verification; wheel velocities cannot be inferred from human
foot trajectories. The bundled excerpt GIFs do not depict newly trained Q1 PPOs.

No checkpoint binaries, complete source videos, source caches, local logs,
downloaded human video or upload-service response/management credentials are copied.

## Third-party notices

The native CAD/URDF assets are user-provided. The source leg project's
[`THIRD_PARTY.md`](https://github.com/willnien10005914/q1_leg_urdf/blob/568b33229e31cc503b6729d1185c625d35a711a4/THIRD_PARTY.md)
states that no redistribution license is asserted for those CAD files. This
snapshot does not assign a new license or grant additional rights to those assets
or to the source human or robot videos. Attribution and excerpts do not assign a new license to their source footage.

The browser's vendored modules are copied unchanged from the leg project's
`web/vendor` (itself reused from the wheel project):

- [Three.js](https://github.com/mrdoob/three.js), MIT; retained license:
  [`public/vendor/three/LICENSE`](../public/vendor/three/LICENSE).
- [URDFLoader](https://github.com/gkjohnson/urdf-loaders), Apache-2.0; retained
  license: [`public/vendor/urdf-loader/LICENSE`](../public/vendor/urdf-loader/LICENSE).

The sync script parses Python configuration as syntax without importing or
executing Isaac code. It needs only Python 3.11+ and Git; simulation/training
dependencies are not needed to update the store snapshot.
