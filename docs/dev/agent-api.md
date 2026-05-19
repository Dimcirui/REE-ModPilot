# Agent Callable API Reference

> 当前 Agent 可调用的所有工具清单。按类别分组，供开发者查阅和扩展。
> LLM 运行时不读此文件——工具 schema 由 `AgentLoop._build_tool_list()` 动态注入 system prompt。

---

## Phase Tools（17 个）

推进 `_phase_idx`，完成后触发压缩 + 暂停。

### Setup Block

| Tool | 底层操作 | 代码位置 |
|------|---------|---------|
| `SetupImportSource` | `bpy.ops.import_scene.fbx(filepath=...)` → 导入用户 FBX | `app/phases/setup.py` |
| `SetupValidateScene` | 扫描 `bpy.data.objects` → 校验场景状态 | `app/phases/setup.py` |
| `InferModelType` | 读 `armature.data.bones` → 比对 X-preset 名称 → 算覆盖率 | `app/phases/infer_model_type.py` |
| `SetupImportMHWilds` | `bpy.ops.mbt.import_mhwilds_fmesh()` → 导入 MHWs 骨架 | `app/phases/setup.py` |

### Preset Writing

| Tool | 底层操作 | 代码位置 |
|------|---------|---------|
| `PresetSupplementWrite` | 写 JSON 文件（`<base>_extended.json`），纯文件 I/O | `app/phases/preset_write.py` |
| `PresetCustomWrite` | 写 JSON 文件（`<character>_custom.json`），纯文件 I/O | `app/phases/preset_write.py` |

### Phase 1–3: Preprocessing

| Tool | 底层操作 | 代码位置 |
|------|---------|---------|
| `PoseCorrection` | `bpy.ops.pose.transforms_clear()` → 复位姿态<br>`bpy.ops.object.transform_apply(scale=True)` → 缩放对齐<br>MMD: `bpy.ops.modder.tpose_direction()`<br>终末地: `bpy.ops.modder.apply_transform_forward()`<br>VRChat: 跳过（已在 T-pose） | `app/phases/pose_correction.py` |
| `SkeletonAlign` | `bpy.ops.modder.universal_snap()` → X+Y 双预设骨骼对齐 | `app/phases/skeleton_align.py` |
| `VertexGroups` | `bpy.ops.object.join()` → 合并网格<br>`bpy.ops.object.vertex_group_clean(limit=0.0)` → 清零权重<br>`bpy.ops.object.vertex_group_normalize_all()` → 归一化<br>`bpy.ops.modder.direct_convert()` → 顶点组重命名<br>`bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')` → 重设父级 | `app/phases/vertex_groups.py` |

### Phase 3.5: Physics Transplant

| Tool | 底层操作 | 代码位置 |
|------|---------|---------|
| `PhysicsTransplant` | `bpy.ops.modder.smart_graft()` → 将源骨架的物理骨移植到 MHWs 骨架<br>`bpy.ops.modder.refresh_physics_bone_colors()` → 刷新骨骼颜色 | `app/phases/physics_bones.py` |

### Phase 4A–4B: Physics Chains

| Tool | 底层操作 | 代码位置 |
|------|---------|---------|
| `PhysicsClassification` | `bpy.ops.modder.refresh_physics_bone_colors()` → 标记链角色 | `app/phases/physics_bones.py` |
| `PhysicsChains` | `bpy.ops.modder.clear_chain_role()` → 清除链标记<br>`bpy.ops.modder.merge_into_parent()` → 合并到父骨<br>`bpy.ops.re_chain.create_chain_header(chainFormat='.chain2')` → 创建 chain2 集合<br>`bpy.ops.mhws.auto_create_chains(settings_mode=...)` → 自动创建链结构<br>`bpy.ops.re_chain.create_chain_settings()` → 创建额外 Settings<br>`bpy.ops.re_chain.apply_angle_limit_ramp(maxAngleLimit=1.047198, maxIteration=4)` → 角度限制渐变 | `app/phases/physics_bones.py` |
| `PhysicsAdjust` | `setattr(pg, key, val)` → 直接设置 CHAINSETTINGS 属性组参数（无 bpy.ops） | `app/phases/physics_bones.py` |

### Phase 5: Material

| Tool | 底层操作 | 代码位置 |
|------|---------|---------|
| `MaterialConsolidate` | `mesh.materials.pop(index)` + `poly.material_index` → 合并重复材质（直接 API） | `app/phases/material.py` |
| `MaterialInspect` | 遍历 `node_tree.nodes` + `node_tree.links` → 分析材质连接（直接 API，只读） | `app/phases/material.py` |
| `MaterialSetup` | `nodes.new()` + `links.new()` → 创建/连接 Principled BSDF 节点（直接 API） | `app/phases/material.py` |
| `MaterialGenerate` | `bpy.ops.mhws.mdf_gen_refresh()` → 预烘焙<br>`bpy.ops.mhws.mdf_gen_process()` → 生成 .mdf2 + .tex | `app/phases/material.py` |

### Phase 6: Export

| Tool | 底层操作 | 代码位置 |
|------|---------|---------|
| `BatchExport` | `bpy.ops.re_mesh.delete_loose()` — 清理游离几何<br>`bpy.ops.re_mesh.solve_repeated_uvs()` — 去重 UV<br>`bpy.ops.re_mesh.remove_zero_weight_vertex_groups()` — 删空权重组<br>`bpy.ops.re_mesh.limit_total_normalize(maxWeights='12')` — 权重上限<br>后备: `bpy.ops.object.vertex_group_limit_total(limit=12)` + `bpy.ops.object.vertex_group_normalize_all()`<br>`bpy.ops.mhws.batch_export()` → 输出 mesh + mdf2 + chain2 | `app/phases/batch_export.py` |

---

## Query Tools（10 个）

只读，不推进 `_phase_idx`。连续 2 轮纯查询后限制为仅 phase tools。

| Tool | 底层操作 | 代码位置 |
|------|---------|---------|
| `SceneInfo` | 扫描 `bpy.data.objects`、`bpy.context.mode` | `app/phases/query_tools.py` |
| `ListObjects` | 枚举 `bpy.data.objects`（名称、类型、可见性） | `app/phases/query_tools.py` |
| `GetBoneInfo` | 读 `armature.data.bones`（名称、头/尾坐标、父级、custom_properties） | `app/phases/query_tools.py` |
| `ListCollections` | 枚举 `bpy.data.collections`（名称、子对象列表） | `app/phases/query_tools.py` |
| `GetMeshInfo` | 读网格 `vertices`、`polygons`、`vertex_groups`、`materials` | `app/phases/query_tools.py` |
| `GetMaterialProps` | 读材质 `node_tree` 属性 | `app/phases/query_tools.py` |
| `GetObjectProps` | 读对象的 `location`、`rotation_euler`、`scale`、`hide_get()` | `app/phases/query_tools.py` |
| `InspectMaterialNodes` | 读材质完整节点树（全部节点 + 连接） | `app/phases/query_tools.py` |
| `ListMdfPresets` | 读 `mhws.mdf_presets` 枚举 | `app/phases/query_tools.py` |
| `PhysicsRead` | 读 CHAINSETTINGS 属性组参数 | `app/phases/physics_bones.py` |

---

## Meta Tools（2 个）

不调用 Blender，由 `AgentLoop._execute_tool_call()` 内部处理。

| Tool | 作用 | 代码位置 |
|------|------|---------|
| `sync_phase_state` | 更新前端进度条（断线重连/会话恢复时用） | `app/agent/loop.py` |
| `query_history` | 翻阅磁盘 MoveLog，按 phase/kind/name 过滤 | `app/agent/history.py` |

---

## 未直接暴露的底层 API

以下操作被 Phase Tools 内部调用，但 LLM **不能**直接调用。如需扩展，可通过包装为新 PhaseTool 来开放：

| 类别 | 算子 |
|------|------|
| Modding-Toolkit 姿态 | `modder.tpose_direction`, `modder.apply_transform_forward` |
| Modding-Toolkit 对齐 | `modder.universal_snap`, `modder.smart_graft` |
| Modding-Toolkit 骨骼 | `modder.direct_convert`, `modder.clear_chain_role`, `modder.merge_into_parent`, `modder.refresh_physics_bone_colors` |
| MHWs 管线 | `mbt.import_mhwilds_fmesh`, `mhws.auto_create_chains`, `mhws.mdf_gen_refresh`, `mhws.mdf_gen_process`, `mhws.batch_export` |
| RE Chain | `re_chain.create_chain_header`, `re_chain.create_chain_settings`, `re_chain.apply_angle_limit_ramp` |
| RE Mesh | `re_mesh.delete_loose`, `re_mesh.solve_repeated_uvs`, `re_mesh.remove_zero_weight_vertex_groups`, `re_mesh.limit_total_normalize` |
| Blender 基础 | `bpy.ops.object.join`, `bpy.ops.object.transform_apply`, `bpy.ops.pose.transforms_clear` |
| 直接 API（无 bpy.ops） | `nodes.new()`、`links.new()`、`setattr(pg, ...)` |
