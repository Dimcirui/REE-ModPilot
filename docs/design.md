# REE-ModPilot 设计讨论文档

> **本文档目的**：在动工之前把未决的产品/架构/技术决策摊开，按依赖顺序逐题讨论。
> 每题给出**选项 + 取舍 + 初步倾向**，不预设结论。
> 决定一题就更新顶部状态表 + 该题的"决议"区块，再往下推进。

---

## 决策状态总览

| # | 主题 | 层 | 状态 | 决议摘要 |
|---|------|----|------|---------|
| A1 | AI 角色边界（自动 / 步进 / 助手） | 产品 | 🟢 已决议 | 方案 b 步进式向导；MVP 来源限定成体系模型 |
| A2 | 目标用户起点假设 | 产品 | 🟢 已决议 | 档位 2（Blender 入门）；门槛即过滤；讲解懒加载；内部维护先决条件清单不对外列 |
| A3 | "30 分钟"口径 | 产品 | 🟢 已决议 | 30min 营销锚 / 1h 工程目标；MVP 扩到视频 1-7 |
| A4 | MVP 验收用例 | 产品 | 🟢 已决议 | L3 验收 / MHWs 单游戏 / 用户自备 MMD 素材 / 信号 = 导出无严重报错 + 进游戏目检 |
| B5 | Agent 如何感知 Blender 状态 | 架构 | 🟢 已决议 | 混合：Agent 维护轻量 cache + phase tool 入口自动 spot-check + 调用结束更新 cache |
| B6 | 工具粒度 | 架构 | 🟢 已决议 | 中层 phase tool（~12-15 个，对齐 plan.md 环节）；phase 内含混合分类（高置信自动 / 低置信用户拍板） |
| B7 | 错误恢复策略 | 架构 | 🟢 已决议 | 结构化 error + LLM 措辞 + 重试/跳过/求助；不做回滚（依赖 Blender undo）；关键 phase 边界做 sanity check |
| B8 | 跨会话状态续传 | 架构 | 🟢 已决议 | MVP 不做；新会话用 get_scene_info 重建 cache 即可 |
| C9 | Agent 框架选型 | 技术 | 🟢 已决议 | 路径 A：MVP 用原生 SDK + 手写 ReAct；MVP 后用 LangGraph 重写练手 |
| C10 | LLM 选型 | 技术 | 🟢 已决议 | 双轨制 + provider 抽象；开发期默认 DeepSeek V4；Sonnet/Haiku 作 oracle/fallback |
| C11 | RAG 是否需要 | 技术 | 🟢 已决议 | MVP 不上 RAG；内容 RAG（非 tool RAG）作为未来备选 |
| C12 | 前端形态 / 技术栈 | 技术 | 🟢 已决议 | htmx + 极简 HTML/CSS；FastAPI 直接渲染；不上前端框架 |
| C13 | Python 依赖管理 | 技术 | 🟢 已决议 | uv（速度 + 现代 lockfile + 同时管 Python 版本与 venv） |
| D14 | 目录结构 & 测试策略 | 工程 | 🟢 已决议 | ModPilot/app 模块化（blender / llm / agent / phases / routes / templates）+ unit/integration 双层 pytest |
| D15 | 测试素材集 | 工程 | 🟢 已决议 | repo 不附带；docs/demo_setup.md 列推荐素材；用户首次跑下载到 ~/.modpilot_assets/ |
| E16 | PhaseResult 形状 | 实现 | 🟢 已决议 | 轻量三字段：success / state_diff / error；user_message 由 agent loop 生成，不内嵌在 phase |
| E17 | 分类决策位置 | 实现 | 🟢 已决议 | 分类在 agent loop，phase tool 只管执行；避免每个 phase 重复写分类逻辑 |
| E18 | 同步 BlenderClient 在异步 FastAPI 中的调用方式 | 实现 | 🟢 已决议 | asyncio.to_thread()；BlenderClient 保持同步；单用户工具无并发压力 |

图例：⚪ 未启动 / 🟡 讨论中 / 🟢 已决议 / 🔴 阻塞

**讨论顺序建议**：A1 → A2 → A3 → A4 → B5/B6（耦合，一起讨论）→ B7 → B8 → C → D。
A 层是一切的总开关，**强烈建议从 A1 开始**。

---

# A 层：产品 / 用户体验

> 决定整个项目的形态。B、C 层全部由 A 层驱动。

---

## A1. AI 在流程里扮演什么角色？

### 背景

「AI Agent + Blender 自动化」可以落到三种完全不同的产品形态上。CLAUDE.md
现在没有明确选哪种。

### 三个候选

**方案 a — 全自动（batch mode）**
> 用户丢素材 + 选目标游戏 → Agent 跑完视频 1-3 → 输出可导出的 Blender 文件。

- 优点：体验最"AI"，30 分钟达成最容易。
- 缺点：**几乎不可行**。
  - 来源模型千差万别（VRChat / MMD / 自制 / 各种命名规范），
    选 X 预设这一步就需要人类经验判断。
  - 物理骨怎么处理（路线 A 还是 B）是审美决策，不是流程决策。
  - 出错时 Agent 没有人类视觉反馈，黑盒重试只会越错越远。
- 适用：**未来阶段**，等积累足够素材规则后可能可行；MVP 不现实。

**方案 b — 步进式向导（guided wizard）**
> Agent 把 plan.md 里的环节切成步骤，每步：
> 1. 解释这步要干什么 + 为什么
> 2. 必要时调工具自动执行
> 3. 让用户在 Blender 里看效果，回一句"OK"或"有问题：……"
> 4. 进入下一步或排错

- 优点：
  - 完美贴合"新手"定位 —— 既教学又自动化。
  - 错误恢复天然（用户每步确认）。
  - 工具层也好做，每个工具职责明确。
- 缺点：
  - 节奏比"全自动"慢，用户每步要回应。
  - ReAct 的"自主推理"价值打折，更像"流程引擎 + LLM 解释器"。

**方案 c — 助手 / 答疑（passive copilot）**
> 用户主导操作，遇到不懂的地方问 Agent，Agent 答疑或按需调用工具。

- 优点：实现最简单。
- 缺点：
  - **没体现 Agent 价值**，沦为"plugin_api 文档检索 + 偶尔一键操作"。
  - 30 分钟目标基本达不到（用户自己点就够了）。
  - 不如直接做插件内嵌的 chatbot。

### 我的倾向

**方案 b（步进式向导）为主，在某些纯机械环节内嵌微批量**。例如视频 3 的
"重命名顶点组"在用户确认了 X/Y 预设之后就是一次 `direct_convert` 调用，
不需要再交互；但视频 1 的"选哪种姿态修正工具"必须 Agent 引导用户判断
（A-Pose / 复杂姿态 / RE 引擎专用）。

关键设计决定会因此被锁定：
- UI 是**对话流 + 步骤进度条 + Blender 截图侧栏**，不是纯聊天。
- Agent 的 prompt 是"流程驱动"而非"自由探索"，每个步骤有 enter/exit 条件。
- 工具粒度倾向于**中层封装**（每个工具对应一个步骤），不是 28 个低层 op。

### 决议 🟢（2026-05-08）

**选定方案 b（步进式向导）。**

合并双方的排除理由：
- **方案 a 排除**：(1) 实现复杂度高，短期内做不到；(2) 需要大量行业专精
  语料，目前没有；(3) 出错反复试错，新手无法纠正，会卡死；(4) 不少步骤
  涉及审美决策（如物理骨路线 A/B 选择），不是全自动能给最优解。
- **方案 c 排除**：(1) 同样需要大量行业语料；(2) 做到最后退化成 QA RAG，
  体现不出 Agent 能力。
- **方案 b 选中**：原型阶段实现成本可控；针对"成体系模型"能做出有
  说服力的效果。

**附加范围约束（重要，影响后续多题）**：

> MVP 原型只针对**成体系模型作为来源**：MMD 模型、VRChat / Unity Humanoid
> 模型、以及特定游戏的成体系模型。**不接受任意 FBX**。

这条约束的下游影响：
- **A2 目标用户**：用户至少能识别自己的模型属于哪一类（VRC / MMD / 其他），
  对 Blender 不会完全零基础。
- **A4 MVP 用例**：来源模型必须从这些"成体系"集合中选定。
- **B6 工具粒度**：X 预设候选集合很小（~3-5 个），可以在流程开头让
  Agent 先做一次"模型类型路由"决策，简化后续工具调用。
- **C11 RAG**：进一步降低 RAG 必要性 —— 模型类型有限 → 流程分支可枚举 →
  适合写进 system prompt 而非检索。

**留的口子**：
- 工具层接口设计**不**把"成体系模型"硬编码进去；只是在 prompt / Agent
  流程层先聚焦这几类，工具底层保持通用。后期支持任意来源时不用重构。
- 方案 c（助手 / 答疑）未来如果真有需求，可作为方案 b 内嵌的"自由问答"
  侧支并存，不必全盘重写。

---

## A2. 目标用户的起点在哪？

### 背景

"新手"是一个滑动尺度。目标用户的 Blender 基础决定了 Agent 要不要包揽
"打开文件"、"切到物体模式"、"在大纲里找对象"这种基础 UI 操作。

### 三档候选

**档位 1 — 完全零基础**
> 没用过 Blender，不会装插件，不知道大纲是什么。

- Agent 要包揽：Blender 安装指引、插件安装、UI 操作教学、基础术语
  （骨架 / 网格 / 顶点组 / 模式）。
- 工作量翻倍，30 分钟完全不可能。

**档位 2 — Blender 入门**（我倾向这档）
> 装过 Blender、能导入 FBX/PMX、知道大纲选对象、会切编辑/姿态模式，
> 但完全不懂 RE Engine 模型结构、不懂骨骼对齐、不懂顶点组。

- Agent 教什么：Modding-Toolkit 的概念（X/Y 预设、标准骨骼、辅助骨）、
  每个步骤的"为什么"、出错时怎么解读。
- Agent 不教：基础 Blender UI（"按 N 打开侧边栏"这种不教）。

**档位 3 — Blender 老手 + 想做 mod 的小白**
> 熟练 Blender，只是没碰过 RE Engine modding。

- Agent 退化为"流程加速器"，主要价值是自动跑 Modding-Toolkit 的链式调用，
  而非教学。

### 我的倾向

**档位 2**。理由：

- 能装 blender-mcp 和 Modding-Toolkit 已经过滤掉了档位 1（这一步本身
  就要懂 add-on 安装）。
- plan.md 整体的讲解深度（解释姿态修正、X/Y 预设原理）也是冲档位 2 写的。
- 包揽档位 1 内容会把 Agent 拖成"Blender 教程网站"，跑题。

### 决议 🟢（2026-05-08）

**选定档位 2（Blender 入门）。** 但理由不只是"能力分档"，而是产品哲学：

**门槛即过滤机制**（用户提出，作为核心理由）：
> 给目标用户设定门槛是**主动选择**，不是无奈。愿意跨过门槛的人才是真心
> 想学习 / 热爱游戏的，过滤掉别有用心的群体。"用 AI 帮你做 mod"这个概念
> 自然会吸引愿意学的人；用户面本身就窄，扩大不一定是好事。

衍生子决议：

1. **讲解懒加载（lazy explanation）**——影响 prompt 风格 + UI 文本密度。
   - 默认 Agent **不主动**深度讲解（不主动讲"为什么先修姿态再对骨架"
     这种背后原理）。
   - 用户**遇到错误**时，把错误响应当作讲解 hook，质量要做高。
   - 上来就主动问深度问题的用户极少，按少数情况处理。
   - 实际归位档位介于 2.5 和 3 之间：不是档位 2 那种"事无巨细教学"，
     更接近档位 3 的"流程加速器 + 错误时的高质量答疑"。

2. **先决条件清单：不对外列出，内部维护一份**。
   - 对外不放在产品着陆页 —— 列清单本身会劝退潜在用户。
   - 对内必须有 —— 它定义了"我们假设用户已经会的东西"，是 prompt 工程
     和 UI 文案的隐性边界。
   - 初稿见本文档 [附录 P：先决条件清单（内部）](#附录-p先决条件清单内部)。

3. **没列出来不等于不写**：着陆页/首屏说明里**仍要让用户隐性识别**
   "这个工具不是给完全零基础的人用的"，避免错误自我归类。具体怎么写
   留给 C12 前端议题。

**留的口子**：清单未来如果要分发（如打包给社区/教学），可以从内部清单
派生出对外版本，加上"如果你不熟下面这些，建议先看 X"的引导。MVP 不做。

---

## A3. "30 分钟"具体怎么算？

### 背景

CLAUDE.md 写"将制作时间从数小时压缩到 30 分钟以内"。这条目标到底从哪
开始计时、到哪算结束，直接影响 MVP 边界。

### 几种口径

| 口径 | 起点 | 终点 | 含意 |
|------|------|------|------|
| 严格 | 用户在 ModPilot 里说"我要做 mod" | mod 在游戏里跑起来 | 包含视频 1-7 全流程 + 游戏内验证；MVP 内不可能 |
| 宽松 | 同上 | Blender 里"理论上可导出" | 视频 1-3 完成、模型骨架顶点组都对齐了；MVP 现实目标 |
| 工具时间 | 同上 | 同上 | 但**只算 Agent + 工具调用时间**，用户思考/检查时间不算 |

### 我的倾向

**宽松口径作为 MVP 的 30 分钟目标**。完成视频 1-3 = 模型在 Blender 里
"骨架对齐 + 顶点组转换"完毕，可以进入后面的物理/材质/导出阶段。
游戏内运行属于视频 4-6 的范围，不在 MVP 内。

但这个目标要不要明确写进 README / 设计文档作为 MVP 验收标准？

### 决议 🟢（2026-05-08）

**两条独立的时间锚点**：

| 用途 | 数字 | 性质 |
|------|------|------|
| 对外宣传 | 30 分钟 | 营销锚点。让用户感觉值，**不必死磕**。落不到也不算翻车。 |
| 对内 MVP | 1 小时 | 工程目标。针对**视频 1-7 全流程**的实际 demo 跑通时长。 |

**底线参考（用户实战数据）**：
- 作者本人极限纪录：6 分钟（除去加载/硬件等待）
- mod 熟手 + Modding-Toolkit：1-2 小时出**优质**mod
- 基础质量（不优化穿模等）：半小时可达
- 这些数字证明 1 小时工程目标"实现不难"，不需要为了达标做激进性能优化。

**关键副产品 — MVP 范围被重新定义**：

> 原 CLAUDE.md / 旧 A4 提案设的"MVP = 视频 1-3"被用户驳回。
> 新 MVP 范围 = **视频 1-7 全流程**。

理由：
- 视频 1-3 内容门槛极低，入门 mod 作者 ~5 分钟就能完成（点几下工具就行）。
- AI 真正的价值在视频 4-7 的"经验类分类判断"：物理骨 / 身体骨按命名识别、
  PBR 通道映射、装备包槽位路由等。
- 这类判断与 A1 方案 a 排除的"深度行业经验"**不一样**——它们对基础 AI
  也好做（命名差异明显、分类边界清晰），准确率有保证。
- 砍到 1-3 反而做不出"AI Agent"的差异化价值，沦为工具链 wrapper。

**下游影响**：
- A4 验收用例需重新设计（验收链路变长）。
- B6 工具粒度需覆盖更多 Operator（包括 MDF2、批量导出、各游戏专用工具）。
- B7 错误恢复要处理更多失败场景（材质转换失败、依赖插件未装等）。
- C12 前端要承载更多步骤的状态（不只是骨架对齐的进度条）。

**留的口子**：
- 工程目标 1 小时不是硬指标。如果排期吃紧，先保 1-7 跑通再优化时长。
- "AI 在 4-7 做分类判断"这一假设若实测失败（比如分类准确率低于预期），
  退路是把这些步骤降级为"Agent 给候选选项 + 用户拍板"。

---

## A4. MVP 验收用例长什么样？

### 背景

没有具体场景就没法判断"做完了"。需要锁定**一个**端到端的可重复用例
作为 MVP 的验收标准。后续扩展是锦上添花。

### 我的倾向（A3 决议后重写——范围已扩到视频 1-7）

锁定**一个**端到端用例：

- **来源模型**：一个**带物理骨**的 VRChat 标准女角色（含裙子或长发）。
  *（带物理骨是关键——这样视频 4 物理骨路线 B 才有演示价值；
  否则视频 4 只能演示路线 A 的"全砍"。）*
- **目标游戏**：**MHWs（怪猎荒野）一个游戏深度打通**。
  理由：plan.md 在 MHWs 上举例最完整；多游戏并行会让 MVP 工作量爆炸。
  其他游戏（MHWI / RE4 / RE9）留作 MVP 后扩展。
- **目标骨架**：MHWs 标准角色（如 ch03_000_9000）。
- **范围**：视频 1-7 全流程。
- **验收线**（待你定，三档可选）：
  - **L1（弱）**：Agent 引导下，所有 Operator 都能调起来不报错；
    最终在 Blender 里"看起来对了"。**不**实际导出文件。
  - **L2（中）**：成功导出 mesh / mdf2 / chain2 / clsp 等 mod 文件到磁盘；
    `re_mesh.exportfile` 等导出 Operator 全成功。
  - **L3（强）**：导出文件实际放进游戏，能进游戏看到 mod 角色（不闪退、
    不全黑、骨骼不抽搐）。
- **目标时长**：1 小时跑完（A3 工程目标）。
- **谁跑**：你跑。MVP 不找外部用户。

### 待讨论

1. **验收线选哪档？** L1 最容易但说服力弱；L3 最有说服力但需要游戏测试，
   每次验收成本高，且会引入很多"游戏配置 / 解包工具"等无关变量。
   我倾向 **L2**——文件导出成功且无 RE Mesh Editor 报错，作为 MVP 通过；
   L3 当作"额外里程碑"非阻塞。
2. **来源模型怎么准备？**
   - 你手上有现成的 VRC 模型可以拿来当 demo 吗（带物理骨那种）？
   - 还是需要现找一个公开授权可分发的？
   - 这个素材最终要不要进 repo（就放 `Modding-Toolkit/test_assets/` 之类）？
3. **MHWs 之外的游戏**：MVP 完全不碰，还是某些环节用其他游戏 demo
   做"附加证明点"（比如 RE4 假头法只能在 RE4 演示）？我倾向**完全不碰**。
4. **"成功"判定的具体可观测信号**——以 L2 为例：
   - mesh / mdf / chain 等关键文件存在于 Natives 目录
   - 文件不是 0 字节
   - 关键导出器返回 `{'FINISHED'}`（vs `{'CANCELLED'}`）
   - 这些信号是不是要进自动化检查脚本（类似 `verify_blender_mcp.py`
     的 stage 0）？做一个 stage_mvp.py？

### 决议 🟢（2026-05-08）

**MVP 单一端到端用例锁定**：

| 维度 | 决定 |
|------|------|
| 流程范围 | plan.md 视频 1-7 全流程（A3 决议） |
| 目标游戏 | **MHWs（怪猎荒野）单游戏** |
| 来源模型 | **用户自备**，**MMD 优先 / VRChat 次之** |
| 验收线 | **L3（实际进游戏跑）** |
| 时长目标 | 1 小时（A3 工程目标） |
| 谁跑 demo | 作者本人；MVP 不引入外部用户测试 |

**关键理由 / 用户输入**：

- **MHWs 单游戏**：RE 引擎游戏大同小异，做通一个之后其他游戏（MHWI / RE4 /
  RE9）扩展不难。MVP 完全不碰其他游戏。
- **MMD 优先**：MMD 模型贴图基本是一体的、且贴图非常简单（一个基础色为主），
  前处理环节少。VRC 模型经常需要先把不同部件拼接组合，贴图体系也复杂
  得多，前处理量大。MVP 演示以 MMD 为主，能跑通最多场景；VRC 是
  "支持但不优先演示"。
- **L3 比预想的简单**（用户更正了我的估计）：toolkit 的批量导出已经
  自动处理了游戏配置 / 路径 / REF 前置（不需要手动解包封包）。L3 的额外
  成本对作者来说很低。
- **"成功"信号**（用户原话）：**导出无严重报错 → 必然能跑通**。因为各类
  常见问题都被 toolkit 自动处理了。

**素材策略**：repo **不附带**模型素材（版权 + MMD 文件大）。dev / demo
时让作者从外部下载特定模型，文档里写明用哪个 MMD / VRC 模型作为参考。

**自动化检查（待写）**：仿 stage 0 的 `verify_blender_mcp.py`，做一个
`verify_mvp.py`（或 `stage_mvp.py`）：
- 关键 Operator 的返回是否 `FINISHED`（vs `CANCELLED`）
- 导出的 mesh / mdf2 / chain2 / clsp 等文件是否存在 / 非空
- 中间态符合预期（X/Y 预设是否正确加载、骨架对齐预览的 ✓ 比例等）

→ MVP 验收 ≈ "脚本绿灯 + 进游戏目检 1 次"。

**留的口子**：
- 验收脚本设计时按"通用 RE 引擎导出检查"思路写，便于后期扩展到 MHWI/RE4/RE9。
- 来源模型如果 MMD 优先实测困难（比如 MMD 模型质量差 / 风格化太强），
  退路是改用 VRC 单一标准模型。

---

**🎉 A 层（产品 / UX）至此全部决议完成**。下一步进入 **B 层（架构）**。

---

# B 层：架构

> A 层定下后展开。这里只先列议题，每题写 1-2 句背景，等 A 定了再细化。

---

## B5 + B6. 状态感知 + 工具粒度（联合决议）

二者强耦合，分开决议会反复来回。一起锁定。

### 决议 🟢（2026-05-08）

**B6 — 工具粒度：中层 phase tool 架构**

按 plan.md 的"环节"切分，约 **12-15 个 phase tool**，每个对齐一个或多个相邻
环节。理由（用户输入 + 我提案）：

- plugin_api.md 里的 ~50+ 个 Operator **有明确领域分类**（用户原话），
  每个 op 通常只在 1-2 个环节里使用。把每个 op 都暴露成 LLM 工具是
  无意义的扁平化。
- 1-7 全流程下（A3），LLM 走每个 op 会产生 5-10 倍的 tool call → 慢、贵、
  失败点多。
- LLM 真正出场的地方是 **phase 内部的经验类分类**（X 预设选择 / 物理骨
  路线 A/B / PBR 通道映射），不是 phase 之间的机械调度。

**phase tool 内部职责**：
1. 入口自动 spot-check 当前 Blender 状态（衔接 B5）
2. 必要时调 LLM 做经验分类（含混合策略，见下）
3. 编排式调用底层 Operator
4. 结构化返回结果给上层 Agent + 更新 cache

**Agent（上层）职责**：
- 决定调哪个 phase（基于用户进度 + 状态）
- 询问 / 解释（A2 讲解懒加载）
- 路由用户问题（"这步对吗？"）
- 错误响应（B7 议题）

**核心 trade-off（用户认同）**：
> **用 Python 编排代码换稳定性 + 性能 + 成本**。
> Phase tool 内部是确定性 Python（必要时调 LLM 做单点分类），LLM 不在
> operator 层做调度。代价：要写更多 Python；收益：每步 1-2 次工具调用、
> 上下文短、错误恢复局部化、可单独单测（mock blender socket）。

---

**B5 — 状态感知：混合方案**

- **Agent 端**维护轻量 cache：
  - 场景中骨架/网格列表
  - 当前 Blender 模式（OBJECT / EDIT / POSE）
  - X / Y 预设是否已加载、哪个
  - 当前 active object
- **每个 phase tool 入口**自动 `get_scene_info`，把实际状态 vs cache 的
  diff 抛给 Agent
- **工具调用结束**时由工具更新 cache

→ 即原候选 c（混合）。Spot-check 频率从"每个 op 前"降到"每个 phase 前"，
开销可控。

---

**Phase 内部分类策略：混合（用户选定）**

当 phase tool 需要 LLM 做经验分类时（如选 X 预设、判物理骨路线），按置信度
分流：

| 置信度 | 行为 | 适用例 |
|--------|------|--------|
| 高 | phase tool 自动决定，不打扰用户 | 来源模型骨骼名命中 VRChat 标准 → 直接选 VRC 预设 |
| 低 | LLM 输出 1-3 个候选 + 简短理由 → Agent 转给用户拍板 | 模型混合命名、来源不明 |

**待落地的实现细节（不在本议题决议范围内，B7 时再细化）**：
- 置信度怎么测？候选实现：(a) LLM 显式输出 confidence；(b) 多次采样
  consensus；(c) 规则启发式（命中率 ≥ 阈值）；(d) 混合。
- 用户拍板的 UI 形态（候选卡片 / 下拉 / 自由输入）→ C12 议题。

---

**留的口子**：
- Phase 边界后期可调整（合并 / 拆分相邻 phase）。Agent 看到的工具数量是
  实现细节，不是 API 契约。
- 一个底层 `execute_code` 逃生口**不在 MVP 默认开放**（避免 LLM 走野路子
  破坏 phase 抽象），但保留实现（debug / 极端情况）。
- Cache 的精确字段未来可能扩展（如导出路径、Natives Root）。从最小集合
  起步，按需加。

---

### 用户附加提问归档

讨论 B5/B6 时用户提出两个补充问题，决议归在 C9（Agent 框架）：

1. **Tool Retrieval（向量库动态工具检索）是否合适？** —— 见 C9 区块讨论。
2. **是否引入 LangChain / LangGraph 作为学习载体？** —— 见 C9 区块讨论。

## B7. 错误恢复策略

### 决议 🟢（2026-05-08）

**前提判断**（来自用户讨论 + A2 / B6 / project memory 累积）：错误的主要
形态**不是 toolkit 内部炸**（用户原话：toolkit 兜底很多），而是**用户
场景错配**——选错对象 / 选错预设 / 模式不对 / 对齐结果质量低。
B7 主要在做"帮用户修自己的状态"，不是"帮 toolkit 修自己"。

---

**1. 错误返回的结构** —— 每个 phase tool 返回
`Result<state_diff, structured_error>`，error 字段：

| 字段 | 用途 |
|------|------|
| `category` | precondition_missing / op_cancelled / sanity_check_failed / unknown |
| `phase` | 哪个 phase 出的错 |
| `op_attempted` | 如果调到了底层 op，是哪个 |
| `user_action_needed` | 结构化提示，给 LLM 措辞用 |
| `raw_traceback` | 原始异常，**不直接给用户看**（debug 用） |

---

**2. 错误 UX 形态：结构化 fact + LLM 措辞 + 三选项**

LLM 把 `structured_error` 转成对用户的人话（按 A2 讲解懒加载规则：错误
是讲解的最佳触发点）。然后给用户三个明确选项：

- **重试**：用户在 Blender 里改完状态后，重跑当前 phase
- **跳过**：跳过本 phase 直接进下一个（带警告，下游会被影响）
- **求助**：进入 Q&A 模式，LLM 答疑（这里允许 LLM 更自由地对话）

LLM **不主动做"创意修复"**（按 [project memory: 用 Python 换稳定性](../memory/project_python_over_llm.md)）。
"求助"分支允许更对话式的应答，但仍然不能让 LLM 主动调工具改状态——只能
**讲解**。

---

**3. 回滚能力：不做**

理由：
- Blender 自带 Ctrl+Z 已经覆盖大多数 case
- 用户在 phase 完成后**自然会**保存 `.blend`
- 自动快照 `.blend` 副本的工程量大（磁盘 IO / 文件管理 / 命名冲突），
  收益不抵
- 真出现"回不去"的硬性场景（极罕见），让用户自己重启 Blender 再来

留的口子：如果 MVP 实测中"回不去"成了高频痛点，加 phase 级别的轻量
快照（如把关键 scene 属性持久化到 JSON），但**不**做完整 .blend 快照。

---

**4. 防御性预检：在关键 phase 边界做 sanity check**

Phase 之间不做"全量"上游验证（性能差、干扰用户），但**关键交界**做
sanity check：

| Phase 入口 | 预检项 |
|------------|--------|
| Phase 4 (物理骨) | Phase 3 顶点组匹配率 ≥ 阈值（如 90%）；否则警告 + 让用户拍板"还要继续吗？" |
| Phase 5 (材质) | 关键骨架 / 网格命名是否符合 Y 预设要求 |
| Phase 6 (导出) | Natives Root 是否设置；mesh / mdf / chain 集合绑定是否齐 |

预检失败时进入与上面相同的"重试 / 跳过 / 求助"分支。"跳过"会带更
明显的警告（"这一步预检不通过，强行继续可能导致最终导出失败"）。

---

**留的口子（整体 B7）**：
- 错误分类的细粒度可以增长——上线后看实际遇到的错误模式，扩 `category`
  enum 即可，不影响接口
- 求助分支的 LLM 自由度需要实测——如果发现 LLM 在求助里给坏建议，再收紧
- 阈值（如 90% 匹配率）先用经验值，等 demo 数据积累后再调

## B8. 跨会话状态续传

### 决议 🟢（2026-05-08）—— MVP 不做

**MVP 范围**：刷新 / 重开浏览器 = 新会话开始。**不持久化**进度 / cache /
对话历史。

**理由**：
- A4 验收用例就是"作者一次跑完 1 小时"，不存在跨会话需求
- 完整持久化要做对：进度 + Blender 文件路径 + cache + 对话历史，工程量
  不抵收益
- `.blend` 文件本身已经是隐式的"进度持久化"——用户保存了 .blend，
  下次打开 ModPilot 时通过 `get_scene_info` 重新感知场景状态即可

**留的口子**：
- 上线后用户反馈"中途断了想接着做"成高频痛点 → 加最小化"会话快照"
  JSON（保存 phase 进度 + cache snapshot），不持久化对话历史
- LLM 上下文恢复用 `get_scene_info` 重建 cache，不需要 chat history
  续传

---

# C 层：技术选型

> 大多数选型在 A、B 定下后基本是唯一解。每个议题独立小节展开 / 归档。

| 议题 | 候选 | 当前状态 |
|------|------|---------|
| C9 Agent 框架 | 手写 ReAct / Claude Agent SDK / LangChain / LangGraph | 🟢 路径 A（见下） |
| C10 LLM | Claude Sonnet/Haiku / OpenAI 兼容（DeepSeek/Qwen 等）/ 本地 | 🟢 双轨：开发 DeepSeek V4 + Claude oracle/fallback（见下） |
| C11 RAG | tool retrieval / 内容 RAG / 不上 | 🟢 不上 RAG，内容 RAG 备选（见下） |
| C12 前端 | React+Vite / 朴素 HTML+htmx / Streamlit | 🟢 htmx + 极简 HTML/CSS（见下） |
| C13 包管理 | uv / poetry / pip+venv | 🟢 uv（见下） |

---

## C9. Agent 框架选型

### 决议 🟢（2026-05-08）—— 路径 A：解耦学习

**MVP**：用 **原生 Anthropic SDK（`anthropic` 包）+ 手写 ReAct loop**（约
300 行 Python）。Phase 架构（B6 决议）本身就接近状态机，不需要外部框架。

**MVP 后**：另开 branch 把同一个 agent 用 **LangGraph 重写一遍**作为
学习练手。这样：
- 学习真发生（用户的核心目标）
- MVP 不被框架学习曲线 / breaking change 拖累
- 重写时能清楚看到框架在抽象什么、哪些抽象有价值

**讨论时考察过的备选**（保留作未来知识 / 扩展时参考）：

| 框架 | 适合什么场景 | 为什么我们 MVP 不用 |
|------|-------------|---------------------|
| LangChain（全套） | 大量集成需求（多 vector DB、多文档加载器等）的产品 | 抽象重、breaking change 频繁、debug 难；我们大多数功能用不上 |
| LangGraph | stateful agent、多分支 / 错误恢复 / 用户交互循环 | **真的契合我们的甜区**，但学习成本会拖累 MVP；改放 MVP 后练手 |
| Claude Agent SDK | Claude 模型的 agentic 用例 | 对齐好但生态小、tutorials 少；学习迁移性差 |

**留的口子**：
- 如果 MVP 后做 LangGraph 重写时发现框架收益显著，可以反向 fold 回主分支
- LangChain 不专门学，因为 LangGraph 文档涵盖了大多数现代概念

---

## C10. LLM 选型

### 决议 🟢（2026-05-08）—— 双轨制 + provider 抽象

**MVP 主力（开发期默认）**：**DeepSeek V4**（用户拥有 API key；用户实测
"性能强大，直逼 Gemini 3 Pro low"）。
- 协议：DeepSeek 历来 OpenAI 兼容，V4 沿用同一形态（用 `openai` SDK +
  改 base_url + 改 model 名调用）
- 实施时待补：具体 model 字符串、确认 base URL

**Oracle / fallback 模型**：Claude Sonnet 4.6（强）+ Haiku 4.5（中性价比）
- 用途 1（debug oracle）：phase 在 V4 上失败 → 切 Sonnet 看是不是设计
  问题；如果 Sonnet 也失败 = 设计错；Sonnet 成功 = V4 能力问题，决定
  改 prompt 还是升级模型
- 用途 2（demo 保底）：A4 决议的 demo 给关键观众时切 Sonnet，那点成本
  不计较
- 用途 3（真实差距校准）：少量 A/B 评测，量化 V4 vs Sonnet 在我们
  workload 上的实际差距

**Provider 抽象层**：约 100 行的 `LLMClient` 类
- 输入：messages、model 字符串、tools
- 内部：根据 model 前缀路由到 anthropic 或 openai 客户端
- 切换 provider = 改 config，不改业务代码
- 不引入 LangChain 的 chat model 抽象（C9 决议：解耦学习）

**澄清两件事（用户讨论中提出）**：
1. **Anthropic SDK ≠ 锁定 Claude**：`anthropic` 包只调 Claude，但
   `openai` 包能调几乎所有国产模型（DeepSeek/Qwen/Moonshot/智谱等都提供
   OpenAI 兼容 endpoint）。我们用后者+前者各调一种。
2. **用其他 LLM ≠ 必须 LangGraph**：LangGraph 管 agent 状态机，跟选哪家
   LLM 完全正交。这两件事不要绑定。

**讨论时考察过的备选**（保留作未来知识）：

| 模型 | 输入价 | 输出价 | 我们用不用 |
|------|--------|--------|------------|
| Claude Sonnet 4.6 | $3 / M | $15 / M | oracle / demo fallback |
| Claude Haiku 4.5 | $0.80 / M | $4 / M | 中性价比备用 |
| GPT-5 mini | ~$0.25 / M | ~$2 / M | 暂不用，需要时可加 |
| **DeepSeek V4** | (V3 参考: $0.27 / $1.10) | 同左 | **MVP 默认** |
| Qwen3 系列 | ~$0.20-0.40 / M | ~$1-2 / M | 备选；中文命名识别可能更强 |
| Gemini 2.5 Flash | ~$0.075 / M | ~$0.30 / M | 工具调用偶有怪行为；不优先 |
| 本地 Ollama | $0 | $0 | MVP 不在范围（硬件要求 / 性能问题）|

**留的口子**：
- MVP 跑稳后做一次 V4 vs Sonnet 的小规模 A/B 评测（关键 phase 的分类
  准确率），量化"省了多少钱、丢了多少质量"
- 用户 demo 时让用户选 model（自己 API key 跑通整个流程）—— 与 A2
  "档位 2 + 门槛即过滤" 一致
- 上下文成本观测要做：prompt cache 命中率（C11 也提到了）+ 单会话总
  token 数

---

## C11. RAG 是否需要

### 决议 🟢（2026-05-08）

**MVP 不上 RAG**。plan.md 总长约 12K tokens，**直接塞 system prompt +
prompt cache** 即可——成本一次写入、后续命中缓存几乎免费。引入向量库
是过度设计：多了 embedding 模型、向量 DB、检索调用三个失败点，没有
对应的收益。

**讨论时考察过的备选**（保留作未来知识 / 扩展时参考）：

| 模式 | 适合什么场景 | 为什么我们 MVP 不用 |
|------|-------------|---------------------|
| **Tool Retrieval**（工具向量检索） | 100+ 工具、MCP marketplace、动态工具池 | 我们只有 12-15 个 phase tool，且静态、命名无歧义；纯杀鸡用牛刀 |
| **内容 RAG**（plan.md / plugin_api.md 切片向量化） | 文档量大、用户问答场景 | plan.md 12K 直接塞 prompt 性价比更高 |

**内容 RAG 作为未来备选**（用户保留）：

如果未来出现以下场景，把内容 RAG 作为第一备选升级路径：

- plan.md / plugin_api.md 大幅扩张（比如加入 RE Engine 各游戏拆包指南）
- 用户问答场景被实测高频（很多人问"为什么这么做"），需要更精准的
  in-context 引用
- 错误响应需要召回易错点表的具体条目作为讲解材料（A2 讲解懒加载）

→ 用 chromadb（或更轻的 lancedb）+ 句子级嵌入 + 简单 top-k 即可。
不为此引入 LangChain 的 retriever 抽象。

**留的口子**：
- 如果实施时发现 plan.md 真的超出舒适区（比如要加更多游戏 / 拆包指南），
  按上述路径升级。
- prompt cache 命中率监控应作为 MVP 上线后的观测指标——如果命中率低，
  说明对话流型态在频繁打断 cache，那时可重新评估 RAG。

---

## C12. 前端形态 / 技术栈

### 决议 🟢（2026-05-08）—— htmx + 极简 HTML/CSS

**MVP**：FastAPI 直接渲染 HTML（Jinja2 模板），htmx 处理交互。**不上**前端
框架。

**htmx 覆盖的需求**：
- 流式 LLM 输出（SSE / `hx-sse`）
- Phase 状态卡片局部更新（`hx-swap`）
- 错误响应的"重试 / 跳过 / 求助"按钮（`hx-post`）
- Blender 视口截图侧栏（`hx-trigger="every 5s"` 定时刷新）

**讨论时考察过的备选**（保留作未来知识）：

| 方案 | 适合什么场景 | 为什么 MVP 不用 |
|------|-------------|----------------|
| React + Vite | 复杂状态、组件复用、SPA 体验 | MVP 太简单 / 学习成本 / 部署麻烦 |
| Vue + Vite | 中文生态友好的 SPA | 同 React 拒理由；htmx 更轻 |
| Streamlit / Gradio | 数据科学 demo / 快速 prototype | 自定义 UI 难、聊天交互不优 |
| Solid.js / Svelte | 现代轻量 SPA | 仍然是 SPA，对单用户本地 app 过度 |

**留的口子**：
- 后期交互复杂度上来了（多 panel / 富 UI / 协作场景）→ 迁 React
- HTML 结构清晰的话迁移成本可控

---

## C13. Python 依赖管理

### 决议 🟢（2026-05-08）—— uv

**MVP**：用 [uv](https://github.com/astral-sh/uv)（Astral 出品，Rust 实现）
做依赖管理 + 虚拟环境 + Python 版本管理。

**理由**：
- 比 pip 快 10-100 倍，重装依赖几秒钟
- 一站式：管 Python 版本 + venv + 依赖（不需要 pyenv / conda）
- `pyproject.toml` 标准格式 + `uv.lock` 标准化锁文件
- 2025-2026 主流首选

**讨论时考察过的备选**：

| 方案 | 优劣 |
|------|------|
| pip + venv | 通用但慢；缺现代 lock file；要配合 pyenv 管 Python 版本 |
| poetry | 老牌但慢；依赖解析有时奇怪；维护节奏慢 |
| **uv** | 快、规范、活跃维护、`uv.lock` 标准化 |
| conda / mamba | 跨语言依赖（C 库等）才有意义；我们纯 Python，多余 |
| rye | 同 Astral 早期项目，已被 uv 取代 |

**留的口子**：uv 仍在 0.x 演进期（截至 2026 年 5 月）。如果遇到无法绕开
的 bug，pip + venv 是无痛 fallback（`pyproject.toml` 标准化 = 互通）。

---

# D 层：工程

> 等 A/B/C 大局定下来再展开。先占位。

## D14. 目录结构 & 测试策略

### 决议 🟢（2026-05-08）

**ModPilot/ 内部按职能模块化**（不按"前后端"分层，因 htmx 决策让前端
依附后端 —— C12）：

```
REE-ModPilot/
├── ModPilot/                          # 后端应用（新建）
│   ├── pyproject.toml                 # uv 管理（C13）
│   ├── uv.lock
│   ├── .env.example                   # API key 模板
│   ├── README.md
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI 入口
│   │   ├── config.py                  # 配置（LLM / Blender / API key）
│   │   ├── blender/
│   │   │   ├── client.py              # BlenderConnection（自 stage 0 提取）
│   │   │   └── state.py               # Scene state cache + diff（B5）
│   │   ├── llm/
│   │   │   ├── client.py              # LLMClient provider 抽象（C10）
│   │   │   ├── anthropic_provider.py
│   │   │   └── openai_provider.py     # 兼容 DeepSeek/Qwen/OpenAI
│   │   ├── agent/
│   │   │   ├── loop.py                # ReAct 主循环（C9）
│   │   │   ├── prompts.py             # System + phase prompts
│   │   │   └── error_handler.py       # B7 结构化 error → 用户消息
│   │   ├── phases/                    # B6：12-15 个 phase tool
│   │   │   ├── base.py                # PhaseTool 基类 / Result 类型
│   │   │   ├── pose_correction.py     # 视频 1
│   │   │   ├── skeleton_align.py      # 视频 2
│   │   │   ├── vertex_groups.py       # 视频 3
│   │   │   ├── physics_bones.py       # 视频 4
│   │   │   ├── material.py            # 视频 5
│   │   │   ├── batch_export.py        # 视频 6
│   │   │   └── advanced.py            # 视频 7
│   │   ├── routes/
│   │   │   ├── api.py                 # JSON API
│   │   │   └── pages.py               # HTML 渲染 + htmx 端点（C12）
│   │   └── templates/                 # Jinja2 模板
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── unit/                      # mock Blender
│   │   └── integration/               # 真 Blender，pytest marker
│   └── static/
│       └── htmx.min.js
├── docs/                              # 已有
├── memory/                            # 已有（存于 ~/.claude，不在 repo）
├── verify_blender_mcp.py              # Stage 0
└── verify_mvp.py                      # Stage MVP（待写）

# 不在 repo 里（gitignore；README 列为前置要求）：
#   Modding-Toolkit/  ← 用户从 Dimcirui/Modding-Toolkit 单独装
#   blender-mcp/      ← 用户从 ahujasid/blender-mcp 单独装
```

**测试分层**：

| 层 | 工具 | 何时跑 |
|----|------|--------|
| 单元测试（mock Blender） | pytest + fake JSON socket server | 每次 commit / CI 默认 |
| 集成测试（真 Blender） | pytest + 9876 端口 + `@pytest.mark.integration` | 手动 / Blender 开着时 |
| Stage 脚本（verify_*.py） | 独立顶层脚本，不进 pytest | 里程碑手动 |

**关键设计点**：
- `blender/client.py` 把 stage 0 的 `_recv_response` / `call` / `execute_code`
  / `SENTINEL` 提取到生产代码，不再是脚本临时实现
- `phases/base.py` 定义契约（Result / 入口 spot-check / 出口 cache 更新），
  所有 phase 继承
- `llm/client.py` 是 C10 的 ~100 行抽象层，**业务代码只见 `LLMClient.chat(...)`**，
  不直接 import anthropic / openai

**留的口子**：
- 子目录命名后期可重构（`agent/` 拆 `agent/` + `flows/` 等），从最简集合起步
- 如果后期前端复杂化迁 React，`routes/pages.py` + `templates/` 可整体替换
  为 `frontend/`，不动其他模块

---

## D15. 测试素材集

### 决议 🟢（2026-05-08）

**约定**：

- **不进 repo** —— 版权 + 文件大小（A4 决议）
- **`docs/demo_setup.md`（待写）**列具体推荐：
  - MMD 模型推荐（公开授权，URL + 版本）
  - MHWs 标准角色骨架（拆包工具 + 路径说明）
  - REF 前置安装（toolkit 自动处理，但说明用户需有 REF）
- 用户首次跑 MVP 时按文档下载素材到约定路径（如 `~/.modpilot_assets/`）

**当前不锁定具体素材**——选具体 MMD 模型是实施阶段第一步任务，等开始
实施时再定（候选：TDA Miku 公开版本 / NICONI 公开版等）。

**留的口子**：
- 如果实测发现 MMD 模型质量普遍差 / 风格化太强 → 退路改用 VRC 单一标准
  模型（A4 决议保留的口子）
- 后期若产品扩散，考虑做"素材市场"页 / 推荐列表，但 MVP 不做

---

# E 层：实现阶段决策

> 记录 Stage 2+ 实现过程中落定的细粒度决策。格式与 A/B/C/D 层一致。

---

## E16. PhaseResult 形状

### 决议 🟢（2026-05-09）

**轻量三字段**：

```python
@dataclass
class PhaseResult:
    success: bool
    state_diff: dict        # SceneState.diff() 输出；成功时描述变化，失败时为空
    error: PhaseError | None
```

**排除**：不在 PhaseResult 里内嵌 `user_message` 或 `next_phase`。
- `user_message` 由 agent loop 调 LLM 生成，保持 phase tool 无 LLM 依赖。
- `next_phase` 由 agent loop 根据 phase 序列和当前状态决定，不由 phase 自己宣告。

**PhaseError 结构**（对应 B7）：

```python
@dataclass
class PhaseError:
    category: str      # "operator_failed" | "precondition" | "timeout" | "unexpected"
    operator: str      # 失败的 bpy.ops.* 调用（可为空）
    message: str       # 简短技术描述（给 LLM 措辞用）
    suggestion: str    # 可选：已知修复建议
    raw: str           # 原始异常文本（不直接给用户看）
```

---

## E17. 分类决策位置

### 决议 🟢（2026-05-09）

**分类在 agent loop，phase tool 只管执行。**

Phase tool 接收 agent loop 传入的分类参数（如 `preset: str`），自己不调 LLM。
Agent loop 在调用 phase 之前做分类：

```
agent loop
  → LLM 分类（高置信 → 自动；低置信 → 暂停等用户确认）
  → 拿到分类结果
  → 调 phase_tool.run(params)
  → 处理 PhaseResult
```

**理由**：phase tool 保持纯执行单元，无 LLM 依赖，单元测试不需要 mock LLM。
分类逻辑集中在 agent loop，不在每个 phase 里重复。

---

## E18. 同步 BlenderClient 在异步 FastAPI 中的调用方式

### 决议 🟢（2026-05-09）

**`asyncio.to_thread(phase.run, ...)`**——phase tool 保持同步，FastAPI route 用 to_thread 卸载到线程池。

```python
# FastAPI route 调用 phase 的模式
result = await asyncio.to_thread(phase.run, scene_cache, params)
```

**排除**：不改写 BlenderClient 为 async（工程量大，对单用户工具无收益）。

**留的口子**：若未来需要高并发或 WebSocket 实时推送，再将 BlenderClient 改写为 asyncio socket。

---

# 附：讨论流程约定

1. 一次只讨论一题（或紧耦合的两题，如 B5+B6）。
2. 讨论结束后我把结论写进对应"决议"区块，并在状态表把状态改成 🟢。
3. 决议要包含：**选了哪个方案 / 排除了什么 / 留了什么口子给未来**。
4. 全部 A 层决议完成前不动 ModPilot/ 后端代码。

---

**接下来怎么走？**

按依赖顺序逐题推进。当前进度见顶部状态表。

---

# 附录 P：先决条件清单（内部）

> **用途**：定义 ModPilot 假设用户已经具备的 Blender 基础能力。是 prompt
> 工程、UI 文案、错误响应、教学触发条件的**隐性边界**。
> **不对外发布**（A2 决议）。
> **维护方式**：随产品迭代调整；新增功能若依赖某项基础能力，先问一下
> "这条假设还成立吗？" —— 如果用户实际反馈中频繁被卡，考虑收编进 Agent。

## 我们假设用户已经会的事

**Blender 安装与插件**
1. 已装 Blender 4.x（推荐 4.3.2，与 Modding-Toolkit 兼容版本一致）。
2. 会安装 / 启用 Blender add-on（Edit → Preferences → Add-ons），已装好
   `blender-mcp` 和 `Modding-Toolkit`。

**文件 I/O**
3. 能用 Blender 导入 FBX / PMX / glTF 等常见格式。
4. 能 Save / Save As `.blend` 文件，知道工作流程要自己存盘。

**视口操作**
5. 基础视口导航（中键拖拽旋转 / 滚轮缩放 / Shift+中键平移）。
6. 知道 N 键（侧边栏）/ T 键（工具栏）的开关。

**对象 / 场景**
7. 能在大纲（Outliner）里选中对象，能识别对象类型（ARMATURE / MESH /
   EMPTY / CAMERA / LIGHT）。
8. 知道"激活对象 / active object"的概念（最后选中的那个，黄色高亮）vs
   "已选对象"（红色边框）—— Modding-Toolkit 多个 Operator 区分两者。

**模式**
9. 能切换模式：OBJECT / EDIT / POSE（Tab 或下拉框）。知道 EDIT 模式
   编辑结构、POSE 模式做姿态、OBJECT 模式做整体变换。

**骨架 / 网格关系**
10. 知道 Armature Modifier 把网格绑定到骨架上（不需要会编辑权重，但
    要知道这层关系存在）。
11. 知道顶点组（Vertex Group）是什么。
    *（不展开讲；理论太复杂，留给用户自己搜索了解。）*

**模型来源识别 ★（A1 引入的硬约束，重点）**
12. 能说出自己的来源模型属于哪一类：VRChat / MMD / Unity Humanoid /
    某个具体游戏的拆包。能找到对应的来源说明。
    > **强调**：MVP **只接受成体系模型**。任意来源 FBX 不在范围内。
    > 成体系模型的命名规则、骨骼结构都有公开文档可查，工具链和 Agent
    > prompt 都依赖这一点才能稳定工作。

## 我们**不**假设用户会的事（这些 Agent 要包揽或主动解释）

- RE Engine 模型结构、骨架命名规则、X/Y 预设的概念、辅助骨的存在意义。
- 顶点组与权重的内部机制（只在错误响应里提到，不主动讲）。
- 物理骨拓扑、chain_role 标记、`_End` 末端骨的生成规则。
- MDF2 / MRL3 材质格式、PBR 通道映射、texconv 工作流（视频 5 范围）。
- 批量导出的 JSON 配置、parts_mask、BoneSystem 等（视频 6 范围）。

## 审阅记录

- 2026-05-08：初稿审阅完毕。结论：
  - 第 5 项（视口导航）保留——这种"基础到不像门槛"的项目恰好让用户
    自检"是不是这个产品的目标用户"。
  - 加入"顶点组是什么"——只列名词，不展开讲（理论复杂，自行搜索）。
  - 第 11 项（模型来源识别）单独标 ★ 强调，因为是 A1 引入的硬约束。
