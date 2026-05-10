# E2E Testing — Bug Fixes & Workflow Discoveries

Live-testing notes from running the full pipeline end-to-end.
Each session section records: what broke, what was changed, and any workflow knowledge added to docs.

---

## Session 1 — 2026-05-11 (Phases setup → 4A)

### Environment / Provider

| Fix | Files |
|-----|-------|
| `.env` had `LLM_PROVIDER=anthropic` pointing at DeepSeek base URL → 404. Fixed by switching to `openai_compatible`. | `.env` (user-side) |
| DeepSeek V4-flash returns `thinking` content blocks; `_build_assistant_tool_msg` stripped them, causing 400 on next call. Added `content_blocks` field to `LLMResponse`; Anthropic provider collects all block types; assistant messages round-trip verbatim. | `app/llm/client.py`, `app/llm/anthropic_provider.py`, `app/agent/loop.py` |

### Agent Loop

| Fix | Files |
|-----|-------|
| Chinese error-inquiry phrases (e.g. "可以告诉我具体是哪里出了错误吗？") fell through to `"unknown"` in `parse_user_choice`. Expanded keyword list. | `app/agent/error_handler.py` |
| Chinese retry phrases ("你再尝试尝试", "继续吧") not recognized. Added `"尝试"`, `"重新"`, `"再来"`, `"继续"` to retry keywords. | `app/agent/error_handler.py` |
| Agent replied in English despite CLAUDE.md language policy. Added explicit `LANGUAGE RULE` block to `build_system_prompt`; Chinese instruction added to `_ERROR_SYSTEM` and `build_error_prompt`; error option labels changed to `[Retry] — 重新执行 \| [Skip] — 跳过继续 \| [Ask] — 查看详情`. | `app/agent/prompts.py`, `app/agent/error_handler.py` |
| `phase_35`, `phase_4a`, `phase_4b` were all in `_NEGOTIATING_PHASES`; LLM had no tools available, told users to operate Blender manually. Moved all three to RUNNING_PHASE. | `app/agent/loop.py` |
| `PhysicsTransplant`, `PhysicsClassification`, `PhysicsChains` not registered; LLM could not call them. Registered all three. | `app/agent/loop.py` |
| AWAIT_CONFIRM → NEGOTIATING transition meant user confirmations never triggered tool calls. Changed `_handle_await_confirm` to transition to RUNNING_PHASE instead. | `app/agent/loop.py` |
| `propose_and_confirm` proposal detection only existed in `_run_negotiating_turn`; proposals emitted during RUNNING_PHASE (Phase 4B chain classification) never set AWAIT_CONFIRM. Added detection to `_run_react_turn`. | `app/agent/loop.py` |
| DeepSeek raw tool-call markup (`<｜｜DSML｜｜tool_calls>`) leaked into NEGOTIATING replies when no tools were provided. Added `_RAW_TOOL_CALL_RE` strip in `_run_negotiating_turn`. | `app/agent/loop.py` |
| Agent asked user for `target_armature` name every phase instead of using the known fixed value `"MHWilds_Female Armature"`. Added explicit fixed-value instruction to tool schema descriptions in Phase 1 and Phase 2. | `app/phases/pose_correction.py`, `app/phases/skeleton_align.py` |

### Phase 1 — Pose Correction

| Fix | Files |
|-----|-------|
| Scale alignment computed `z_max` from `matrix_world` (which already includes unapplied scale), then `src_arm.scale = (ratio, ...)` overwrote the existing unapplied scale instead of composing. Pre-step now applies all transforms before computing heights. | `app/phases/pose_correction.py` |
| Preset enum values sent to Blender were bare names (`"MMD"`) instead of filenames (`"MMD.json"`). Fixed `.json` suffix on all 5 preset enum assignments across 3 phase files. | `app/phases/pose_correction.py`, `app/phases/skeleton_align.py`, `app/phases/vertex_groups.py` |

### Phase 3 — Vertex Groups

| Fix | Files |
|-----|-------|
| `lookup_lines` string had 4-space indent on top-level code, causing `unexpected indent` in Blender. Removed leading spaces from f-string lines. | `app/phases/vertex_groups.py` |
| After reparenting merged mesh to MHWilds armature, mesh remained in the default collection instead of `MHWilds_Female.mesh`. Added collection unlink/link step in `_reparent_to_target`. | `app/phases/vertex_groups.py` |
| MHWilds reference meshes (imported with the skeleton for height measurement) were never cleaned up. Added `_delete_mhwilds_reference_meshes` called after all three pipeline steps complete; excludes the merged source mesh by name. | `app/phases/vertex_groups.py` |

### Phase 3.5 — Physics Transplant

| Fix | Files |
|-----|-------|
| `settings.import_preset_enum` / `target_preset_enum` missing `.json` suffix. Fixed. | `app/phases/physics_bones.py` |
| After transplant, source armature was not hidden and X preset was not switched to MHWilds (怪猎荒野). Added hide + preset switch at end of `_run_smart_graft` when operator FINISHED. (Color refresh step removed — Phase 4A handles it.) | `app/phases/physics_bones.py` |

### Phase 4A — Physics Classification

| Fix | Files |
|-----|-------|
| `PhysicsClassification.tool_schema` had `x_preset` enum `[MMD, VRChat, 终末地]`; after Phase 3.5 X preset is always MHWilds — source preset enum is wrong here. Removed `x_preset` from Phase 4A and Phase 4B schemas; `import_preset_enum` hardcoded to `'怪猎荒野.json'` in both. | `app/phases/physics_bones.py` |
| `modder.refresh_physics_bone_colors` failure returned opaque `"Blender error"` with no detail. Wrapped operator call and bone-traversal loop in separate `try/except`; errors now surface as `REFRESH_ERR:` / `CHAIN_ERR:` with traceback in `PhaseError.raw`. Mode changed from POSE to OBJECT before switching to POSE for operator call. | `app/phases/physics_bones.py` |

### Workflow Knowledge Added to Docs

| Addition | File |
|----------|------|
| Setup Phase section; Central Collection doctrine (`MHWilds_Female.mesh`); Phase 1-3 entry conditions; VRChat base body keyword list (infer+confirm, not auto-proceed). | `docs/agent_workflow.md` |
| Phase 4A: `_HJ_` bones → silent ignore rule (no user prompt). | `docs/agent_workflow.md` |
| Phase 4A: `*_root` shared-root-bone pattern — inform user, ask which to merge via `modder.merge_into_parent`. | `docs/agent_workflow.md` |
| Phase 4A Execution Steps: operator name `modder.merge_into_parent`, preconditions, auto-refresh behaviour. | `docs/agent_workflow.md` |

---

## Session 2 — 2026-05-11 (Phase 4A → 4B, continued)

### Agent Loop

| Fix | Files |
|-----|-------|
| DeepSeek DSML markup (`<｜｜DSML｜｜tool_calls>`) leaked into ASK_MODE reply because the regex used for stripping failed on invisible Unicode char differences in DeepSeek V4 output. Added plain-string `str.find()` fallback in `_parse_dsml_tool_calls`; extracted `_strip_dsml_block()` helper with two-layer strip (regex + greedy truncation) used in all 3 markup-strip call sites. | `app/agent/loop.py` |
| Agent stuck in "max tool-call rounds" loop: 8 rounds not enough for Phase 4A classification (queries all chain heads one by one). Increased `_MAX_TOOL_ROUNDS` 8→15. Added query-tool throttle: after 2 consecutive query-only rounds, `_run_react_turn` restricts tool list to phase tools only, forcing the LLM to move to action. Added `QueryTool` base class and `_build_phase_only_tool_list()`. | `app/agent/loop.py`, `app/phases/query_tools.py` |
| ERROR_HANDLING → ASK_MODE deadloop: user typing "退出ask模式" was classified as "ask" by LLM (returning to ASK_MODE), never escaping. Three-part fix: (1) added "退出" to `exit_keywords` in `_handle_ask_mode`; (2) `parse_user_choice` no longer treats LLM "unknown" as final — falls through to keyword matching; (3) "退出" added to retry keywords in `_keyword_fallback`. | `app/agent/loop.py`, `app/agent/error_handler.py` |

### Phase 4A — Physics Bone Classification

| Fix | Files |
|-----|-------|
| `bpy.ops.object.mode_set.poll() 上下文缺失活动物体` crash: every code snippet called `mode_set(mode='OBJECT')` before setting `bpy.context.view_layer.objects.active`. Fixed in all 7 affected call sites across `_run_smart_graft`, `_inspect_and_refresh`, `_clear_and_refresh_chain_roles`, `_merge_into_parents`, `_create_chains` (both auto-create and main paths), `_consolidate_chain_settings`, `_apply_angle_limit_ramp`. Pattern: set active object BEFORE calling mode_set. | `app/phases/physics_bones.py` |

### Phase 4B — Physics Chain Creation

| Fix | Files |
|-----|-------|
| 212 spurious CHAINGROUPs created (including for all body bones): root cause was stale `chain_role` marks from a previous session. `refresh_physics_bone_colors` cannot clear existing `chain_role` — those must be explicitly cleared first. Added `prepare_only=True` flow to `physics_chains`: clears ALL bone chain_roles + resets color palettes + calls refresh; returns immediately for user to verify colors before chain creation. | `app/phases/physics_bones.py` |
| Native game bones (e.g. `Cage`, `Cage_L`) accidentally marked with `chain_role` by `refresh_physics_bone_colors`: they should not be physics. Added `bones_to_clear` parameter to `physics_chains`: selects specified bones in POSE mode and calls `modder.clear_chain_role`, without deleting or merging them. Runs before `bones_to_merge` and chain creation. | `app/phases/physics_bones.py` |
| SHARED+consolidation approach for chain settings too complex and fragile (CHAINNODE property names uncertain). Reverted `auto_create_chains` to `settings_mode='SEPARATE'` (one CS per chain head). `run()` now branches: SEPARATE uses `_apply_params_to_chain_settings` directly; SHARED retains consolidation path for future use. `_create_chains` accepts `settings_mode` param. | `app/phases/physics_bones.py` |

| `prepare_only` flow lacked auto-verification: agent had to ask user if colors looked right before proceeding. Added `_verify_chain_marks()` to `PhysicsChains`: after cleanup, checks every `chain_role='head'/'branch_head'` bone for an `_End` descendant (iterative BFS). Body bones marked as chain heads have no `_End` descendants — auto-detected as suspicious. On failure, retries cleanup once more; returns `marks_clean: bool` in state diff. Agent can proceed to chain creation automatically when `marks_clean=True`. | `app/phases/physics_bones.py`, `tests/unit/test_physics_bones.py` |

### Docs

| Addition | File |
|----------|------|
| Phase 4B: Pre-creation cleanup step (prepare_only); `bones_to_clear` usage for native game bones; updated execution steps for SEPARATE mode. | `docs/agent_workflow.md` |
| Phase 4A: `bones_to_clear` vs `bones_to_merge` distinction added to execution steps. | `docs/agent_workflow.md` |
| `modder.clear_chain_role`: corrected behavior note — operator does NOT reset bone colors; usage guidance added. | `docs/plugin_api.md` |
