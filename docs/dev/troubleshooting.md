# Troubleshooting Reference

> Human-readable debugging manual. Referenced by `docs/agent/agent_workflow.md`.
> Not injected into the agent's system prompt.

---

## Setup Phase

### Common Errors

- **EMPTY objects present**: Source model importers often create EMPTY root or center objects.
  Ask user to delete them in the Outliner, then retry.
- **Multiple armatures**: Ask user to remove any test or leftover armature not part of
  the source model.
- **Import CANCELLED**: Modder-Batch-Tool addon is not installed or the hardcoded FBX file
  `games/MHWilds/model/MHWilds_Female.fbx` is missing from the addon directory.
  Report: "Install Modder-Batch-Tool and verify MHWilds_Female.fbx is present."

---

## Phase 1: Pose Correction

### Common Errors

- **Wrong skeleton preset selected**: Pose recorder cannot find matching bones → operator
  returns `CANCELLED`. Ask user to verify the skeleton preset matches their source model type.
- **Endfield pose recorder not applied**: If the source is Endfield and the model still appears
  in A-pose after step 4, check that the "forward" (not "inverse") direction was selected.

---

## Phase 2: Skeleton Alignment

### Common Errors

- **A few bones missing (< 5 ✗)**: Likely a preset compatibility edge case. Warn user:
  "A few bones could not be aligned — this may be a preset compatibility issue. Check
  which bones are missing and whether they are required for MHWs."
- **Many bones missing (> 10 ✗)**: Likely the wrong X preset was selected. Ask user to
  go back and verify the source preset matches their model's bone naming convention.
- **Wrong selection order**: If the source armature is the active (yellow) object instead of
  the MHWs armature, alignment direction is reversed. Redo with correct selection order.

---

## Phase 3: Vertex Groups

### Common Errors

- **Mesh selected instead of armature (reversed)**: If user accidentally runs the operator
  on the armature object rather than the mesh, vertex groups are unchanged. Check that a
  MESH object is selected.
- **Conflicting bone name gets `_old` suffix**: If a target name was already in use, the
  operator adds `_old` to the conflicting entry. Inform user and ask them to clean up
  the `_old` suffixed groups manually.

---

## Phase 3.5: Physics Bone Transplant

### Common Errors

- **_End bone position is far off**: Source bone length was abnormal. The `_End` bone
  head is placed at the source bone's tail (world coordinates), so unusual source bone
  lengths will misplace End bones. Inform user to check source bone proportions before
  transplant.
- **Parent relationships not rebuilt correctly**: Occurs when the source physics bone's
  parent chain is not mapped in the X→standard key→Y bridge. User may need to manually
  re-parent affected bones after transplant.
- **Wrong selection order**: If MHWs armature is not the active object, transplant
  direction is reversed. Redo with correct selection order.

---

## Phase 4A: Physics Bone Classification

### Background

The toolkit identifies physical bones by exclusion: any bone not listed in the X preset
(body bones + listed auxiliaries) is treated as a candidate physical bone. However, some
of these unlisted bones are actually unlisted auxiliary bones (twist helpers, roll helpers,
correction bones) that should be folded into the nearest body bone rather than treated as
physics. The agent's job is to classify these edge cases.

#### Shared Root Bones (`*_root` pattern)

Some physics setups attach multiple chains to a single intermediate `*_root` bone (e.g.
`hair_root`, `skirt_root`) rather than directly to a body bone. These root bones are **not**
chain members — they are connector pivots. (Some root bones carry weight data and must be
kept; only the user can know this.)

### Common Errors

- **`modder.merge_into_parent` fails**: The selected bone has no parent in the armature.
  Ask user to manually identify the correct parent body bone in Blender's Outliner.
- **Chain list is empty after classification**: All unlisted bones were classified as
  aux_merge. Warn: "No physical bones were identified. If the model has physics (hair/cloth),
  check that the X preset is correct and that physics bones have not been included in it."

### Out of Scope (MVP)

- Nested branch de-branching (merging sub-branches into main chain) — not handled in MVP.

---

## Phase 4B: Physics File Creation

### Common Errors

- **`create_chain_header` creates chain1 instead of chain2**: `chainFileType` was not
  set before calling. Always set it explicitly first.
- **`create_chain_settings` poll fails**: `chainCollection` not set in the tool panel.
  Set `context.scene.re_chain_toolpanel.chainCollection` first.
- **`apply_angle_limit_ramp` poll fails**: Active object is not a chain group type.
  Select a `RE_CHAIN_CHAINGROUP` or `RE_CHAIN_SUBGROUP` object first.
- **`apply_chain_settings_preset` applies wrong preset**: `chainSettingsPresets` enum was
  not set before calling, or the name does not exactly match a file in the preset directory.
  Verify the preset name matches the `.json` filename (without extension).

---

## Phase 5: Material Processing

### Common Errors

- **texconv not found**: RE Mesh Editor should bundle texconv; ask user to verify
  RE Mesh Editor is correctly installed.
- **Roughness appears inverted in-game**: Source uses smoothness (inverted roughness).
  Enable the roughness invert option in the MDF2 Generator per-material settings.
- **Normal map lighting wrong**: Source uses GL-format normals. Enable GL→DX normal
  flip in the MDF2 Generator per-material settings.
- **`list_mdf_presets` returns error**: RE Mesh Editor addon is not loaded in Blender.
  Verify the addon is enabled and restart Blender if needed.

---

## Phase 6: Batch Export

### Common Errors

- **RE Mesh Editor not installed**: Export operators unavailable. Report:
  "RE Mesh Editor is required for MHWs export. Please install it."
- **Unassigned slot causes export error**: Always assign empty model to unused slots.
- **BoneSystem FBXSkel name mismatch**: Name must exactly match the definition used
  during rig setup. Verify with user before re-running.
- **Baked textures missing from Natives path**: Phase 5 bake may have written to a
  different directory. Re-check mod root path against Natives root.
