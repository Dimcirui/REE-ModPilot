# REE-ModPilot Agent Workflow

> **Purpose**: Execution instructions for the ModPilot agent. This is NOT a tutorial —
> it is a machine-readable operating manual. Follow it literally.
>
> **Scope**: MHWs single-game pipeline, MMD-priority source model, videos 1-7 full flow.

---

## Global Behavior Rules

### Explanation Policy
- Do NOT proactively explain background theory (why pose comes before skeleton, etc.).
- Explanations are triggered ONLY by errors or explicit user questions ("why?", "what is X?").
- When explaining, be brief and specific to the current context.

### Confidence-Based Routing
- **High confidence** (clear match, unambiguous signal): proceed automatically, inform user after.
- **Low confidence** (ambiguous naming, multiple candidates, unusual structure): pause, present
  candidates via `propose_and_confirm`, wait for user input before executing.
- Threshold guidance per phase is specified in each phase's "Classification" section.

### Propose-and-Confirm Protocol
Used whenever the agent presents a structured proposal for user review:
1. Generate a proposal table with a `confidence` column (`high` / `mid` / `low`).
2. Auto-accept `high` confidence rows; highlight `low` confidence rows for user attention.
3. User corrects only the highlighted rows; all others proceed as proposed.
4. After user confirms, execute the full accepted proposal.

Base output schema:
```json
{
  "proposals": [
    { "item": "<name>", "proposed_value": "<value>", "reason": "<one-line>", "confidence": "high|mid|low" }
  ],
  "requires_user_review": true
}
```

### Error Response Protocol
On `PhaseError`, always:
1. State what failed in plain language (one sentence).
2. State what the user should check or fix (one sentence).
3. Present three options: **[Retry]** / **[Skip]** / **[Ask]**.
- "Skip" carries a visible warning about downstream impact.
- "Ask" enters free Q&A mode; do NOT call tools in this mode — explain only.

### Output Format Constraints
- Classification outputs must be valid JSON matching the schema defined in each phase.
- Never output free-form text where a structured schema is required.
- Wrap structured outputs in a fenced code block tagged `json`.

---

## Phase Sequence

```
[Setup Block]
  Setup 1 (Validate Scene) → [user confirm] → Setup 2 (Import MHWilds Armature)
        ↓
[1-3 Preprocessing Block]
  Phase 1 → Phase 2 → Phase 3
        ↓
[3.5 Physics Bone Transplant]          — automatic (no LLM)
  transplant source physics bones → MHWs armature
  hide source armature; switch X preset to MHWs
        ↓
[4A Physics Bone Classification]       — NEGOTIATING loop
  (operating on MHWs armature)
        ↓  (physics chain list)
[4B Physics File Creation]             — NEGOTIATING loop
        ↓
[5  Material Processing]               — NEGOTIATING loop (non-MMD only)
        ↓
[6  Batch Export]
```

Setup and Phases 1-3 run as RUNNING_PHASE (LLM calls tools directly).
Phase 3.5 is fully automatic.
Phases 4A-6 each contain one or more NEGOTIATING loops.

---

## Setup Phase

**Goal**: Validate source model scene state, then import the MHWilds Female reference skeleton.

### Central Collection

`MHWilds_Female.mesh` is the **central target collection** for ALL downstream phases:
- Phase 3 (VertexGroups) reparents source meshes into this collection under the MHWilds armature.
- Phase 5 (Material) operates on meshes within this collection.
- Phase 6 (BatchExport) exports the contents of this collection.

**Do NOT delete, rename, or move this collection at any point in the pipeline.**

### Step 1: Validate Scene (`setup_validate_scene`)

Call this tool with no parameters on the first user message. No parameters needed.

The tool checks (after excluding objects inside `MHWilds_Female.mesh` if present):
- Exactly **1 ARMATURE** object exists.
- All **MESH** objects are direct children of that armature.
- No other object types (EMPTY, LIGHT, CAMERA, etc.) exist in the scene.

**On failure**: Report the specific errors to the user; ask them to fix the scene and
say "ready" to re-validate. Do NOT proceed to Step 2 until validation passes.

**On success**: Report scene state and ask for confirmation before importing:
> "Found source model: armature **[name]**, **[N]** mesh(es): [list].
> Shall I now import the MHWilds Female reference skeleton? [Yes / Not yet]"

**Do NOT call `setup_import_mhwilds` in the same response.** Wait for the user's reply.

### Step 2: Import MHWilds Armature (`setup_import_mhwilds`)

Call only after the user confirms Step 1.

- If `MHWilds_Female.mesh` already exists: operator is skipped; report as already done.
- Otherwise: switches Blender to Object mode if needed, then runs `mbt.import_mhwilds_fmesh`.

Default parameters (do not change unless user requests):
- `convert_to_tpose: true`
- `merge_facial_bones: true`

On success, report:
> "MHWilds Female armature imported (`MHWilds_Female.mesh` collection created).
> Proceeding to Phase 1."

### Common Errors

- **EMPTY objects present**: Source model importers often create EMPTY root or center objects.
  Ask user to delete them in the Outliner, then retry.
- **Multiple armatures**: Ask user to remove any test or leftover armature not part of
  the source model.
- **Import CANCELLED**: Modder-Batch-Tool addon is not installed or the hardcoded FBX file
  `games/MHWilds/model/MHWilds_Female.fbx` is missing from the addon directory.
  Report: "Install Modder-Batch-Tool and verify MHWilds_Female.fbx is present."

---

## Phase 1–3: Preprocessing Block

### Entry Conditions
- [ ] Setup phase complete: source model validated, `MHWilds_Female.mesh` collection present.
- [ ] X preset selected (source model type: MMD / VRChat / Endfield / other).
- [ ] Y preset selected (target game: MHWs).

> **Fixed names — do NOT ask the user:**
> - `target_armature` for all phases = `"MHWilds_Female Armature"` (always, after setup)
> - `y_preset` = `"怪猎荒野"` (always for MHWs MVP)

### Preset Paths
| Source Type | X Preset File | Skeleton Preset |
|---|---|---|
| MMD | `..\Modding-Toolkit\assets\presets\import\MMD.json` | MMD |
| VRChat | `..\Modding-Toolkit\assets\presets\import\VRChat.json` | VRChat |
| Endfield | `..\Modding-Toolkit\assets\presets\import\终末地.json` | Endfield |

Y preset is always: MHWs (Monster Hunter Wilds).

### VRChat Base Body Identification

VRChat models are built on community avatar bases. If the source armature name or any
mesh name contains one of the following keywords (case-insensitive), treat the model
as VRChat with **high confidence** — do not ask the user:

```
kipfel, shinano, manuka, milltina, rurune, mamehinata, shinra, chocolat, selestia,
kikyo, minase, sio, milfy, rinasciita, komano, mafuyu, eku, chiffon, karin, lumina,
marycia, mao, moe, lasyusha, rusk, ichigo, maya, mizuki, hakka, airi, zome, lapwing,
deltaflair, lime, kanata, rindo, sophina, platinum, nemesis, sapphy, wolferia, ririka,
mishe, kokoa, fiona, mint, lazuli, soraha, minahoshi, koyuki, cian, meiyun, merino,
velle, anon, ciel, sephira, lucifer
```

If none of these keywords appear, fall back to asking the user to confirm the source type.

---

### Phase 1: Pose Correction

**Goal**: Align source model's pose to approximately match MHWs reference armature pose,
then scale the model to roughly match MHWs body proportions via bounding box.

#### Classification Decision Point: Pose Tool Selection

| Source Type | Tool to Use | Notes |
|---|---|---|
| MMD | Pose Convert → Direction Calculation | Rotates upper arms to horizontal (A-pose → T-pose approximation) |
| VRChat | No pose tool needed | VRC models are typically already in T-pose |
| Endfield | Pose Convert → Pose Recorder → Endfield A-to-T (forward) | Uses pre-recorded delta to convert Endfield's A-pose |

Confidence: **high** for all three cases — source type is known from X preset selection.
Proceed automatically without asking the user.

#### Execution Steps
1. Set X preset (source) and skeleton preset to match source model type.
2. Set Y preset (target) to MHWs.
3. Scale the armature + meshes to roughly match MHWs bounding box (bbox align).
4. Apply the pose correction tool selected above.

#### User Interaction Points
1. **After step 3 (bbox scale)**: Display viewport screenshot. Ask:
   "The model has been scaled to match the MHWs reference proportions. Does the scale look acceptable? [Yes / Adjust]"
2. **After step 4 (pose correction)**: Display viewport screenshot. Ask:
   "Pose correction applied. Does the pose roughly match the T-pose of the reference armature? [Yes / No — describe the issue]"

#### Exit Conditions
User confirms that the model's scale and pose are approximately aligned with the MHWs
reference armature. Exact precision is not required here; close enough for skeleton alignment
in Phase 2 is sufficient.

#### Common Errors
- **Wrong skeleton preset selected**: Pose recorder cannot find matching bones → operator
  returns `CANCELLED`. Ask user to verify the skeleton preset matches their source model type.
- **Endfield pose recorder not applied**: If the source is Endfield and the model still appears
  in A-pose after step 4, check that the "forward" (not "inverse") direction was selected.

---

### Phase 2: Skeleton Alignment

**Goal**: Move MHWs reference armature bones to align with source model's skeleton positions.

#### Classification Decision Point
Always use **Align Bones [X+Y, dual armature]** from the Universal Standard Conversion panel.
Do not use generic position/position+twist/full alignment tools unless the X+Y operator is
unavailable.

spine_03 handling is automatic: if the source preset lacks spine_03, the operator silently
skips it. No detection needed.

#### Execution Steps
1. In Object Mode, select the **source armature** first.
2. Ctrl+click to also select the **MHWs armature** (it must be the active/yellow object).
3. Run: Align Bones [X+Y, dual armature].

#### User Interaction Point
- After execution: Report the mapping preview result (count of ✓ matched / ✗ missing bones).
  Ask: "Skeleton alignment complete. Do the results look correct? [Yes / There is an issue]"

#### Exit Conditions
All body bones in the standard set show ✓ in the mapping preview.
A small number of ✗ (typically only optional bones like spine_03 or game-specific extras) is acceptable.

#### Common Errors
- **A few bones missing (< 5 ✗)**: Likely a preset compatibility edge case. Warn user:
  "A few bones could not be aligned — this may be a preset compatibility issue. Check
  which bones are missing and whether they are required for MHWs."
- **Many bones missing (> 10 ✗)**: Likely the wrong X preset was selected. Ask user to
  go back and verify the source preset matches their model's bone naming convention.
- **Wrong selection order**: If the source armature is the active (yellow) object instead of
  the MHWs armature, alignment direction is reversed. Redo with correct selection order.

---

### Phase 3: Vertex Groups

**Goal**: Rename source mesh vertex groups to MHWs bone naming convention, completing
the skinning weight migration.

#### Classification Decision Point
Fully deterministic. No LLM classification required. X and Y presets already loaded
provide all necessary mapping information.

#### Execution Steps
1. Inspect all meshes in the source model. For any mesh that has no material assigned,
   create a default placeholder material (required for later export steps).
2. Merge all source meshes into a single mesh object.
3. For MMD source models only: delete the `mmd_edge_scale` and `mmd_vertex_order` vertex
   groups from the merged mesh (these are MMD-specific groups that must not be carried over).
4. Normalize all vertex group weights on the mesh, then remove empty vertex groups.
5. With the merged mesh selected (MESH object, not ARMATURE), run:
   Rename Vertex Groups [X+Y].

#### User Interaction Point
- After execution: Report how many vertex groups were renamed successfully and whether
  any auxiliary bone weights were merged. Ask:
  "Vertex group renaming complete. Please check the mapping preview to confirm all
  required bones show ✓. [Looks good / There is an issue]"

#### Exit Conditions
All required MHWs body bone vertex groups are present in the merged mesh.
Mapping preview shows ✓ for all standard bones.

#### Common Errors
- **Mesh selected instead of armature (reversed)**: If user accidentally runs the operator
  on the armature object rather than the mesh, vertex groups are unchanged. Check that a
  MESH object is selected.
- **Conflicting bone name gets `_old` suffix**: If a target name was already in use, the
  operator adds `_old` to the conflicting entry. Inform user and ask them to clean up
  the `_old` suffixed groups manually.

---

### Preprocessing Block Exit
After Phase 3 user confirms the mapping preview: output a one-paragraph summary of what
was completed across phases 1-3, then automatically proceed to Phase 3.5.
Do NOT ask for an additional confirmation at this point.

---

## Phase 3.5: Physics Bone Transplant

**Goal**: Copy all physical bones (and unlisted auxiliary bones) from the source armature
into the MHWs armature. After this phase the source armature is no longer needed.

### Entry Conditions
- [ ] Phases 1-3 completed successfully.
- [ ] Both source armature and MHWs armature are visible in the scene.
- [ ] X preset is still set to the source model type (MMD / VRChat / Endfield).

### Classification Decision Point
None. The transplant operator determines which bones to copy automatically:
any bone in the source armature that is NOT listed in the current X preset is treated
as a physical bone and transplanted. No LLM involvement.

### Execution Steps
1. In Object Mode, select the **source armature** first.
2. Ctrl+click to also select the **MHWs armature** (it must be the active/yellow object).
3. Run: Transplant Physics Bones [X+Y, dual armature].
   - The operator internally: filters unlisted bones → copies world coordinates to MHWs
     armature → auto-generates `_End` terminal bones at leaf positions → resets all
     transplanted bones to vertical (Z+) → rebuilds parent relationships → copies
     `chain_role` custom properties from source to target bones. 
4. Hide the source armature (it is no longer needed).
5. Switch X preset from source type to **MHWs**.
6. Run: Refresh Bone Colors on MHWs armature (visualizes chain_role assignments for user verification).

### User Interaction Point
- After step 6: Display viewport screenshot showing the MHWs armature with color-coded
  physics bones. Inform the user:
  "Physics bones have been transplanted. Colors indicate chain roles:
  sky-blue = chain head, purple = branch head, amber = main-chain continuation, dark-blue = body/end.
  Does the result look correct? [Yes / There is an issue]"

### Exit Conditions
- Physics bones are visible in the MHWs armature with chain_role colors applied.
- Source armature is hidden.
- X preset is set to MHWs.

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

**Goal**: Distinguish physical bones from unlisted auxiliary bones within the MHWs armature;
merge aux bones into parent body bones; produce a physics chain list for Phase 4B.

### Entry Conditions
- [ ] Phase 3.5 completed: physics bones are in the MHWs armature, source armature hidden.
- [ ] X preset is now set to MHWs.
- [ ] Y preset is MHWs.

### Background
The toolkit identifies physical bones by exclusion: any bone not listed in the X preset
(body bones + listed auxiliaries) is treated as a candidate physical bone. However, some
of these unlisted bones are actually unlisted auxiliary bones (twist helpers, roll helpers,
correction bones) that should be folded into the nearest body bone rather than treated as
physics. The agent's job is to classify these edge cases.

### Classification Heuristics

#### Physical Bone Indicators (treat as physical)
- Name contains: `hair`, `skirt`, `dress`, `cloth`, `tail`, `phys`, `dyn`, `spring`,
  `ribbon`, `cape`, `sleeve`, `fringe`, `bang`, `braid`, `ponytail`
- Forms a chain of 2+ bones in parent-child sequence (typical of physics chains)
- Has `chain_role` custom property set on it (Modding-Toolkit marker)
- Is a leaf bone (no children) at the end of a chain that started with a physical-named root

#### Unlisted Auxiliary Bone Indicators (treat as aux_merge)
- Name contains: `twist`, `roll`, `adj`, `helper`, `correct`, `fix`, `ik`, `pole`,
  `target`, `ctrl`, `control`, `weapon`, `camera`, `cam`, `root` (if single root connector)
- Is a single bone with no children that directly parents to a known body bone
- Does not form a multi-bone chain

#### Confidence Rules
- **High confidence → auto**: Name clearly matches physical indicators OR clearly matches
  aux indicators with no ambiguity. Classify automatically and report summary.
- **Low confidence → propose_and_confirm**: Name is generic (e.g. `bone_01`, `extra_L`),
  or is a single bone that could be either a root of a physics chain or a loose helper.
  Surface to user with reason and candidate role.

### Propose-and-Confirm: Physics Bone Proposal
```json
{
  "proposals": [
    {
      "bone_name": "<name>",
      "proposed_role": "physical | aux_merge | keep_body",
      "merge_target": "<nearest body bone name, if aux_merge>",
      "reason": "<one-line>",
      "confidence": "high|mid|low"
    }
  ],
  "requires_user_review": true
}
```
Only surface `low` confidence entries for user review. Auto-accept `high` and `mid`.

### Execution Steps (after user confirms proposal)
1. For each bone classified as `aux_merge`: run Merge to Parent Bone, targeting the
   specified `merge_target` body bone.
2. Verify the remaining unlisted bones are all classified as `physical`.
3. Build the physics chain list (see output schema below).

### Output: Physics Chain List
Passed directly to Phase 4B as structured data. Conversation history is NOT carried over.
```json
{
  "chains": [
    {
      "chain_id": "<short identifier, e.g. hair_L>",
      "bones": ["<root bone>", "...", "<tip bone>"],
      "inferred_type": "<key from physics_presets.json, e.g. light_hair>",
      "chain_role": "main | branch"
    }
  ]
}
```

### Exit Conditions
- All unlisted auxiliary bones have been merged into body bones.
- All remaining unlisted bones form valid physical chains.
- Physics chain list is built and non-empty.

### Common Errors
- **Merge to Parent Bone fails**: Target body bone not found in armature. Ask user to
  manually identify the correct parent.
- **Chain list is empty after classification**: All unlisted bones were classified as
  aux_merge. Warn: "No physical bones were identified. If the model has physics (hair/cloth),
  check that the X preset is correct and that physics bones have not been included in it."

### Out of Scope (MVP)
- Nested branch de-branching (merging sub-branches into main chain) — not handled in MVP.

---

## Phase 4B: Physics File Creation

**Goal**: Create a chain2 collection, build the chain hierarchy, apply angle limit ramps,
group chains into Settings by physics type, and apply preset parameters to each group.

### Entry Conditions
- [ ] Phase 4A physics chain list received (structured data, no conversation history carried over).
- [ ] physics_presets.json loaded (available in system prompt).
- [ ] RE Chain Editor plugin installed.

### Chain Hierarchy Reference (corrected)
```
header       — top-level container; auto-created with the collection; no parameter edits needed
  └─ settings  — one Settings block per physics type (e.g. one for hair, one for cloth);
                 holds the physics simulation parameters for that type group
       └─ group  — one group per individual physical chain; references the chain's bones;
                   inherits parameters from its parent settings
```

### Preset Discovery
At Phase 4B entry, discover available presets by scanning:
`<RE-Chain-Editor>/Presets/ChainSettings/`
Use only presets with names starting with `MHWilds` as the authoritative reference set.
Other presets in the directory are user-added and less reliable.

### Settings Grouping Logic
Group chains from the physics chain list by `inferred_type`. Each distinct type gets its
own Settings block. Chains with the same type share a Settings.

Example: 5 chains with types `[long_hair, long_hair, hanging_cloth, long_hair, ribbon]`
→ 3 distinct types → 3 Settings blocks needed.

`inferred_type` → RE Chain preset name mapping is stored in `physics_presets.json`.
If a chain's `inferred_type` has no mapping, default to `"MHWilds Long Straight Hair"`
and flag as low confidence for user review.

### Propose-and-Confirm: Settings Grouping Proposal
Present BEFORE any execution. User confirms grouping and preset assignment.
```json
{
  "proposed_settings": [
    {
      "settings_id": "settings_1",
      "re_chain_preset_name": "<MHWilds preset filename without .json>",
      "chains": ["<chain_id>", "..."],
      "reason": "<one-line>",
      "confidence": "high|mid|low"
    }
  ],
  "requires_user_review": true
}
```
Only surface `low` confidence entries (ambiguous `inferred_type`) for user review.

### Execution Steps (after user confirms grouping proposal)

**Step 1 — Create chain2 collection and header:**
```python
context.scene.re_chain_toolpanel.chainFileType = "chain2"
bpy.ops.re_chain.create_chain_header(collectionName="myChain", chainFormat=".chain2")
```
`chainFileType` MUST be set before the operator call; otherwise it defaults to chain1.

**Step 2 — Create initial Settings with all chain groups (shared mode):**
Run Modding-Toolkit's one-click Create Chain2 operator in "shared same settings" mode.
This creates ONE Settings block containing ALL chain groups (one group per physical chain).

**Step 3 — Apply angle limit ramp to all groups:**
For each group object in the collection, select it as active and run:
```python
bpy.ops.re_chain.apply_angle_limit_ramp(maxAngleLimit=1.047198, maxIteration=4)
```
`maxAngleLimit=1.047198` ≈ 60°, `maxIteration=4`.
Poll condition: active object type must be `RE_CHAIN_CHAINGROUP` or `RE_CHAIN_SUBGROUP`.

**Step 4 — Create additional Settings blocks:**
Additional Settings to create = (total distinct types − 1), since Step 2 already made one.
```python
bpy.ops.re_chain.create_chain_settings()
```
Poll condition: `context.scene.re_chain_toolpanel.chainCollection` must be set.

**Step 5 — Apply RE Chain preset to each Settings block:**
For each Settings block, select it as active and apply the matched preset:
```python
context.scene.re_chain_toolpanel.chainSettingsPresets = "<preset name>"
bpy.ops.re_chain.apply_chain_settings_preset()
```
The `chainSettingsPresets` EnumProperty is populated at runtime by scanning the
`Presets/ChainSettings/` directory. Set the value before calling apply.

**Step 6 — Reassign groups to their correct Settings:**
Move each chain group into its designated Settings block per the confirmed proposal.
(All groups start in the one Settings created by Step 2; reorganize from there.)

### Key Parameter Reference (for natural language adjustments)
These are the parameters that actually vary across MHWilds presets.
All other parameters are fixed defaults — do NOT change them.

| Parameter | What it controls | Low value | High value |
|---|---|---|---|
| `damping` / `minDamping` | Motion resistance (always keep equal) | Fluid/loose | Slow/sticky |
| `reduceSelfDistanceRate` | Chain link rigidity | Stretchy | Rigid |
| `gravity` | Gravitational pull direction + strength | `[0,0,0]` = none | `[0,-9.8,0]` = full |
| `springForce` | Pull-back-to-rest-pose force | 0 = no spring | >0 = spring return |
| `shockAbsorptionRate` | Dampens sudden impact forces | 0 = no absorption | ~0.28 = absorbing |
| `windEffectCoef` | Character-wind responsiveness | 0 = no wind | ~0.24 = responsive |
| `envWindEffectCoef` | Environment-wind responsiveness | 0 = no wind | ~0.27 = responsive |
| `motionForce` | Extra force from character motion | 0 = none | 15-20 = strong |

`settingsAttrFlags` bitmask (do not change unless user requests):
- `1` = Default, `2` = Virtual Ground Root, `4` = Virtual Ground Target,
  `8` = Ignore Same Group Collision, `16` = Use Reduce Distance Curve

`colliderFilterInfoPath`: For MHWilds, use `System/Collision/Filter/Character/Character_Chain.cfil`
as default. Empty string = no collision interaction.

### Natural Language Adjustment Mapping
After a preset is applied, the user may request fine-tuning. Translate as follows:
| User phrase | Parameters to change |
|---|---|
| "stiffer" / "less floppy" | Increase `damping`+`minDamping`; decrease `reduceSelfDistanceRate` |
| "softer" / "more floppy" | Decrease `damping`+`minDamping`; increase `reduceSelfDistanceRate` |
| "add gravity" / "droops too little" | Set `gravity: [0, -9.8, 0]` |
| "remove gravity" / "floats" | Set `gravity: [0, 0, 0]` |
| "more wind responsive" | Increase `windEffectCoef` and `envWindEffectCoef` |
| "no wind effect" | Set both wind coefficients to 0 |
| "spring back to pose" | Increase `springForce` |
| "absorb shocks" | Increase `shockAbsorptionRate` |
| "reacts more to movement" | Increase `motionForce` |
| "try a different preset" | Switch to another MHWilds preset and re-apply |

### Exit Conditions
- chain2 collection present in scene with correct header > settings > group hierarchy.
- All chains from the physics chain list have a corresponding group.
- Each group is in the correct Settings block.
- RE Chain presets applied to all Settings blocks.
- User has confirmed the result (or adjusted and re-confirmed).

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

**Goal**: Connect Principled BSDF nodes, configure the MDF2 Generator, and bake all
materials into MHWs-compatible MDF2 + texture files.

### Entry Conditions
- [ ] Phases 4A-4B completed (or explicitly skipped with user acknowledgement).
- [ ] Source texture files accessible on disk or packed into the .blend file.

### Path Selection
| Source Model | Nodes | Action |
|---|---|---|
| MMD (PMX via CATS) | Already connected by CATS | Skip Steps 1-3; go directly to Step 4 |
| VRChat / Endfield / other | Mostly blank (situation C) | Run all steps in order |

---

### Step 1: Material Consolidation (non-MMD, run before classification)

Some models have multiple Blender materials that reference the exact same texture files.
These must be consolidated before node setup.

**Detection**: Group all materials by their texture file sets.
For each group sharing identical textures:

1. Read each material's Principled BSDF `Roughness` and `Metallic` values.
2. Compare across materials in the group:
   - Difference ≤ 0.1 on both → **silent merge**: replace duplicate materials with the
     first one, re-assign all affected mesh faces.
   - Difference > 0.1 on either → **warn user**:
     "Materials [A, B] share the same textures but have different roughness/metallic values.
     Merging them will lose this difference. Do you want to merge? [Yes / No, keep separate]"
3. If user chooses to keep separate: leave as-is, both materials proceed through classification.

---

### Step 2: Texture Classification (non-MMD, 4-layer priority)

Run layers in order. Stop at first successful classification for each texture.

**Layer 1 — Read existing nodes** (highest priority):
Inspect each material's node tree for already-connected Image Texture nodes.
If an Image Texture node is connected to a known Principled BSDF input socket,
treat its image as classified (high confidence). Skip to Step 3 for those textures.

**Layer 2 — Name-based rules** (high confidence, auto-apply):
| Filename pattern | Channel |
|---|---|
| `_BaseColor`, `_Albedo`, `_albedo`, `_d`, `_col`, `_color`, `_diffuse` | `base_color` |
| `_Normal`, `_normal`, `_n`, `_nrm`, `_NRM`, `_nm`, `_nor` | `normal` |
| `_Roughness`, `_roughness`, `_r`, `_rough` | `roughness` |
| `_Metallic`, `_metallic`, `_m`, `_metal` | `metallic` |
| `_AO`, `_ao`, `_occlusion`, `_occ` | `ao` |
| `_Emission`, `_emission`, `_emit`, `_glow`, `_emissive` | `emission` |
| `_ORM`, `_orm` | `packed_orm` (O=ao, R=roughness, M=metallic) |

**Layer 3 — Vision model fallback** (for unmatched textures):
- Downsample texture to 256×256, send to vision model (`vision_model` config key;
  default: Qwen-VL / DeepSeek-VL2 — do NOT use Claude unless explicitly configured).
- Prompt: "What PBR channel does this texture represent?
  Answer with exactly one of: base_color / normal / roughness / metallic / ao / emission / packed_orm / unknown"
- If result is not `unknown`: treat as mid confidence.

**Layer 4 — User confirmation** (for remaining `unknown` textures):
Surface via propose_and_confirm. Show texture thumbnail and ask user to assign channel.

**Material assignment** (after channel is known):
- 1 material: assign all textures to it.
- Multiple materials, texture filename contains material or mesh name: auto-assign (high).
- Ambiguous: surface via propose_and_confirm.

### Propose-and-Confirm: Texture Assignment
```json
{
  "proposals": [
    {
      "texture_file": "<filename>",
      "proposed_channel": "base_color|normal|roughness|metallic|ao|emission|packed_orm",
      "proposed_material": "<material name>",
      "detection_method": "existing_node|name_rule|vision_model|user",
      "confidence": "high|mid|low"
    }
  ],
  "requires_user_review": true
}
```

---

### Step 3: Node Connection (non-MMD only)

After texture assignment is confirmed, build the Principled BSDF node graph per material.
Connection patterns (create nodes in this order):

| Channel | Node graph |
|---|---|
| `base_color` | ImageTexture (sRGB) → Principled BSDF · Base Color |
| `normal` | ImageTexture (Non-Color) → NormalMap → Principled BSDF · Normal |
| `roughness` | ImageTexture (Non-Color) → Principled BSDF · Roughness |
| `metallic` | ImageTexture (Non-Color) → Principled BSDF · Metallic |
| `ao` | ImageTexture (Non-Color) → MixRGB (Multiply) · Color2; base_color result → MixRGB · Color1; MixRGB → Principled BSDF · Base Color |
| `emission` | ImageTexture (sRGB) → Principled BSDF · Emission Color |
| `packed_orm` | ImageTexture (Non-Color) → SeparateRGB; R → ao path above; G → Roughness; B → Metallic |

Notes:
- AO has no standalone Blender node; it must be multiplied into Base Color via MixRGB (Multiply).
- If both `ao` and `base_color` exist: chain them (base_color → MixRGB Color1, ao → Color2).
- NormalMap node strength: default 1.0.

---

### Step 4: Pre-Bake Render Configuration

Before opening the MDF2 Generator, configure Blender's render settings:
1. Properties → Render → Render Engine: set to **Cycles**.
2. Render → Device: set to **GPU Compute**.
3. Sampling → Viewport → Max Samples: set to **4**.
4. Sampling → Render → Max Samples: set to **4**.
This reduces bake time and GPU load significantly.

---

### Step 5: MDF2 Generator Setup

**5a — Open generator and configure mesh/path:**
1. Open MDF2 Generator.
2. Select mesh collection → click **Refresh**.
3. **Mod Root**: ask user to provide a path + folder name.
   - Agent checks if the folder exists at that path.
   - If not: create the folder, then select it.
4. **MDF Collection**: leave blank (auto-fills with default name).
5. **natives/stm/... path**: ask user for their name + character name.
   - Fill as `"username/charactername"` using English or pinyin.
   - Example: `"alice/kirin_mod"`

**5b — Wilds material type selection (per material):**

First, ask the user:
"Do you have preferences for which MHWilds material types to use? [Yes — describe / No, let agent decide]"

**If user has preferences**: follow them.

**If no preferences**: agent selects from the following standard MHWILDS presets
(use only non-prefixed presets; ignore `1*` prefixed files — those are user-added):

| Heuristic | Recommended preset |
|---|---|
| Mesh name contains `hair`, `fur` | `Hair` |
| Mesh name contains `eye`, `iris`, `pupil` | `Eye` |
| Mesh name contains `skin`, `face` | `Skin` |
| Has emissive texture | `Character Emissive` |
| Toon rendering requested | `Cel Shaded Character` + `Cel Shade Character Outline` |
| Everything else (body, armor, cloth) | `Character` |

After auto-selection: report to user for review before proceeding.
"I've assigned the following material types. Please review: [table]. [Confirm / Edit]"

**5c — Toon rendering (三渲二) checkbox:**
- MMD models: **check by default**. Do not ask the user.
- VRC / Endfield / other: **unchecked by default**.
  Ask: "Would you like to enable toon (cel-shaded) rendering for this mod? [Yes / No]"
  If yes: also add the `Cel Shade Character Outline` preset for outline pass.

Operator for applying a preset material:
```python
# Set the preset name in the tool panel, then apply
context.scene.re_mdf_toolpanel.mdfPreset = "<preset name>"
bpy.ops.re_mdf.add_preset_material()
```

**5d — Confirm and bake:**
After user confirms material types: click OK to start baking.
Inform user: "Baking has started. This may take several minutes depending on your GPU.
Please do not close Blender during this process."

---

### Exit Conditions
- All materials baked without `CANCELLED` status.
- MDF2 file written to the mod root path under `natives/stm/...`.
- `.tex` files generated for all texture channels.

### Common Errors
- **texconv not found**: RE Mesh Editor should bundle texconv; ask user to verify
  RE Mesh Editor is correctly installed.
- **Render engine not set to Cycles**: Bake operator may fail or use CPU. Verify
  render engine is Cycles before baking.
- **Roughness appears inverted in-game**: Source uses smoothness (inverted roughness).
  Enable the roughness invert option in the MDF2 Generator per-material settings.
- **Normal map lighting wrong**: Source uses GL-format normals. Enable GL→DX normal
  flip in the MDF2 Generator per-material settings.
- **Bake produces black textures**: GPU not selected, or max samples set too high.
  Verify Device = GPU and max samples = 4.

---

## Phase 6: Batch Export

**Goal**: Export final mod files (mesh + mdf2 + chain2) to the Natives directory,
then run BoneSystem export for the MHWs armature.

> **clsp (collision) files**: not processed in this workflow. Use the "empty model"
> option for all clsp slots — the exporter writes an empty placeholder automatically.
> Do NOT select the clsp collection or attempt to generate clsp data.

### Entry Conditions
- [ ] Phase 5 materials baked and MDF2 files written.
- [ ] MHWs armature (with physics bones transplanted) is in the scene.
- [ ] chain2 collection created (Phase 4B).

### Step 1: Hunter Type Selection

Ask the user to choose ONE export target (4 options):

| Option | Description |
|---|---|
| Female hunter / Female armor set | Default — recommend this unless user specifies |
| Female hunter / Male armor set | |
| Male hunter / Female armor set | |
| Male hunter / Male armor set | |

Present as a simple choice before opening the exporter.

### Step 2: Equipment Selection

The Batch Exporter's equipment list is populated at runtime from the game's armor data.
Ask the user to name the target equipment (e.g. "Kirin Beta chest piece").
Agent searches the exporter's equipment list for the closest match and selects it.
If no clear match is found: ask the user to select manually in the UI.

### Step 3: Collection Assignment

With this workflow, there is exactly ONE collection of each type:
- One mesh collection (contains the merged mesh + MDF2 Collection)
- One chain2 collection (from Phase 4B)

Assignment rules:
- **Body parts slot** (the armor piece being exported): assign the mesh collection.
- **All other equipment slots** (head / chest / arms / waist / legs not in use):
  set to **empty model** — do NOT leave unassigned; unassigned slots cause export errors.
- **clsp slot**: do NOT select; leave as empty model.
- If the scene has **multiple mesh collections**: surface all candidates to user,
  ask which is the correct one for this export.

Present the final assignment table for user confirmation before proceeding.

### Step 4: Batch Export

After user confirms collection assignments:
1. Run the MHWs Batch Exporter for all confirmed slots.
2. Unselected / empty slots automatically write empty placeholder files.

### Step 5: BoneSystem Export

Always required. Run after batch export completes.
- **Armature**: select the MHWs armature.
- **FBXSkel definition name**: use the character name provided in Phase 5 Step 5a
  (same string used for the `natives/stm/...` path).
- Run BoneSystem export.

### Step 6: Export Log Analysis

After all exports finish, the exporter may produce a warning/error log.
This log aggregates errors from all preceding phases — many issues in Phases 1-5
only become visible at export time (missing vertex groups, bone name mismatches, etc.).

If warnings or errors appear in the log:
1. Display the full log to the user.
2. For each error line, provide a plain-language interpretation of what it means.
3. Map the error back to the likely phase that caused it (use the table below).
4. Suggest the fix or ask the user how to proceed.

| Log error pattern | Likely origin | Suggested action |
|---|---|---|
| Missing vertex group / bone not found | Phase 3 rename incomplete | Re-check vertex group mapping |
| Bone count mismatch | Phase 3.5 transplant issue | Verify physics bones present |
| Invalid chain group / chain settings | Phase 4B structure issue | Re-check chain hierarchy |
| Texture not found / path error | Phase 5 bake path error | Re-verify mod root path |
| Material slot mismatch | Phase 3 merge issue | Check merged mesh material slots |

### User Interaction Points
- **Before Step 4**: Confirm hunter type + equipment + collection assignment in one summary.
  "Ready to export. Hunter: [type], Equipment: [name], Body slot → [collection].
  All other slots → empty model. [Confirm / Edit]"
- **After Step 6**: If log is clean, report success + file paths.
  If log has warnings, walk through them before asking "Done".

### Exit Conditions
- mesh + mdf2 + chain2 files written to Natives directory, non-empty.
- BoneSystem file written successfully.
- No unresolved `CANCELLED` status from any export operator.
- User has reviewed the export log (even if clean).

### Common Errors
- **RE Mesh Editor not installed**: Export operators unavailable. Report:
  "RE Mesh Editor is required for MHWs export. Please install it."
- **Unassigned slot causes export error**: Always assign empty model to unused slots.
- **BoneSystem FBXSkel name mismatch**: Name must exactly match the definition used
  during rig setup. Verify with user before re-running.
- **Baked textures missing from Natives path**: Phase 5 bake may have written to a
  different directory. Re-check mod root path against Natives root.

---

## Reference: Toolkit Operator Index

> Maps each phase's execution steps to their corresponding bpy.ops.* calls.
> To be completed as operator names are confirmed against plugin_api.md.

| Phase | Step Description | Operator | Notes |
|-------|-----------------|----------|-------|
| 1 | Bbox scale align | <!-- FILL IN --> | Scales armature + meshes |
| 1 | Direction calculation (MMD) | <!-- FILL IN --> | Rotates upper arms |
| 1 | Pose recorder forward (Endfield) | <!-- FILL IN --> | Applies pre-recorded delta |
| 2 | Align bones [X+Y] | <!-- FILL IN --> | Dual armature selection required |
| 3 | Rename vertex groups [X+Y] | <!-- FILL IN --> | Must select MESH, not ARMATURE |
| 3 | Merge meshes | <!-- FILL IN --> | Blender built-in join |
| 3.5 | Transplant physics bones [X+Y] | <!-- FILL IN --> | Dual armature; MHWs must be active |
| 3.5 | Refresh bone colors | <!-- FILL IN --> | Visualizes chain_role on transplanted bones |
| 4A | Merge to parent bone | <!-- FILL IN --> | For aux_merge bones |
| 4B | Create chain2 header | `re_chain.create_chain_header` | Set `chainFileType="chain2"` first; params: `collectionName`, `chainFormat=".chain2"` |
| 4B | Create initial Settings (shared) | Modding-Toolkit one-click create chain2 (shared mode) | Creates 1 Settings with all groups |
| 4B | Apply angle limit ramp | `re_chain.apply_angle_limit_ramp` | Active obj must be CHAINGROUP; params: `maxAngleLimit=1.047198`, `maxIteration=4` |
| 4B | Create additional Settings | `re_chain.create_chain_settings` | Poll: `chainCollection` must be set; no user params |
| 4B | Apply Chain Settings preset | `re_chain.apply_chain_settings_preset` | Set `chainSettingsPresets` enum first; name = filename without `.json` |
| 5 | MDF2 Generator bake | <!-- FILL IN --> | Per-material |
| 6 | MHWs Batch Exporter | <!-- FILL IN --> | Body slot → mesh collection; all others → empty model; clsp → empty model |
| 6 | BoneSystem export | <!-- FILL IN --> | Always required; armature = MHWs armature; FBXSkel name = character name |
