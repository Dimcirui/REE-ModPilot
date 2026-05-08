# Modding Toolkit - Plugin Operator API Reference

> Auto-generated from plugin source code. All `bpy.ops` operators exposed by the Modding Toolkit Blender addon.
> Use via `bpy.ops.<bl_idname>(<params>)` in `execute_blender_code` calls.

---

## 1. 姿态转换 (Pose Convert) — `core/pose_ops.py`

### `modder.tpose_direction`
- **Label**: 方向计算 (简单T转A)
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - `context.scene.mhw_suite_settings.pose_import_preset_enum` must load a valid X preset (via BoneMapManager)
- **Behavior**: Rotates upperarm_L / upperarm_R bones to horizontal orientation. Simple A-Pose → T-Pose tool. Applies armature pose, converts meshes to apply deformation, then re-binds mesh.

### `modder.tpose_matrix_zero`
- **Label**: RE Engine 矩阵归零
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - `context.scene.mhw_suite_settings.pose_import_preset_enum` must load a valid X preset
- **Behavior**: Zeroes out rotation matrices for all limb bones (clavicle, upperarm, forearm, hand, all fingers, thigh, shin, foot, toe). Sets a hardcoded RE Engine orientation matrix. **Not suitable for RE9.** Applies armature and re-binds mesh.

### `modder.record_transform`
- **Label**: 录制变换
- **Params**:
  - `preset_name` (StringProperty) — save filename for the transform JSON
- **Preconditions**:
  - Must select **two** ARMATURE objects (A-pose first, then Ctrl+click B-pose)
  - `context.active_object` = B-pose skeleton
  - Both skeletons must have overlapping bone names
- **Behavior**: Computes per-bone local rotation delta (Qb × Qa⁻¹) relative to parent. Only saves bones where rotation difference exceeds threshold (>0.9999 dot product). Outputs JSON to `assets/presets/pose/<name>.json`.

### `modder.apply_transform_forward`
- **Label**: 正向 (A→B)
- **Params**: none (reads from scene settings)
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - `context.scene.mhw_suite_settings.pose_preset_enum` must be set to a valid JSON (not 'NONE')
  - `context.scene.mhw_suite_settings.pose_import_preset_enum` should be a valid X preset for bone name bridging
- **Behavior**: Applies recorded transform in forward direction (A→B). Converts parent-space delta to bone local space via `rest_rot⁻¹ × delta × rest_rot`, applies to `matrix_basis`. Re-binds mesh.

### `modder.apply_transform_inverse`
- **Label**: 逆向 (B→A)
- **Params**: none
- **Preconditions**: Same as `apply_transform_forward`
- **Behavior**: Same as forward but inverts the delta quaternion first.

### `modder.delete_pose_preset`
- **Label**: 删除记录
- **Params**: none (reads `settings.pose_preset_enum`)
- **Preconditions**: A valid pose preset must be selected in the enum
- **Behavior**: Deletes the selected JSON file from `assets/presets/pose/`. Shows confirmation dialog.

---

## 2. 通用标准转换 (Standard Conversion) — `core/standard_ops.py`

### `modder.universal_snap`
- **Label**: 骨架对齐 [X+Y, 双骨架]
- **Params**: none
- **Preconditions**:
  - Must select **two** ARMATURE objects (source=X first, then Ctrl+click target=Y)
  - `context.active_object` = target skeleton (Y)
  - Both `import_preset_enum` (X) and `target_preset_enum` (Y) must load valid presets
- **Behavior**: Rigid body bone alignment. For each standard bone key: computes source bone world-space head position, converts to target local space, moves target bone head to that position, translates tail equally (preserving bone direction), then recursively propagates offset to all children. Bones with `skip_snap: true` in Y preset are skipped. Operates in EDIT mode.
- **Related scene settings**: `import_preset_enum`, `target_preset_enum`

### `modder.direct_convert`
- **Label**: 重命名顶点组 [X+Y]
- **Params**: none
- **Preconditions**:
  - At least one **MESH** object must be selected (via `context.selected_objects`)
  - Both `import_preset_enum` (X) and `target_preset_enum` (Y) must load valid presets
- **Behavior**: Converts vertex group names from source format (X) to target game format (Y) on all selected meshes. Uses fuzzy name matching (`_normalize_bone_name`). Merges aux bone weights into main bone, then renames main vertex group to target name. spine_03 auto-fallback: if Y preset lacks spine_03, weights merge into spine_02 target. If target name already occupied, old group is removed first.

### `modder.apply_standard_x`
- **Label**: 标准化重命名 (X)
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - `import_preset_enum` must load a valid X preset
- **Behavior**: Merges aux bone weights into main bones on all bound meshes. In EDIT mode: renames main bones to standard key names, deletes aux bones. One-way standardization of source skeleton.

### `modder.apply_standard_y`
- **Label**: 转换为游戏名 (Y)
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - `target_preset_enum` must load a valid Y preset
- **Behavior**: In EDIT mode, renames bones from standard key names to target game bone names (from Y preset). One-way.

### `modder.smart_graft`
- **Label**: 物理骨移植 (+End Bone)
- **Params**: none
- **Preconditions**:
  - Must select **two** ARMATURE objects (source=In first, then Ctrl+click target=Out)
  - `context.active_object` = target skeleton (Out)
  - Both `import_preset_enum` (X) and `target_preset_enum` (Y) must load valid presets
  - Source skeleton must have physics bones (bones not in X preset)
- **Behavior**: Full physics bone transplant workflow. Phase 1: copies all physics bones from source to target at world-space positions. Phase 2: detects leaf bones and fork points without `main_continue` marker, auto-generates `_End` bones at source bone tail positions. Phase 3: verticalizes all created bones (Z+ direction, use_connect=False, roll=0). Phase 4: rebuilds parent hierarchy — physics→physics direct; physics→mapped bone via standard key bridge; orphan physics→ancestor chain lookup. Copies `chain_role` custom properties from source to target.

### `modder.merge_physics_weights`
- **Label**: 物理权重降级 [X]
- **Params**: none
- **Preconditions**:
  - At least one **MESH** object must be selected
  - Selected meshes must have an armature modifier (find_armature)
  - `import_preset_enum` must load a valid X preset
- **Behavior**: Merges physics bone vertex groups into their nearest base bone (walking up parent chain until a preset bone is found). Used for stripping physics when target game doesn't need it.

### `modder.rename_bones_to_target`
- **Label**: 基础骨骼改名 [X+Y]
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - Both `import_preset_enum` (X) and `target_preset_enum` (Y) must load valid presets
- **Behavior**: Renames base bones from X-actual-name to Y-target-name via standard key bridging. If target name already occupied, renames the occupying bone to `<name>_old` first. Operates in EDIT mode.

### `modder.remove_non_base_bones`
- **Label**: 剔除非基础骨骼 [X]
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - `import_preset_enum` must load a valid X preset
- **Behavior**: Deletes all bones not found in the X preset. Use after `merge_physics_weights` to fully strip physics. Operates in EDIT mode.

### `modder.set_bone_visibility`
- **Label**: 骨骼可见性
- **Params**:
  - `mode` (EnumProperty) — one of `'ALL'`, `'BASE'`, `'PHYSICS'`
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - For BASE/PHYSICS modes: `import_preset_enum` must load a valid X preset
- **Behavior**: Sets bone hide state per mode. Mode ALL: unhide all. Mode BASE: hide non-preset bones. Mode PHYSICS: hide preset bones. Saves choice to `settings.bone_view_mode`.

### `modder.refresh_physics_bone_colors`
- **Label**: 刷新骨骼颜色 [X]
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - `import_preset_enum` must load a valid X preset
- **Behavior**: Detects `chain_role` for physics bones and applies 4-color marking system: head=sky blue, branch_head=purple, main_continue=amber gold, body/untagged=deep blue. Operates in POSE mode.

### `modder.mark_as_main_continue`
- **Label**: 标记为主链延伸
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - Must be in POSE mode (auto-switches if needed)
  - At least one pose bone must be selected
- **Behavior**: Sets `chain_role = 'main_continue'` on selected pose bones and colors them amber gold.

### `modder.clear_chain_role`
- **Label**: 清除链角色标记
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - Must be in POSE mode (auto-switches if needed)
  - At least one pose bone must be selected
- **Behavior**: Clears `chain_role` custom property from selected bones, resets color to deep blue (body).

### `modder.merge_into_parent`
- **Label**: 合并到父骨
- **Params**: none
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - Must be in POSE or EDIT mode
  - Selected bones must have parent bones
- **Behavior**: Merges selected bones' vertex weights into their parents, then deletes the selected bones. Disconnects children first. Auto-refreshes bone colors after. Used for cleaning functional root bones (hair_root, etc.).

---

## 3. 基础工具 (General Tools) — `ui/main_panel.py`

### `mhw.general_tools`
- **Label**: 通用工具
- **Params**:
  - `action` (EnumProperty, required) — one of:
    - `'ROLL_ZERO'` — 扭转归零: recursively sets Roll=0 on selected bones (EDIT mode). Must select bones in EDIT mode.
    - `'ADD_TAIL'` — 添加尾骨: adds vertical tail bones at selected bone tips (EDIT mode). Must select bones in EDIT mode.
    - `'MIRROR_X'` — 镜像对齐 X: mirrors bone transforms from X+ to X- (EDIT/POSE mode). Must select exactly **2** bones.
    - `'SIMPLIFY_CHAIN'` — 骨链简化: pairs bones into (keep, merge) groups by chain structure, merges weights, deletes merged bones. Must select ≥2 bones in EDIT mode. Auto-skips tail bones with no weights.
    - `'MERGE_TO_ACTIVE'` — 合并到激活骨: merges all other selected bones' weights into the active bone (last clicked), then deletes others. Must have active bone + ≥2 selected bones in EDIT mode.
    - `'ALIGN_POS'` — 对齐 (位置): aligns target bones' head to source by name matching, preserves length/direction. Must select 2 ARMATURE objects.
    - `'ALIGN_POS_ROLL'` — 对齐 (位置+扭转): aligns head and roll, preserves length/direction. Must select 2 ARMATURE objects.
    - `'ALIGN_FULL'` — 对齐 (完全): full alignment (head+tail+roll) by bone name. Must select 2 ARMATURE objects.
    - `'MERGE_CHAINS'` — 合并链到激活链: merges multiple bone chains into the active bone's chain, matching bones by position index. Must select ≥2 bones, active bone on target chain.
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - Sub-conditions depend on `action` (see above)

---

## 4. 预设编辑器 (Preset Editor) — `core/editor_ops.py`

### `modder.init_editor`
- **Label**: 初始化/刷新列表
- **Params**: none
- **Preconditions**: none (operates on `context.scene.mhw_preset_editor.slots`)
- **Behavior**: Clears all slots and re-populates with 58 standard bone slots.

### `modder.pick_bone`
- **Label**: 拾取
- **Params**:
  - `slot_index` (IntProperty, required) — target slot index
  - `is_aux` (BoolProperty, default=False) — whether picking for aux bones
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - Must be in POSE or EDIT mode
  - At least one bone selected (for aux: multiple allowed; for main: active bone used)
- **Behavior**: Fills the slot with selected bone name. For `is_aux=True`, batches all selected bones as aux entries; for `is_aux=False`, uses active bone as main.

### `modder.clear_slot`
- **Label**: 清除
- **Params**:
  - `slot_index` (IntProperty, required)
  - `target` (StringProperty) — `'MAIN'` to clear main, or aux bone name to clear specific aux
- **Preconditions**: Slot must exist
- **Behavior**: Clears specified slot entry.

### `modder.mirror_mapping`
- **Label**: 镜像左侧 → 右侧
- **Params**: none
- **Preconditions**: Slots must have data in left-side entries
- **Behavior**: Mirrors left-side slot data (ending in `_L`) to right-side (`_R`) using text replacement rules: `_L_→_R_`, `_L.→_R.`, `_L→_R`, `.L→.R`, ` L → R `, `Left↔Right`, `left↔right`, `Lf↔Rt`, `(L)↔(R)`, plus regex fallback.

### `modder.save_x_preset`
- **Label**: 保存预设
- **Params**: none (reads `context.scene.mhw_preset_editor.new_preset_name` and edit_mode)
- **Preconditions**:
  - Editor must have at least one filled slot
  - `new_preset_name` must not be empty
- **Behavior**: Saves preset JSON to `assets/presets/import/` (X) or `assets/presets/bone/` (Y). If file exists, merges with existing data preserving unknown fields. Only saves filled slots.

### `modder.load_x_preset`
- **Label**: 读取预设
- **Params**: none (reads from scene settings)
- **Preconditions**:
  - A valid preset must be selected in the appropriate enum (`import_preset_enum` for X, `target_preset_enum` for Y)
- **Behavior**: Loads preset JSON into editor slots. Calls `init_editor` first to reset slots. Preserves `main[1:]` candidates.

### `modder.delete_x_preset`
- **Label**: 删除预设
- **Params**: none (shows confirmation dialog)
- **Preconditions**: A valid preset must be selected
- **Behavior**: Deletes the preset JSON file from disk and resets the enum to 'NONE'.

### `modder.open_preset_folder`
- **Label**: 打开预设文件夹
- **Params**: none
- **Preconditions**: Folder must exist
- **Behavior**: Opens OS file manager at the preset directory (`assets/presets/import/` or `assets/presets/bone/`).

### `modder.convert_preset`
- **Label**: 转换预设
- **Params**: none
- **Preconditions**: A valid preset must be selected
- **Behavior**: Copies preset from X→Y or Y→X directory, appending "(X转换)" or "(Y转换)" suffix. Updates `preset_info.type` in JSON. Skips if target file exists.

---

## 5. MHWI Tools — `games/mhwi/operators.py`

### `mhwi.align_non_physics`
- **Label**: 对齐非物理骨骼
- **Params**: none
- **Preconditions**:
  - Must select **2** ARMATURE objects (source then target)
  - `context.active_object` = target
- **Behavior**: Aligns bones by name, skipping MHWI physics bones (MhBone_/bonefunction_ with ID 150-245). Uses `bone_utils.align_armatures_by_name` with skip_fn.

### `mhwi.auto_create_chains`
- **Label**: 一键创建 Chain (CTC)
- **Params**:
  - `ctc_collection` (EnumProperty) — selected CTC collection (dynamically populated from valid collections)
- **Preconditions** (checked via `poll()`):
  - Must be in **POSE** mode
  - `context.active_object` must be an ARMATURE
  - **Requires MHW Model Editor** (`bpy.ops.mhw_ctc.create_chain_from_bone` must exist)
- **Pre-requisites for success**:
  - CTC collection must exist with a CTC_HEADER empty object
  - X preset (`import_preset_enum`) should be loaded for physics bone detection
  - Physics bones must have `chain_role` set (use `refresh_physics_bone_colors` first)
- **Behavior**: Iterates over all chain heads (bones with `chain_role in ('head', 'branch_head')`), skips already-created chains (idempotent), skips forked chains (reports them), creates CTC chains for linear chains via `bpy.ops.mhw_ctc.create_chain_from_bone()`.

### `mhwi.split_physics_bones`
- **Label**: 拆分物理骨
- **Params**:
  - `fast_mode` (EnumProperty) — `'DIRECT'` (直接重命名) or `'SPLIT'` (拆分为多个部位); only shown when total bones ≤255
- **Preconditions** (checked via `poll()`):
  - `context.active_object` must be an ARMATURE
  - Auto-loads `怪猎世界.json` X preset
- **Behavior**: Fast mode (≤255 bones): renames all physics bones to `MhBone_300~512`. Split mode: classifies physics bones by anatomical region (head/arms/torso/legs) via parent chain lookup, splits into `_body`/`_arm`/`_wst`/`_leg` armature copies, deletes non-region bones from each copy. Shows capacity table in dialog (body capacity = 255 - base bone count). After splitting, use `batch_rename_physics_bones`.

### `mhwi.batch_rename_physics_bones`
- **Label**: 一键重命名
- **Params**: none
- **Preconditions** (checked via `poll()`):
  - At least one selected object must be an ARMATURE
- **Behavior**: Batch renames physics bones across all selected armatures. `_body` skeletons use ID range 300-512. Non-body skeletons: non-tail bones → 150-200, tail bones → 201-245. Uses auto-loaded `怪猎世界.json` X preset for base bone detection.

---

## 6. MHWilds Tools — `games/mhws/operators.py`

### `mhws.endfield_face_rename`
- **Label**: Endfield 面部改名
- **Params**: none
- **Preconditions** (checked via `poll()`):
  - At least one selected object must be a MESH
- **Behavior**: Batch renames 118 Endfield face vertex groups to MHWilds naming format (Endfield → MHWilds). Processes all selected MESH objects.

### `mhws.face_weight_simplify`
- **Label**: 面部权重简化
- **Params**: none
- **Preconditions** (checked via `poll()`):
  - `context.active_object` must be a MESH
- **Behavior**: Hardcoded weight simplification for MHWilds face bones. Merges several face bone groups directly (e.g., cheek bones → Head), and does partial transfers at 60% ratio (e.g., `_A_LOD00 + _B_LOD00` → 60% to main bone, 40% retained at source). Weight cap clamped to 1.0.

### `mhws.auto_create_chains`
- **Label**: 一键创建 RE Chain
- **Params**:
  - `chain_collection` (EnumProperty) — selected Chain Collection (dynamically populated)
  - `settings_mode` (EnumProperty) — `'SEPARATE'` (各自独立) or `'SHARED'` (共享同一)
- **Preconditions** (checked via `poll()`):
  - Must be in **POSE** mode
  - `context.active_object` must be an ARMATURE
  - **Requires RE Chain Editor** (`bpy.ops.re_chain.create_chain_settings` must exist)
- **Pre-requisites for success**:
  - Chain Collection must exist with a RE_CHAIN_HEADER object
  - X preset should be loaded for physics bone detection
  - Physics bones must have `chain_role` set
- **Behavior**: Decomposes physics bone topology into linear paths via `_decompose_chains`. For SEPARATE mode: creates new Chain Settings per chain. For SHARED mode: creates one shared Chain Settings. Linear chains use default mode; branched chains use experimental mode (selects all bones in path). Calls `bpy.ops.re_chain.chain_from_bone()`.

---

## 7. RE4 Tools — `games/re4/operators.py`

### `re4.fakebone_one_click`
- **Label**: (假头法) 生成假骨骼
- **Params**:
  - `native_skeleton` (EnumProperty) — select from files in `assets/native_skeletons/re4/`
- **Preconditions**:
  - `context.active_object` must be an ARMATURE
  - **Requires RE Mesh Editor** (`bpy.ops.re_fbxskel.importfile` and `exportfile`)
  - A native fbxskel file must exist in `assets/native_skeletons/re4/`
- **Behavior**: Full FakeBone workflow (two phases):
  1. **Body phase**: Imports native skeleton as ruler, applies COPY_ROTATION constraints from user arm, visual transform apply, creates end bones, applies COPY_SCALE+COPY_LOCATION, keeps only end bones, joins into user arm, rebuilds parent hierarchy.
  2. **Finger phase**: Same constraint flow for finger bones, generates end bones with finger-initial suffixes (e.g., `_endP`, `_endI`), joins into user arm.

---

## 8. RE9 Tools — `games/re9/operators.py`

### `re9.sync_child_orientation`
- **Label**: 同步子级朝向及扭转
- **Params**: none
- **Preconditions** (checked via `poll()`):
  - `context.active_object` must be an ARMATURE
  - Must be in **EDIT_ARMATURE** mode
  - At least one bone selected; selecting both parent and descendant will error
- **Behavior**: Recursively aligns selected bones' descendants: child tail direction = parent tail direction, child roll = parent roll. Length preserved.

---

## 9. Addon Updater — `addon_updater_ops.py`

> These operators are part of the addon auto-update system. Generally not invoked by the AI agent.

| bl_idname | Class |
|-----------|-------|
| *(dynamic)* | `AddonUpdaterInstallPopup` |
| *(dynamic)* | `AddonUpdaterCheckNow` |
| *(dynamic)* | `AddonUpdaterUpdateNow` |
| *(dynamic)* | `AddonUpdaterUpdateTarget` |
| *(dynamic)* | `AddonUpdaterInstallManually` |
| *(dynamic)* | `AddonUpdaterUpdatedSuccessful` |
| *(dynamic)* | `AddonUpdaterRestoreBackup` |
| *(dynamic)* | `AddonUpdaterIgnore` |
| *(dynamic)* | `AddonUpdaterEndBackground` |

### `modder.check_updates` — `core/update_ops.py`
- **Label**: 检查更新
- **Params**: none
- **Preconditions**: Network access to GitHub raw URL
- **Behavior**: Fetches version.json from GitHub, compares remote version with local addon version.

---

## 10. Batch Export Operators

> These operators manage the multi-game batch export UI dialogs and binding system. They operate on scene-level property groups and collection pointers.

### RE9 Batch Export — `games/re9/batch_export_ui.py` + `batch_export.py`

| bl_idname | Purpose |
|-----------|---------|
| `re9.toggle_entry` | Toggle export entry enabled/disabled |
| `re9.toggle_group` | Toggle export group enabled/disabled |
| `re9.toggle_simplified` | Toggle simplified export mode |
| `re9.pick_mesh_collection` | Assign mesh collection to slot |
| `re9.pick_mdf_collection` | Assign MDF collection to slot |
| `re9.pick_sfur_collection` | Assign SFUR collection to slot |
| `re9.pick_chain2_collection` | Assign chain2 collection to slot |
| `re9.pick_clsp_collection` | Assign CLSP collection to slot |
| `re9.pick_armature` | Assign armature object |
| `re9.pick_simplified_group_mesh` | Assign simplified group mesh collection |
| `re9.pick_simplified_group_mdf` | Assign simplified group MDF collection |
| `re9.pick_simplified_group_sfur` | Assign simplified group SFUR collection |
| `re9.pick_simplified_group_chain2` | Assign simplified group chain2 collection |
| `re9.pick_simplified_group_clsp` | Assign simplified group CLSP collection |
| `re9.pick_simplified_empty_mesh` | Assign simplified empty mesh collection |
| `re9.pick_simplified_empty_mdf` | Assign simplified empty MDF collection |
| `re9.pick_simplified_empty_sfur` | Assign simplified empty SFUR collection |
| `re9.pick_simplified_empty_chain2` | Assign simplified empty chain2 collection |
| `re9.pick_simplified_empty_clsp` | Assign simplified empty CLSP collection |
| `re9.clear_simplified_group` | Clear simplified group binding |
| `re9.clear_simplified_empty` | Clear simplified empty binding |
| `re9.clear_normal_binding` | Clear normal entry binding |
| `re9.clear_all_bindings` | Clear all bindings |
| `re9.batch_export_dialog` | Open batch export dialog (invoke props dialog) |
| `re9.batch_export` | Execute batch export |
| `re9.set_natives_root` | Set Natives Root directory path |

### RE4 Batch Export — `games/re4/batch_export_ui.py` + `batch_export.py`

| bl_idname | Purpose |
|-----------|---------|
| `re4.toggle_entry` | Toggle entry |
| `re4.toggle_group` | Toggle group |
| `re4.toggle_simplified` | Toggle simplified mode |
| `re4.pick_mesh_collection` | Bind mesh collection |
| `re4.pick_mdf_collection` | Bind MDF collection |
| `re4.pick_chain_collection` | Bind chain collection |
| `re4.pick_armature` | Bind armature |
| `re4.pick_simplified_group_mesh` | Simplified group mesh bind |
| `re4.pick_simplified_group_mdf` | Simplified group MDF bind |
| `re4.pick_simplified_group_chain` | Simplified group chain bind |
| `re4.pick_simplified_empty_mesh` | Simplified empty mesh bind |
| `re4.pick_simplified_empty_mdf` | Simplified empty MDF bind |
| `re4.pick_simplified_empty_chain` | Simplified empty chain bind |
| `re4.clear_simplified_group` | Clear simplified group |
| `re4.clear_simplified_empty` | Clear simplified empty |
| `re4.batch_export_dialog` | Open batch export dialog |
| `re4.batch_export` | Execute batch export |
| `re4.set_natives_root` | Set Natives Root path |

### MHWS Batch Export — `games/mhws/batch_export_ui.py` + `batch_export.py`

| bl_idname | Purpose |
|-----------|---------|
| `mhws.pick_collection` | Bind collection to slot |
| `mhws.clear_binding` | Clear slot binding |
| `mhws.batch_export_dialog` | Open batch export dialog |
| `mhws.batch_export` | Execute batch export |
| `mhws.set_natives_root` | Set Natives Root path |
| `mhws.bonesystem_settings` | BoneSystem export settings dialog |

### MHWI Batch Export/Import — `games/mhwi/batch_export_ui.py` + `batch_export.py` + `batch_import_ui.py` + `batch_import.py`

| bl_idname | Purpose |
|-----------|---------|
| `mhwi.pick_collection` | Bind collection to slot |
| `mhwi.clear_binding` | Clear slot binding |
| `mhwi.toggle_blank` | Toggle blank model option |
| `mhwi.toggle_ccl` | Toggle CCL option |
| `mhwi.batch_export_dialog` | Open batch export dialog |
| `mhwi.batch_export` | Execute batch export |
| `mhwi.set_natives_root` | Set Natives Root path |
| `mhwi.batch_import_dialog` | Open batch import dialog |
| `mhwi.scan_import_folder` | Scan folder for importable files |
| `mhwi.toggle_import_group` | Toggle import group selection |
| `mhwi.select_import_group` | Select import group |
| `mhwi.select_all_import` | Select all import groups |
| `mhwi.batch_import` | Execute batch import |

---

## 11. MDF2 / MRL3 Material Processors

> These operators handle material texture processing for MHWS/RE4/RE9 (MDF2 format) and MHWI (MRL3 format). They extend abstract base classes from `core/mdf_tex_processor_base.py` and `core/mdf_generator_base.py`.

### MDF2 Texture Processor — per-game variants

| Game | bl_idname prefix | Dialog | Refresh | Pick PBR | Pick Direct | Clear PBR | Clear Direct | Copy Mat | Paste Mat | Process |
|------|-----------------|--------|---------|----------|-------------|-----------|--------------|----------|-----------|---------|
| MHWS | `mhws.mdf_tex_processor_dialog` | `mhws.mdf_tex_refresh` | `mhws.mdf_tex_pick_pbr` | `mhws.mdf_tex_pick_direct` | `mhws.mdf_tex_clear_pbr` | `mhws.mdf_tex_clear_direct` | `mhws.mdf_tex_copy_material` | `mhws.mdf_tex_paste_material` | `mhws.mdf_tex_process` |
| RE4 | `re4.mdf_tex_processor_dialog` | `re4.mdf_tex_refresh` | `re4.mdf_tex_pick_pbr` | `re4.mdf_tex_pick_direct` | `re4.mdf_tex_clear_pbr` | `re4.mdf_tex_clear_direct` | `re4.mdf_tex_copy_material` | `re4.mdf_tex_paste_material` | `re4.mdf_tex_process` |
| RE9 | `re9.mdf_tex_processor_dialog` | `re9.mdf_tex_refresh` | `re9.mdf_tex_pick_pbr` | `re9.mdf_tex_pick_direct` | `re9.mdf_tex_clear_pbr` | `re9.mdf_tex_clear_direct` | `re9.mdf_tex_copy_material` | `re9.mdf_tex_paste_material` | `re9.mdf_tex_process` |

### MDF2 Generator — per-game variants

| Game | Dialog | Refresh | Process |
|------|--------|---------|---------|
| MHWS | `mhws.mdf_generator_dialog` | `mhws.mdf_gen_refresh` | `mhws.mdf_gen_process` |
| RE4 | `re4.mdf_generator_dialog` | `re4.mdf_gen_refresh` | `re4.mdf_gen_process` |
| RE9 | `re9.mdf_generator_dialog` | `re9.mdf_gen_refresh` | `re9.mdf_gen_process` |

### MRL3 Processor/Generator — MHWI

| bl_idname | Purpose |
|-----------|---------|
| `mhwi.mrl3_tex_processor_dialog` | Open MRL3 texture processor dialog |
| `mhwi.mrl3_tex_refresh` | Refresh MRL3 texture state |
| `mhwi.mrl3_tex_pick_pbr` | Pick PBR input for texture slot |
| `mhwi.mrl3_tex_pick_direct` | Pick direct texture for slot |
| `mhwi.mrl3_tex_clear_pbr` | Clear PBR input |
| `mhwi.mrl3_tex_clear_direct` | Clear direct texture |
| `mhwi.mrl3_tex_copy_material` | Copy material config |
| `mhwi.mrl3_tex_paste_material` | Paste material config |
| `mhwi.mrl3_tex_process` | Process MRL3 textures |
| `mhwi.mrl3_generator_dialog` | Open MRL3 generator dialog |
| `mhwi.mrl3_gen_refresh` | Refresh MRL3 generator state |
| `mhwi.mrl3_gen_process` | Process MRL3 generation |

**Common preconditions for MDF/MRL operators**:
- Require RE Mesh Editor (`bpy.ops.re_mesh.exportfile`) for MHWS/RE4/RE9
- Require MHW Model Editor (`bpy.ops.mhw_mod3.export_mhw_mod3`) for MHWI
- Work on scene-level property groups that manage texture slot assignments
- Dialog operators open as popup windows (`invoke_props_dialog`)

---

## Appendix A: Scene Settings (Property Group)

All operators read from `context.scene.mhw_suite_settings` (type: `MHW_PT_SuiteSettings`):

| Property | Type | Purpose |
|----------|------|---------|
| `import_preset_enum` | EnumProperty | Source (X) preset selection |
| `target_preset_enum` | EnumProperty | Target (Y) preset selection |
| `pose_import_preset_enum` | EnumProperty | Pose convert skeleton preset |
| `pose_preset_enum` | EnumProperty | Pose transform record selection |
| `bone_view_mode` | EnumProperty | `'ALL'` / `'BASE'` / `'PHYSICS'` |
| `show_mapping_details` | BoolProperty | Expand mapping detail preview |

Editor properties at `context.scene.mhw_preset_editor`:
| Property | Type | Purpose |
|----------|------|---------|
| `edit_mode` | EnumProperty | `'X'` or `'Y'` |
| `slots` | CollectionProperty | 58 standard bone slots with `source_bone_name`, `aux_bones`, `std_name` |
| `new_preset_name` | StringProperty | Save-as filename |

---

## Appendix B: Preset File Locations

Relative to addon root (`<addon_dir>/assets/presets/`):

| Purpose | Path |
|---------|------|
| X presets (source) | `import/` |
| Y presets (target) | `bone/` |
| Pose transform records | `pose/` |
| RE4 native skeletons | `../native_skeletons/re4/` |

---

## Appendix C: Operator Naming Convention

| Prefix | Scope |
|--------|-------|
| `modder.*` | Core toolkit operations (pose, standard, editor, update) |
| `mhw.*` | General tools (mhw.general_tools) |
| `mhwi.*` | MHWI game-specific |
| `mhws.*` | MHWilds game-specific |
| `re4.*` | RE4 game-specific |
| `re9.*` | RE9 game-specific |
