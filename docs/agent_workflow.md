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

## Pipeline State Assessment Protocol

**When asked to assess pipeline progress, verify completion, or describe current scene state:**

> **YOU MUST CALL QUERY TOOLS BEFORE DRAWING ANY CONCLUSIONS.**
> Do NOT use object or collection names from conversation history — they reflect the
> scene as it was when those messages were written, which may be many phases ago.
> The scene changes significantly between phases. Conclusions from stale history will be wrong.

**Mandatory tool calls before any phase-completion assessment:**

1. `list_objects()` — current object list (names, types, visibility flags)
2. `list_collections()` — all collection names and their contents
3. `scene_info()` — active object, mode, object count
4. If evaluating Phase 4A/4B: `get_bone_info(armature_name="MHWilds_Female Armature", filter_custom_prop="chain_role")`

**Interpretation rules — apply ONLY to fresh tool results, never to history:**

| What you see in tool output | What it means |
|---|---|
| Meshes named `Group_0_Sub_<N>__<material>` | Phase 3 ✅ + Phase 5B ✅ — generator split the merged mesh by material; this IS the completed state |
| Source `Armature` object with `"visible": false` | Phase 3.5 ✅ — `false` = hidden; **do NOT call this "still visible"** |
| EMPTY objects `RE_CHAIN_HEADER / RE_CHAIN_CHAINSETTINGS / RE_CHAIN_CHAINGROUP` | Phase 4B chain file containers — NOT physics bones |
| Collection with `.mdf2` in its name | Phase 5B ✅ — that IS the `mdf2_collection` name for Phase 6 |

**If any value is needed but not yet known (e.g. mdf2_collection name):**
Call `list_collections()` and read it from the result. Do NOT report it as "unknown" or "missing" without querying first.

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
[6  Batch Export]                      — includes automatic RE Mesh Tools cleanup
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

VRChat models are built on community avatar bases. If the source armature name or any mesh name contains one of the following keywords
(case-insensitive), infer VRChat and **present the inference to the user for confirmation**
before proceeding. Example phrasing:
> "从场景来看你导入了 `Shinano_body`，推测是 **VRChat** 格式，对吗？"

```
kipfel, shinano, manuka, milltina, rurune, mamehinata, shinra, chocolat, selestia,
kikyo, minase, sio, milfy, rinasciita, komano, mafuyu, eku, chiffon, karin, lumina,
marycia, mao, moe, lasyusha, rusk, ichigo, maya, mizuki, hakka, airi, zome, lapwing,
deltaflair, lime, kanata, rindo, sophina, platinum, nemesis, sapphy, wolferia, ririka,
mishe, kokoa, fiona, mint, lazuli, soraha, minahoshi, koyuki, cian, meiyun, merino,
velle, anon, ciel, sephira, lucifer
```

If no keywords match, ask the user directly: "你的来源模型是哪种格式？MMD / VRChat / 终末地 / 其他？"

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

#### Shared Root Bones (`*_root` pattern)
Some physics setups attach multiple chains to a single intermediate `*_root` bone (e.g.
`hair_root`, `skirt_root`) rather than directly to a body bone. These root bones are **not**
chain members — they are connector pivots. Behaviour:
1. Detect: a bone named `*_root` (or `*Root`) that has 2+ physical-chain children and
   parents to a body bone.
2. **Always inform the user**: list each detected root bone, name its children chains, and
   explain its purpose as a shared pivot.
3. Ask the user which root bones should be merged into their parent body bone via
   `modder.merge_into_parent`. Default assumption is merge; the user confirms or exempts.
   (Some root bones carry weight data and must be kept; only the user can know this.)

### Silent-Ignore Rule: `_HJ_` Bones
Any bone whose name contains `_HJ_` is a built-in MHWs auxiliary (helper/adjuster) bone
that was already present in the target armature before transplant. **Do not classify, do not
merge, do not mention to the user.** Skip silently.

### Classification Heuristics

#### Physical Bone Indicators (treat as physical)
- Name contains: `hair`, `skirt`, `dress`, `cloth`, `tail`, `phys`, `dyn`, `spring`,
  `ribbon`, `cape`, `sleeve`, `fringe`, `bang`, `braid`, `ponytail`
- Forms a chain of 2+ bones in parent-child sequence (typical of physics chains)
- Has `chain_role` custom property set on it (Modding-Toolkit marker)
- Is a leaf bone (no children) at the end of a chain that started with a physical-named root

#### Unlisted Auxiliary Bone Indicators (treat as aux_merge)
- Name contains: `twist`, `roll`, `adj`, `helper`, `correct`, `fix`, `ik`, `pole`,
  `target`, `ctrl`, `control`, `weapon`, `camera`, `cam`
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
`_HJ_` bones are never included in proposals — they are silently skipped.

### Execution Steps (after user confirms proposal)

**Operator: `modder.clear_chain_role`** — clears `chain_role` property from selected
pose bones. **Does NOT reset bone colors** — color palette must be reset separately.
Use for native game bones that must be excluded from physics entirely (they remain in
the armature; only the physics marking is removed).

**Operator: `modder.merge_into_parent`** — merges selected bones' vertex weights into
their direct parents, then deletes the selected bones. Children reconnect to grandparent
automatically. Auto-refreshes bone colors.
Preconditions: active object = ARMATURE, mode = POSE or EDIT, selected bones must have parents.

Steps:
1. **Native game bone exclusion** (if any): for bones whose `chain_role` was marked by
   `refresh_physics_bone_colors` but that are actually native game bones (e.g. `Cage`,
   `Cage_L`) — they should NOT be physics. Pass them in `bones_to_clear` when calling
   `physics_chains`. The tool selects them in POSE mode and calls `clear_chain_role`.
2. Handle shared root bones: for each `*_root` bone the user approved to merge,
   pass it in `bones_to_merge`. The tool calls `modder.merge_into_parent`.
3. For each bone classified as `aux_merge`: also pass in `bones_to_merge`.
4. Verify the remaining unlisted bones are all classified as `physical`.
5. Build the physics chain list (see output schema below).

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
- **`modder.merge_into_parent` fails**: The selected bone has no parent in the armature.
  Ask user to manually identify the correct parent body bone in Blender's Outliner.
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

### Pre-Creation Cleanup (always run before chain creation)

Before calling `physics_chains`, stale `chain_role` marks from prior sessions can
contaminate the scene (e.g. body bones incorrectly marked as `chain_role='head'`
from a previous aborted run → one CHAINGROUP per body bone = hundreds of spurious groups).

**Run `physics_chains(target_armature=..., prepare_only=True)` first:**
- Selects all bones, calls `modder.clear_chain_role` on all selected.
- Resets every bone's color palette to DEFAULT (bone colors are NOT cleared by `clear_chain_role`).
- Calls `modder.refresh_physics_bone_colors` to re-detect and re-mark physics bones.
- **Auto-verifies** that marks are clean: every `chain_role='head'/'branch_head'` bone must
  have at least one `_End` bone descendant (auto-generated by Phase 3.5 for transplanted
  physics chains). Body bones have no `_End` descendants → detected as suspicious.
  - If suspicious bones found, retries cleanup+refresh once and re-verifies.
  - Returns `marks_clean: bool` in state diff.

**If `marks_clean=True`**: proceed directly to chain creation — no user confirmation needed.

**If `marks_clean=False`**: report the suspicious bone names to the user; ask them to
inspect bone colors in Blender and manually remove incorrect chain_role marks if needed,
then call `prepare_only=True` again before proceeding.

### Execution Steps (after prepare_only confirms marks_clean=True)

**Step 0 — Bone exclusions and merges (in same `physics_chains` call):**
- `bones_to_merge`: bones to merge into parent via `modder.merge_into_parent`. **Runs first.**
  Reason: `merge_into_parent` auto-refreshes chain_role colors; if clear ran before merge,
  the refresh would re-mark the just-cleared native bones as physics.
- `bones_to_clear`: native game bones that were accidentally marked and must be excluded
  (e.g. `Cage`, `Cage_L`). `clear_chain_role` is called on them **after** merge so the
  merge's color-refresh cannot re-mark them.

**Before calling `physics_adjust`, always call `physics_read` first** to inspect current
parameter values — do not adjust blindly based on assumptions or conversation history.
Example:
```json
{ "targets": ["CHAIN_SETTINGS_04"], "properties": ["gravity", "damping"] }
```

**Step 1 — `mhws.auto_create_chains(settings_mode='SEPARATE')`:**
Creates the RE Chain collection (auto-discovered or auto-created), then calls the
one-click chain creation operator in SEPARATE mode: one Settings block per chain head.
The agent sets `re_chain_toolpanel.chainCollection` via PointerProperty before calling
(NOT via dynamic enum kwarg — the kwarg is unreliable in scripted context).

**Step 2 — Apply angle limit ramp to all groups:**
Selects all `RE_CHAIN_CHAINGROUP` objects in the collection, calls:
```python
bpy.ops.re_chain.apply_angle_limit_ramp(maxAngleLimit=1.047198, maxIteration=4)
```
`maxAngleLimit=1.047198` ≈ 60°, `maxIteration=4`.

**Step 3 — Apply physics parameters from `physics_presets.json`:**
For each new `RE_CHAIN_CHAINSETTINGS` object, set the corresponding physics parameters
directly via PropertyGroup attribute assignment (no preset files required on disk).
Parameters are stored in `app/data/physics_presets.json` keyed by `inferred_type`.
Enum fields (windDelayType etc.) require `str(int(val))` conversion before assignment.

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

Use the `physics_adjust` tool to apply these changes. It does NOT re-create chains —
it sets properties directly on named CHAIN_SETTINGS objects and does not advance the phase.

To identify target CHAIN_SETTINGS names: call `list_objects(type_filter="EMPTY")` and
look for objects with `RE_CHAIN_CHAINSETTINGS` in their name.

```json
{
  "targets": ["CHAIN_SETTINGS_04", "CHAIN_SETTINGS_44"],
  "properties": {"gravity": [0, 3, 0]}
}
```

After a preset is applied, the user may request fine-tuning. Translate as follows:
| User phrase | Parameters to change |
|---|---|
| "stiffer" / "less floppy" | Increase `damping`+`minDamping`; decrease `reduceSelfDistanceRate` |
| "softer" / "more floppy" | Decrease `damping`+`minDamping`; increase `reduceSelfDistanceRate` |
| "add gravity" / "droops too little" | Set `gravity: [0, -9.8, 0]` |
| "remove gravity" / "floats" | Set `gravity: [0, 0, 0]` |
| "reverse gravity" / light upward force | Set `gravity: [0, <positive>, 0]` |
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

**Goal**: Prepare Blender materials for export (Phase 5A), then generate MHWs-native
`.mdf2` and `.tex` files by mapping each Blender material slot to one MHWs game preset
(Phase 5B). The three supported output shader types differ only in which sockets are
relevant and whether `material_setup` is ever needed:

| Output shader | Relevant sockets to check | `material_setup` applies? | Generator handles the rest? |
|---|---|---|---|
| **Principled BSDF** | Base Color, Roughness, Metallic, Normal, etc. | Yes — if any slot is blank | Yes |
| **Emission** | Color (the only input) | **Never** | Yes — all other channels filled by preset |
| **MMDShaderDev GROUP** | Base Tex, Base Alpha | **Never** | Yes — all other channels filled by preset |

Key points:
- `material_setup` is a PBS-only tool. Do not call it for Emission or MMDShaderDev regardless
  of what `material_inspect` shows for their PBS slots (those slots are irrelevant for these
  shader types).
- Emission materials have exactly one relevant input: the Color socket. It may hold an
  ImageTexture (→ generator uses DIRECT strategy, texture path copied) or a constant color
  (→ generator uses SOLID strategy, 256×256 solid `.tex` generated). Both are valid inputs
  for `material_generate` — no wiring is needed either way.
- MMDShaderDev sockets are wired by the CATS importer. Confirm via `material_inspect`, then
  call `material_generate` directly.
- A single mesh can have mixed shader types. Apply the correct path per material, not per mesh.

> **Broken-path detection**: The ONLY authoritative signal for a missing texture is
> `existing_connections[mat][slot].exists == false` returned by `material_inspect`.
> Do NOT use `bpy.types.Image.has_data` (via query tools or otherwise) to judge path
> validity — that flag is a lazy-pixel-load indicator (set only after viewport/render
> touches the image), not a disk-presence check. A texture can be on disk and fully
> valid while `has_data` is still False.

> **Deep node inspection**: `material_inspect` only summarizes Principled BSDF slot
> connectivity. When a material uses Emission / MMDShader / MixShader (the actual
> shader feeding Material Output is NOT a PBS), or when checking for orphan
> Image Texture nodes left over from prior imports, call `inspect_material_nodes`
> (a query tool) for a single material's full node tree: every node, every link,
> the real `output_shader`, and the `orphan_nodes` list. Use it when a material's
> PBS summary looks suspiciously empty.

### Entry Conditions
- [ ] Phases 4A-4B completed (or explicitly skipped with user acknowledgement).
- [ ] Source texture files accessible on disk or packed into the .blend file.

---

### Phase 5A: Blender Material Prep

**Goal**: Consolidate duplicate materials, inspect each material's output shader type and
socket connectivity, and (for Principled BSDF materials only) classify and wire blank
texture slots. Emission and MMDShaderDev materials need no wiring — they pass directly
to Phase 5B.

#### Step 1: Material Consolidation

Some models — especially VRChat community bases — split one logical material into many
Blender materials that all reference the same image files. Without consolidation, every
downstream step repeats work for each duplicate.

Call `material_consolidate(dry_run=True)` first to see a grouping proposal, confirm with
the user, then call `material_consolidate(dry_run=False, groups=...)` to apply.

#### Step 2: Texture Classification (Principled BSDF only, 4-layer priority)

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

##### Propose-and-Confirm: Texture Assignment
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

#### Step 3: Node Connection (Principled BSDF only)

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

#### Phase 5A Exit Conditions
- All materials consolidated (duplicates merged).
- All Principled BSDF materials: texture slots classified and wired.
- Emission and MMDShaderDev materials: no wiring needed; confirmed via `material_inspect`.

---

### Phase 5B: MDF2 Generation

**Goal**: Map each Blender material slot to exactly ONE MHWs game-side preset, then call
`material_generate` to produce `.mdf2` and `.tex` output files.

> **Preset concept**: A preset is an MHWs game-side rendering material definition — a
> `.json` file in RE Mesh Editor's `Presets/MHWILDS/` directory. It tells the game engine
> how to render the mesh in-game. It has **no relationship** to Blender materials, shader
> types, or texture content. Each Blender material slot maps to exactly ONE preset string.
> There is no mixing, stacking, or combining presets for a single slot.

#### Step 1: Confirm mesh collection

Call `list_collections()` to enumerate available collections. Identify the mesh collection
(always `MHWilds_Female.mesh`, created in Phase 3). Use the exact name returned by
`list_collections()` — do not guess or hardcode it.

#### Step 2: Enumerate available presets

Call `list_mdf_presets()` to get the actual preset names installed on this machine.
Present the full list to the user.

#### Step 3: Select ONE preset per Blender material slot

Ask the user to pick exactly ONE preset for each material. If the user has no preference,
propose via `propose_and_confirm` using these heuristics (then wait for user confirmation):

| Heuristic | Suggested preset |
|---|---|
| Material/mesh name contains `hair`, `fur` | `Hair` |
| Material/mesh name contains `eye`, `iris`, `pupil` | `Eye` |
| Material/mesh name contains `skin`, `face` | `Skin` |
| Output shader is Emission | `Character Emissive` |
| Everything else (body, armor, cloth, accessories) | `Character` |

Present as a `propose_and_confirm` table. Surface any `low` confidence rows for user review
before proceeding.

#### Step 4: Call `material_generate`

```json
{
  "mesh_collection": "<name confirmed from list_collections()>",
  "natives_root": "<user-provided mod root folder — any path; toolkit auto-creates natives/ inside>",
  "texture_base_path": "<user-provided sub-path, e.g. 'Author/CharName/'>",
  "preset_mapping": { "<mat_name>": "<preset_name>", ... }
}
```

Parameters:
- `mesh_collection`: must come from `list_collections()` result — do not hardcode.
- `natives_root`: **required**. Ask the user for their mod root folder path. Pass whatever
  folder they specify; the toolkit automatically creates the `natives/` directory structure
  inside it if it does not exist.
- `texture_base_path`: ask the user for their author/character sub-path
  (e.g. `"alice/kirin_mod"`).
- `preset_mapping`: one entry per Blender material slot; value is the exact preset name
  from `list_mdf_presets()` output.

#### Phase 5B Exit Conditions
- `.mdf2` file written under `<natives_root>/natives/STM/...`.
- `.tex` files generated for all texture channels.
- `material_generate` tool result contains key `"mdf_collection"` with the exact Blender
  collection name (e.g. `"MHWilds_Female.mdf2"`). **Record this value.** It is passed
  verbatim as `mdf2_collection` in Phase 6. Do NOT guess, infer, or reconstruct it.

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

**Goal**: Export final mod files (mesh + mdf2 + chain2) to the Natives directory,
then run BoneSystem export for the MHWs armature.

> **clsp (collision) files**: not processed in this workflow. Use the "empty model"
> option for all clsp slots — the exporter writes an empty placeholder automatically.
> Do NOT select the clsp collection or attempt to generate clsp data.

### Entry Conditions
- [ ] Phase 5 materials baked and MDF2 files written.
- [ ] MHWs armature (with physics bones transplanted) is in the scene.
- [ ] chain2 collection created (Phase 4B).

> **Pre-export mesh cleanup is automatic**: `batch_export` internally runs
> `re_mesh.delete_loose`, `re_mesh.solve_repeated_uvs`,
> `re_mesh.remove_zero_weight_vertex_groups`, and
> `re_mesh.limit_total_normalize(maxWeights='12')` on every mesh in
> `mesh_collection` before exporting. Operator warnings (if any) are surfaced
> in `state_diff["mesh_cleanup_warnings"]` but do NOT block the export.
> You do NOT need to call any separate cleanup tool before `batch_export`.

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

Ask the user to name the target armor set (e.g. "煌雷龙 β 胸甲"). Match the description
against the table below to find the `id`. The `id` is passed to the batch export operator
as `mhws_armor_scheme`.

**If multiple candidates match** (e.g. α and β share the same `id`): present via
`propose_and_confirm` and wait for user to confirm.

**Variant key** (determined by Hunter Type from Step 1):
| Variant | Meaning |
|---|---|
| `ff` | Female hunter / Female armor set — **default** |
| `fm` | Female hunter / Male armor set |
| `mf` | Male hunter / Female armor set |
| `mm` | Male hunter / Male armor set |

**Part keys**: `1`=手臂(Arm)  `2`=身体(Body)  `3`=头盔(Helmet)  `4`=腿(Leg)  `5`=腰(Waist)

**Full armor set table** (source: `assets/mhws/armor_sets/mhws_armor_sets.json`):

| ID | Name |
|---|---|
| pl001 | 希望 α (Hope α) |
| pl003 | 辟兽 α/β (Doshaguma α/β) |
| pl003_500 | 护辟兽 α/β (Guardian Doshaguma α/β) |
| pl004 | 骨制 α (Bone α) |
| pl005 | 皮制 α (Leather α) |
| pl006 | 锁甲 α (Chainmail α) |
| pl007 | 钳速龙 α/β (Talioth α/β) |
| pl008 | 缠蛙 α/β (Chatacabra α/β) |
| pl009 | 炎尾龙 α/β (Quematrice α/β) |
| pl010 | 合金 α (Alloy α) |
| pl011 | 锯带龙 α/β (Piragill α/β) |
| pl012 | 刺花蜘蛛 α/β (Lala Barina α/β) |
| pl013 | 桃毛兽王 α/β (Conga α/β) |
| pl014 | 沙海龙 α/β (Balahara α/β) |
| pl015 | 铸铁 α (Ingot α) |
| pl016 | 血盗虫 α/β (Bulaqchi α/β) |
| pl017 | 波衣龙 α/β (Uth Duna α/β) |
| pl017_300 | 波衣龙 γ (Uth Duna γ) |
| pl018 | 沼喷龙 α/β (Rompopolo α/β) |
| pl019 | 杜宾 α/β (Dober α/β) |
| pl020 | 盔速龙 α/β (Kranodath α/β) |
| pl021 | 煌雷龙 α/β (Rey Dau α/β) |
| pl021_300 | 煌雷龙 γ (Rey Dau γ) |
| pl022 | 影蜘蛛 α/β (Nerscylla α/β) |
| pl023 | 风铗龙 α/β (Hirabami α/β) |
| pl024 | 赫猿兽 α/β (Ajarakan α/β) |
| pl025 | 花纹钢 α/β (Damascus α/β) |
| pl026 | 血眠虫 α/β (Comaqchi α/β) |
| pl027 | 狱焰蛸 α/β (Nu Udra α/β) |
| pl027_300 | 狱焰蛸 γ (Nu Udra γ) |
| pl028 | 巨蜂 α/β (Vespoid α/β) |
| pl029 | 冻峰龙 α/β (Dahaad α/β) |
| pl029_300 | 冻峰龙 γ (Dahaad γ) |
| pl030_600 | 护凶爪龙 α/β (Guardian Ebony α/β) |
| pl031 | 暗器蛸 α/β (Xu Wu α/β) |
| pl032 | 锁刃龙 α/β (Arkveld α/β) |
| pl032_300 | 锁刃龙 γ (Arkveld γ) |
| pl032_500 | 护锁刃龙 α/β (Guardian Arkveld α/β) |
| pl033 | 护鹭鹰龙 α/β (Guardian Seikret α/β) |
| pl034 | 怪鸟 α/β (Kut-Ku α/β) |
| pl035 | 雌火龙 α/β (Rathian α/β) |
| pl036 | 火龙 α/β (Rathalos α/β) |
| pl036_500 | 护火龙 α/β (Guardian Rathalos α/β) |
| pl037 | 毒怪鸟 α/β (Gypceros α/β) |
| pl038 | 千刃龙 α/β (Seregios α/β) |
| pl039 | 海龙 α/β (Lagiacrus α/β) |
| pl040 | 铠龙 α/β (Gravios α/β) |
| pl041 | 雪狮子王 α/β (Blango α/β) |
| pl042 | 黑蚀龙 α/β (Gore α/β) |
| pl043_600 | 护雷颚龙 α/β (Guardian Fulgur α/β) |
| pl044 | 龙王的独眼 α (Dragonking α) |
| pl045 | 封印的龙骸布 α (Sealed Dragon Cloth α) |
| pl046 | 库纳法 α (Kunafa α) |
| pl047 | 阿孜兹 α (Azuz α) |
| pl048 | 西尔德 α (Sild α) |
| pl049 | 酥加 α (Suja α) |
| pl050 | 调查团 α (Commission α) |
| pl051 | 机械 α (Artian α) |
| pl052 | 咬鱼 α (Gajau α) |
| pl053 | 死神 α (Death Stench α) |
| pl054 | 燕尾蝶 α (Butterfly α) |
| pl055 | 独角仙 α (King Beetle α) |
| pl056 | 矿石 α (High Metal α) |
| pl057 | 战斗 α (Battle α) |
| pl058 | 花瓣 α (Melahoa α) |
| pl059 | 纯洁龙 α/β (Numinous α/β) |
| pl060 | 公会骑士 (Guild Knight) |
| pl061 | 兵之甲冑 (Feudal Soldier) |
| pl062 | 公会王牌 α (Guild Ace α) |
| pl063 | 花妖猩 α (Mimiphyta α) |
| pl064 | 鬼角假发 (Oni Horns Wig) |
| pl065 | 剑豪独眼罩 (Fencer's Eyepatch) |
| pl066 | 泡狐龙 α/β (Mizutsune α/β) |
| pl067 | 贵族 (Noblesse) |
| pl068 | 萌芽头冠 (Florescent Circlet) |
| pl069 | 落樱缤纷 α (Sakuratide α) |
| pl070 | 恶魔 α (Demon) |
| pl071 | 潜水员 α (Diver α) |
| pl072 | 龙人族之耳 (Wyverian Ears) |
| pl073 | 公会十字 α (Guild Cross α) |
| pl074 | 职员 α (Clerk α) |
| pl075 | 盛开 α (Blossom α) |
| pl076 | 奉献耳饰 α (Earrings of Dedication α) |
| pl076_100 | 大胃王耳饰 α (Gourmand's Earring α) |
| pl077 | 艾露猫头套 α (Faux Felyne α) |
| pl078 | 泡歌鸮 α (Amstrigian α) |
| pl079 | 封印的龙骸布 (Sealed Dragon Cloth) |
| pl080 | 霹雳舞猫 (Cypurrpunk) |
| pl081 | 踊火 α (Afi α) |
| pl083 | 毛茸茸兽耳 (Fluffy Ears) |
| pl084 | 毛茸茸兽尾 (Fluffy Tail) |
| pl085 | 哥特幽魂 α (Dreamwalker α) |
| pl086 | 收获 α (Harvest α) |
| pl087 | 调查队耳饰 α (Expedition Headgear α) |
| pl088 | 知性眼镜 α (Strategist Spectacles α) |
| pl088_100 | 方形眼镜 α (Square Glasses α) |
| pl088_200 | 墨镜 α (Shadow Shades α) |
| pl088_300 | 圆框眼镜 α (Round Glasses α) |
| pl088_400 | 心形眼镜 α (Lovely Shades α) |
| pl088_500 | 下半框眼镜 α (Half Rim Glasses α) |
| pl088_600 | 泪滴墨镜 α (Aviator Shades α) |
| pl088_700 | 猫眼眼镜 α (Kitten Frames α) |
| pl088_800 | 炫光护目镜 α (Mirror Visor α) |
| pl088_900 | 分析之眼 α (Analytic E.Y.E. α) |
| pl089 | 单羽项链 α (Pinion Necklace α) |
| pl090 | 启程的鹰之心 α (Hawkheart Jacket α) |
| pl091 | 宇宙机装 (Cosmoloid) |
| pl092 | 巨戟龙 α/β (Gogmazios α/β) |
| pl093 | 祭典 α (Ceremonial α) |
| pl094 | Gala Suit Slacks α |
| pl095 | 辟兽头套 α (Doshagumask α) |
| pl096 | 胶鲵 α (Gelidron α) |
| pl097 | 肉垫手套 α (Toe Bean Mittens α) |
| pl098 | 骷髅面罩 α (Skull Mask α) |
| pl099 | 猎户星 α (Orion α) |
| pl100 | 欧米茄服装 α (Omega Attire α) |
| pl100_100 | 凶恶套装 α (Bale Armor α) |
| pl101 | 精力充沛森狸人 α (Eager Wudwud α) |
| pl102 | 祝福腰饰 α (Blessed Waistchain α) |
| pl103 | 女王 α (Sororal α) |
| pl104 | 苍世武士 α (Azure Age Haori) |
| pl105 | 沼喷龙头套 α (Rompomask α) |

### Step 3: Collection Assignment

**Before assigning, verify all three collection names exist:**
Call `list_collections()` and confirm the three names appear in the result:
- `mesh_collection` — always `"MHWilds_Female.mesh"` (created in Setup Step 2)
- `mdf2_collection` — use the exact `"mdf_collection"` value from `material_generate`'s
  result; **never guess or reconstruct this name**
- `chain2_collection` — name returned by `physics_chains` in Phase 4B

If any collection is missing, stop and report the missing collection to the user before
proceeding.

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
| 5B | MDF2 generate | `mhws.mdf_gen_refresh` + `mhws.mdf_gen_process` | `material_generate` tool; natives_root = any user folder |
| 6 | MHWs Batch Exporter | <!-- FILL IN --> | Body slot → mesh collection; all others → empty model; clsp → empty model |
| 6 | BoneSystem export | <!-- FILL IN --> | Always required; armature = MHWs armature; FBXSkel name = character name |
