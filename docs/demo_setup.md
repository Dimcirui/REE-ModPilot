# Demo Setup — MVP Acceptance Walkthrough

This document is the first-time-user setup guide for the REE-ModPilot MVP.
It tells you what to install, what to download, and how to lay assets out
on disk so `verify_mvp.py` (and an end-to-end agent run) can complete a full
MHWs character mod from a clean machine.

By design (D15), no demo assets are bundled in the repo. License /
file-size constraints make it impractical, and the toolkit pipeline assumes
the user owns Monster Hunter Wilds and Blender separately.

---

## 1. Prerequisites

### Software

| Component | Version | Notes |
|---|---|---|
| Windows | 10 / 11 | Linux + macOS are unsupported by the underlying toolkits |
| Blender | **4.3.2** | Other 4.x usually works; report breakage |
| Monster Hunter Wilds | retail | Needed only for in-game L3 verification — not for the verify_mvp.py script |
| Python | 3.11+ | Bundled by `uv`; you do not need a system install |

### Blender addons (install in this order)

1. **[Modding-Toolkit](https://github.com/example/Modding-Toolkit)** — the
   `bpy.ops.modder.* / mhws.* / mbt.*` operator surface this project drives.
   Provides preset-aware skeleton align, vertex-group rename, physics
   transplant, MDF2 generator, MHWs batch exporter.
2. **[Modder-Batch-Tool](https://github.com/example/Modder-Batch-Tool)** —
   ships the MHWs reference armature import (`mbt.import_mhwilds_fmesh`)
   and a few quality-of-life batch wrappers.
3. **[RE Mesh Editor](https://github.com/NSACloud/RE-Mesh-Editor)** —
   reads/writes RE Engine `.mesh` and `.mdf2`. Required by Phase 5 (material
   generation) and Phase 6 (mesh export).
4. **[RE Chain Editor](https://github.com/NSACloud/RE-Chain-Editor)** —
   reads/writes `.chain2` physics files. Required by Phase 4B.
5. **[blender-mcp](https://github.com/example/blender-mcp)** — the TCP
   socket bridge ModPilot talks to. Enable, open the N-panel side
   tab, click **Connect to Claude** to bind port `9876`.

> The first four addons together expose the operator surface documented in
> [plugin_api.md](plugin_api.md). Without them ModPilot cannot run.

### Verify the install

From the repo root:

```powershell
uv run python verify_blender_mcp.py
```

5/5 must pass before continuing. Failure modes are explained inline in
the script.

---

## 2. Demo asset list (none bundled — fetch separately)

### MMD source model (default demo)

The MVP targets MMD models because they're the closest to MHWs proportions
out-of-the-box and the toolkit's `MMD` X-preset is well-calibrated.

Pick **one** public, redistributable MMD model. Avoid character models
with no-modify or no-distribute clauses (`改変禁止` / `配布禁止`).

Recommended candidates (download separately, respect each model's terms):

- **TDA Miku Append (公开许可版)** — canonical MMD A-pose, ~30 k tris,
  full PMX with bone hierarchy. Most predictable demo.
- **NICONI Common base** — Free-use VRoid-derived PMX, A-pose.
- Any other PMX that is in a clean A-pose and licensed for derivatives.

Place the model + textures under `~/.modpilot_assets/demo_mmd/`:

```
~/.modpilot_assets/demo_mmd/
├── miku.pmx
└── textures/
    ├── body_diffuse.png
    ├── body_normal.png
    └── ...
```

Open it in Blender via the **mmd_tools** addon (or any PMX importer) so
the result is a single `Armature` with mesh children. Save the scene
as `demo.blend` in the same folder.

### MHWs reference skeleton

`SetupImportMHWilds` calls `mbt.import_mhwilds_fmesh` which loads the
`MHWilds_Female` armature shipped inside the Modder-Batch-Tool addon.
You do **not** need to dump anything from the game for this step.

For Phase 6 BoneSystem export, however, you do need a real `.fbxskel.7`
file from the game's `natives/STM/Art/Character/Equipment/EquipParts/`
directory. The standard female-body skel is `ch03_000_9000.fbxskel.7`.
Use **[REasy Editor](https://github.com/seventoes/REasy)** or
**[RE Toolbox](https://github.com/example/RE-Toolbox)** to unpack the
game's `.pak` archives once and copy that one file out.

### Texture format

RE Engine textures are converted on the fly by `texconv` (bundled with
RE Mesh Editor). Provide source textures as PNG / TGA / DDS — the
generator handles channel layout + DX/GL normal direction.

---

## 3. Mod folder layout (`natives_root`)

Pick an empty folder somewhere with ~500 MB of free space. This becomes
`natives_root` in `verify_mvp_config.json`. The toolkit creates
`natives/STM/...` underneath it automatically:

```
D:/work/demo/mod_out/                 ← natives_root
└── natives/
    └── STM/
        └── Art/
            ├── Character/PlayerEquip/PL999/PL999_001/Common/ID/pl001/
            │   ├── pl001_f_body.mesh.241125222
            │   ├── pl001_f_body.mdf2.45
            │   └── pl001_f_body.chain2.51
            ├── Character/Equipment/EquipParts/
            │   └── ch03_000_9000.fbxskel.7
            └── <texture_base_path>/
                └── *.tex.*
```

`armor_id` defaults to `pl001` (a player-equip slot ID, see
`mhws_armor_sets.json` in the toolkit). The `pl001_*` filenames are
generated by the batch exporter from the armor scheme — they're listed
above so you know what to put in `expected_files` for the script.

---

## 4. Running verify_mvp.py

```powershell
# 1. Make sure Blender is open with demo.blend loaded and blender-mcp
#    "Connect to Claude" is active.
# 2. Copy the example config and fill in your absolute paths:
copy verify_mvp_config.example.json verify_mvp_config.json
# (edit verify_mvp_config.json — see field comments below)

# 3. From the ModPilot uv environment:
cd ModPilot
uv run python ../verify_mvp.py --config ../verify_mvp_config.json
```

A complete run takes 3-8 minutes depending on mesh complexity. Each
phase prints `[  OK ] Phase N: name -- 12.3s` on success. Exit code 0 =
green, non-zero = at least one check failed (the first failure is
printed at the end).

### Useful flags

- `--phases setup phase_1_2_3` — run only a prefix of the pipeline
  (useful when iterating on a specific phase).
- `--report verify_report.json` — write a structured JSON report
  including `state_diff` from each phase for postmortem analysis.
- `--host 127.0.0.1 --port 9876` — override Blender connection target.

### Filling in `verify_mvp_config.json`

Most fields are self-explanatory. The non-obvious ones:

| Field | What to put |
|---|---|
| `x_preset` | `"MMD"` for the demo above; `"VRChat"` / `"终末地"` if you bring your own |
| `source_armature` | The Blender object name — usually `"Armature"` (MMD default) or whatever you renamed it to |
| `mesh_objects` | All MESH children of the source armature, listed by Blender object name |
| `merged_mesh_object` | After Phase 3 joins everything into one mesh, this is its name (usually the first mesh from `mesh_objects`) |
| `texture_dir` | Folder containing all source PNG/TGA/DDS textures — non-recursive scan |
| `texture_mapping` | Only required for non-MMD sources. `{mat_name: {slot: filepath}}`, slot keys = Base Color / Alpha / Roughness / Metallic / Emission / Normal. MMD wires this automatically. |
| `preset_mapping` | `{mat_name: preset_display_name}` — preset names come from RE Mesh Editor's `Presets/MHWILDS/*.json` (e.g. `Hair`, `Skin`, `Character`, `cloth`) |
| `inferred_types` | `{chain_head_bone_name: physics_preset_key}` — keys from `ModPilot/app/data/physics_presets.json` (e.g. `hair_long_straight`, `cloth_skirt_waist`). Run an interactive agent session once to discover the right keys for your model, then paste them here. |
| `expected_files` | Relative paths under `natives_root` you expect to exist after Phase 6. The example file lists the canonical set for `armor_id=pl001`, `armor_variant=ff`, `target_parts=["2"]`. |

---

## 5. L3 acceptance — running the mod in-game

`verify_mvp.py` is the **L2** signal (toolkit operators report success +
output files exist with non-zero size). L3 acceptance per design
[A4](design.md#a4-mvp-验收用例) requires the mod to actually load and
render in Monster Hunter Wilds without crashing.

### Procedure

1. Confirm `verify_mvp.py` exits 0 — no point trying in-game if the
   files aren't even generated.
2. Locate your MHWs install (e.g. `D:/Games/MonsterHunterWilds/`).
3. **Backup the install first.** A malformed mesh / mdf2 can crash MHWs
   on character-screen entry. If you have a mod manager (Fluffy
   Manager, Vortex, etc.), use its install path; otherwise drop the
   contents of `verify_mvp_config.json:natives_root` into the game
   folder so its `natives/STM/...` tree merges into the game's.
4. Launch MHWs. Go to the hunter customization screen (your hunter, not
   create-character — `pl001` is an armor body slot).
5. Equip the body part the mod targets. If you used the example config
   `armor_id=pl001`, this is your hunter's basic outfit body slot.
6. Visual checklist:
   - [ ] Character loads without CTD or pink-error mesh.
   - [ ] Mesh roughly fits the skeleton (no exploded vertices, no
         spinning bones).
   - [ ] Materials render (no white-shader fallback or flat-magenta
         missing-texture state).
   - [ ] Physics bones move when the camera pans / hunter turns.
7. Take a screenshot. Note any visual artifacts in
   [docs/e2e_fixes.md](e2e_fixes.md) under a new dated entry.

### Failure modes (and what they mean)

| Symptom | Likely phase | Fix |
|---|---|---|
| CTD on character entry | Phase 6 file structure broken | Re-run with the exporter's debug logs; check `natives/STM/Art/...` path matches game expectation exactly |
| Mesh loads but is invisible | Phase 5 MDF2 generation | Verify preset_mapping matches Blender material names exactly; check natives/STM/Art/<texture_base_path>/*.tex files exist |
| Mesh visible but flat-shaded white | Material output socket disconnected | Phase 5B was skipped on a non-MMD source, or Principled BSDF Base Color is null |
| Hair / skirt is rigid | Phase 4B chain settings wrong | Re-run with `prepare_only=true` to clear marks, then refine inferred_types |
| Mesh deforms wrong | Phase 3 vertex_groups | The X preset doesn't match the source — re-run setup_infer_model_type to pick a better preset |
| Wrong body proportions | Phase 1 pose_correction | `skip_scale_align=true` was wrong, or the source model isn't at Z=0 origin |

If you hit any of these, capture the verify_mvp output + a Blender Info
log + an in-game screenshot, then file an issue against the project
(reference this section for symptom matching).

---

## 6. Beyond the demo

The same setup procedure works for any other MMD / VRChat / 终末地
source as long as:

- The source armature is in a single connected hierarchy under one
  ARMATURE object.
- All mesh objects are parented to that armature.
- A matching X-preset exists in Modding-Toolkit's
  `assets/presets/import/` — or you let the agent run
  `setup_infer_model_type` to either pick the closest one or write a
  `<character>_custom.json` from scratch (issues #4 / #5 / #6).

The `Armature` / mesh object names aren't sacred — the agent (and the
verify script) take them as parameters. Adapt your config accordingly.
