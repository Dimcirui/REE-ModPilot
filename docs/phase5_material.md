# Phase 5 — Material Setup & MDF2 Generation

> Implementation reference for `app/phases/material.py`.
> Three tools: `MaterialInspect` → `MaterialSetup` → `MaterialGenerate`.

---

## Overview

Phase 5 bridges source-model textures to RE Engine MDF2 materials.
The Modding Toolkit's MDF generator (`mhws.mdf_gen_process`) handles all
RE Engine–specific conversion (channel packing, texconv, preset baking);
Phase 5 only needs to wire textures into Principled BSDF nodes and
configure the generator.

**MMD models skip MaterialSetup entirely** — their textures are already
loaded and connected by the MMD importer. They enter Phase 5 at
MaterialGenerate directly.

Pipeline:

```
MaterialInspect  →  (agent loop: LLM classify + user confirm)  →  MaterialSetup
                                                                        ↓
                →  (agent loop: LLM preset classify + user confirm)  →  MaterialGenerate
```

---

## Principled BSDF Target Slots

| Slot | VRChat wiring | 終末地 wiring | x_preset dependent |
|------|--------------|--------------|-------------------|
| Base Color | ImageTexture → socket | ImageTexture → socket | No |
| Alpha | ImageTexture → socket | ImageTexture → socket | No |
| Roughness | ImageTexture → socket | ImageTexture → socket | No |
| Metallic | ImageTexture → socket | ImageTexture → socket | No |
| Emission | ImageTexture → socket | ImageTexture → socket | No |
| Normal | ImageTexture → SepXYZ → Math(1−Y) → CombXYZ → NormalMap → socket | ImageTexture → NormalMap → socket | **Yes** |

VRChat normal maps are OpenGL (+Y); 終末地 normal maps are DirectX (+Y already
correct for RE Engine). The Y-inversion chain is only inserted for VRChat.

---

## Tool 1 — `MaterialInspect`

**Role**: Read-only. Returns raw data for agent-loop classification.
No LLM calls inside this tool.

### Parameters

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `target_object` | str | yes | Mesh object name in Blender scene |
| `texture_dir` | str | yes | Absolute path to directory containing source textures |

### Steps

1. **Python-side filesystem scan** (`Path.glob`) — collect image files
   (`.png`, `.tga`, `.dds`, `.jpg`, `.tif`, `.bmp`) from `texture_dir`.
   Runs on the FastAPI server; does not go through Blender.

2. **Blender query — material names** — retrieve `[mat.name for mat in
   obj.data.materials]` from `target_object`.

3. **Blender query — existing node connections** — for each material, find
   the Principled BSDF node and check all 6 slots:
   - Direct ImageTexture → record `image.filepath`
   - Normal slot: trace through NormalMap node, and further through
     CombineXYZ chain if present, to reach the ImageTexture
   - Connected but image not resolved → `"connected_no_image"`
   - Not connected → `null`

### Output (`state_diff`)

```json
{
  "materials": ["Body", "Hair", "Face"],
  "texture_files": ["body_d.png", "body_n.tga", "hair_diff.png"],
  "existing_connections": {
    "Body": {
      "Base Color": "C:/tex/body_d.png",
      "Alpha": null,
      "Roughness": null,
      "Metallic": null,
      "Emission": null,
      "Normal": null
    },
    "Hair": { ... }
  }
}
```

After receiving this output, the agent loop:
1. Uses LLM to group `texture_files` by material (matching file names to
   material names).
2. Within each group, uses LLM to assign files to slots by filename
   heuristics (e.g. `*_d.*` → Base Color, `*_n.*` → Normal).
3. For low-confidence assignments, calls the vision model on the file.
4. For remaining ambiguous items, enters ASK_MODE for user confirmation.
5. Calls `MaterialSetup` with the confirmed `texture_mapping`.

---

## Tool 2 — `MaterialSetup`

**Role**: Execute node wiring given a confirmed texture mapping.

### Parameters

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `target_object` | str | yes | |
| `x_preset` | str | yes | `"VRChat"` or `"終末地"` |
| `texture_mapping` | dict | yes | `{mat_name: {slot: filepath_or_null}}` |

`texture_mapping` slot keys: `"Base Color"`, `"Alpha"`, `"Roughness"`,
`"Metallic"`, `"Emission"`, `"Normal"`. A `null` value means: leave that
slot untouched (preserve any existing connection).

### Steps

1. Validate: `target_object` exists in scene; `texture_mapping` non-empty;
   `x_preset` is `"VRChat"` or `"終末地"`.

2. For each material in `texture_mapping`:
   a. Locate Principled BSDF node (create one if absent).
   b. For each of the 5 non-Normal slots with a non-null filepath:
      create `ShaderNodeTexImage`, load image, connect to socket.
   c. For the Normal slot with a non-null filepath:
      - **VRChat**: `ImageTexture → SeparateXYZ → Math(SUBTRACT, 1−Y)
        → CombineXYZ → NormalMap → Normal`
      - **終末地**: `ImageTexture → NormalMap → Normal`
   d. Auto-position nodes with fixed x-step offsets to avoid overlap.

3. Return `{materials_wired: [...], slots_skipped: [...]}`.

### Node type reference

| Purpose | Blender node type |
|---------|------------------|
| Image texture | `ShaderNodeTexImage` |
| Separate XYZ | `ShaderNodeSeparateXYZ` |
| Combine XYZ | `ShaderNodeCombineXYZ` |
| Math | `ShaderNodeMath` (operation=`SUBTRACT`, inputs[0]=1.0) |
| Normal map | `ShaderNodeNormalMap` |
| Principled BSDF | `ShaderNodeBsdfPrincipled` |

---

## Tool 3 — `MaterialGenerate`

**Role**: Configure and run the MDF2 generator. Accepts a confirmed
per-material preset mapping from the agent loop.

### Parameters

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `mesh_collection` | str | yes | Blender collection containing mesh objects |
| `texture_base_path` | str | yes | Sub-path under `natives/STM/Art/` (e.g. `"Author/CharName/"`) |
| `preset_mapping` | dict | yes | `{mat_name: preset_display_name}` (e.g. `{"Hair": "Hair"}`) |
| `mdf_collection_name` | str | no | Output MDF collection name; generator auto-derives if omitted |
| `natives_root` | str | no | Overrides `scene["mhws_natives_root"]`; reads existing scene value if omitted |

`preset_display_name` is the filename without `.json` from
`Presets/MHWILDS/` (e.g. `"Character"`, `"Skin"`, `"Hair"`, `"body"`).

### Steps

1. Validate: `mesh_collection` exists; `texture_base_path` non-empty.

2. Set scene properties:
   ```python
   if natives_root:
       scene["mhws_natives_root"] = natives_root
   s.mesh_collection      = bpy.data.collections[mesh_collection]
   s.texture_base_path    = texture_base_path
   s.mdf_collection_name  = mdf_collection_name  # if provided
   ```

3. Call `bpy.ops.mhws.mdf_gen_refresh()` — populates `s.material_list`
   with auto-detected node strategies and guessed presets.

4. Call `load_preset_enum_items("MHWILDS")` — returns a sequence of
   `(full_path, display_name, ...)` tuples. Build lookup:
   `{display_name: full_path}`.
   > **Implementation note**: the exact import path for
   > `load_preset_enum_items` must be confirmed at implementation time
   > by inspecting the RE Mesh Editor addon source. Use a Blender
   > introspection snippet rather than hardcoding the module path.

5. For each `entry` in `s.material_list`:
   - Look up `preset_mapping.get(entry.material.name)`.
   - If found and present in lookup table → `entry.material_preset = full_path`.
   - If not found → keep refresh's auto-guess; record in `presets_auto_guessed`.

6. Call `bpy.ops.mhws.mdf_gen_process()`.

7. Return `mdf_collection_name` for use as `mdf2_collection` in Phase 6.

### Output (`state_diff`)

```json
{
  "mdf_collection": "MyMeshCol.mdf2",
  "materials_processed": ["Body", "Hair", "Face"],
  "presets_auto_guessed": ["Face"]
}
```

`presets_auto_guessed` entries were not in `preset_mapping`; agent reports
them to the user so they can verify the auto-guessed result is acceptable.

---

## Path layout (reminder)

```
{natives_root}/
├── natives/STM/Art/Model/Character/.../ch02_001_0001.mesh.241111606
│     ↑ batch_export writes here (from armor JSON base_path)
└── natives/STM/Art/{texture_base_path}/pl001_1_ALBD.tex.21
      ↑ mdf_gen_process writes here (from texture_base_path)
```

`natives_root` is shared between Phase 5B and Phase 6.
`texture_base_path` is independent of armor JSON's `base_path`.

---

## Agent Loop Classification Tasks (between tools)

### After MaterialInspect — texture grouping + slot assignment

The agent loop receives `materials` + `texture_files` + `existing_connections`
and must produce `texture_mapping` for MaterialSetup.

LLM prompt context should include:
- List of material names
- List of texture filenames (basename only, not full path)
- Known slot keywords: `_d / _diff / _diffuse / _albedo` → Base Color;
  `_n / _nrm / _normal` → Normal; `_r / _rough / _roughness` → Roughness;
  `_m / _metal / _metallic` → Metallic; `_e / _emit / _emissive` → Emission;
  `_a / _alpha / _opacity` → Alpha
- Instruction to group by material name first, then assign within group

Vision model fallback: called per-file when filename confidence is low.
Typical visual cues: uniform grayscale → Roughness/Metallic; purple-blue
tint → Normal (though not needed for slot identification after the node
wiring change); full colour → Base Color.

**Case B** (material name has no correspondence to texture filenames):
flag as `ungrouped_textures` in the proposed mapping and enter ASK_MODE
for user to manually assign.

### After MaterialGenerate (refresh) — preset selection

The agent loop receives `s.material_list` entries with auto-guessed presets
and must validate/correct them using LLM.

Common mappings:
- Material name contains "hair" / "fur" → `Hair` or `NoPDO Hair`
- Contains "skin" / "body" / "flesh" → `Skin`
- Contains "eye" / "iris" → `Eye` or `iris`
- Contains "cloth" / "fabric" / "dress" → `cloth`
- Generic character parts → `Character`

---

## Testing Plan

| Class | Coverage |
|-------|---------|
| `TestMaterialInspect` | Missing params; object not found (PRECONDITION); texture_dir not found; normal output shape |
| `TestMaterialSetup` | Missing params; invalid x_preset; VRChat Normal chain contains `SUBTRACT`; 終末地 Normal chain does not; null slot skipped |
| `TestMaterialGenerate` | Missing params; collection not found; preset matched to full path; unmatched preset recorded in `presets_auto_guessed`; process FINISHED; process CANCELLED |
