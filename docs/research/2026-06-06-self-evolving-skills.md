# 自我进化 Skill(Self-Evolving Skills)深度研究报告

> 日期:2026-06-06 · 作者:Claude(受 leyi 委托)· 形态:通用概念深度研究 + expert-work 落地章节
>
> **引用可信度声明(请先读)**:本报告的核心三篇论文(arxiv 2604.01687 / 2605.09192 / 2605.10332)由作者亲自打开 arxiv 原文逐条核对(标题、作者、机制、数字)。经典奠基工作(Voyager、Reflexion、Toolformer、ADAS、Gödel Agent、Darwin Gödel Machine、A-MEM)与 Anthropic 官方文档为公认真实来源。**其余 2026 年(2602.* ~ 2606.*)的二手文献来自自动 Web 检索,未逐条核验原文**,引用时统一标注「检索来源,未逐条核验」,请勿据其精确数字直接对外引用。Expert Work 落地章节引用的仓库文件路径均真实存在。

---

## 0. TL;DR(执行摘要)

**一句话定义**:**自我进化 Skill** 指 agent 不通过更新模型权重,而是把自己在执行任务中获得的经验,沉淀为**可复用、可累积、可版本化、可检索**的结构化能力单元(skill),并在后续不断**自动生成、验证、修订、淘汰**这些 skill —— 从而让 agent 的"能力面"随使用持续增长。

**三句话结论**:
1. 这是 2025 下半年 ~ 2026 年最热的 agent 自改进范式,根因是 Anthropic 把 **Skill 提升为一等公民**(结构化多文件包 + progressive disclosure),让"权重不动也能持续变强"在工程上变得可行且低成本。
2. 它真正的硬核难题**不是"如何生成 skill",而是"无 ground truth 时如何验证自生成 skill 真的有效"** —— 你给的三篇论文恰好从三个角度回答了这个问题(共进化验证器 / 后验轨迹证据 / 反思归因),这是它区别于"伪进化"的关键。
3. 学界与安全界已经在大声示警:**伪进化(只改 prompt/context 而非真能力)、误差雪崩、skill 库膨胀导致检索崩塌、自生成代码的供应链与沙箱安全、固定参数下的表达力天花板** —— 任何严肃落地都必须把"护栏"和"能力"同等对待。

**对 Expert Work 最重要的 takeaway**:Expert Work **不需要从零造轮子**。它已经具备自进化 skill 所需的大部分稀缺基建 —— `SkillAuthoredBy = human | agent` 的状态机、高危 publish gate、trajectory 记录器、curation worker、memory 凝结引擎、reflect 节点。**缺的不是地基,而是把这些子系统串成闭环的 glue + 一个"验证器/后验蒸馏"环节**。而"后验蒸馏"正是 SPARK(2605.09192)的核心思想,Expert Work 现有的 trajectory + eval_dataset 基建是它的天然温床。

---

## 1. 什么是"自我进化 Skill"

### 1.1 先分清 Skill 与 Tool

Anthropic 在 2025 年 10 月推出 Agent Skills、12 月开放为行业标准时,给了一个被后续所有论文沿用的定义([Anthropic: Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)):

- **Tool(工具)** = 单一、自包含的函数(一次 API 调用、一个 shell 命令)。
- **Skill(技能)** = 一个**结构化的多文件包**:`SKILL.md`(YAML 元数据 + Markdown 指令)+ 可选脚本 / 资源 / 子文档。它表达的是"完成一类多步专业任务的程序性知识",而非一次原子调用。

关键设计是 **Progressive Disclosure(渐进式披露)**:agent 先只看到 skill 的元数据(名字 + 描述)来判断"要不要用",需要时才加载 `SKILL.md` 全文,再需要时才加载脚本/资源。这让 skill 体积**理论上不受上下文窗口限制**。CoEvoSkills(你给的第一篇)开篇即引用这个 Skill≠Tool 的区分,并指出:正因为 skill 是"相互依赖的多文件 artifact",**为工具设计的自进化方法不能直接搬到 skill 上**。

### 1.2 "自进化"的含义谱系(从弱到强)

"自我进化"是个谱系词,工业界落地的强度差异极大。按改动对象从浅到深排列:

| 档位 | 改动对象 | 代表 | 风险 | 成熟度 |
|---|---|---|---|---|
| L0 外部学习日志 | 一个 `.learnings/` 笔记目录 | Claude Code "Self-Improving Agent" skill | 极低 | 已产品化 |
| L1 prompt / 记忆优化 | 系统 prompt、长期记忆条目 | LangGraph+LangMem、Letta/MemGPT、CrewAI | 低 | 已产品化 |
| L2 工具/技能创造 | 新增可执行 skill/脚本并复用 | Devin 自建脚本、Karpathy AutoResearch、**你给的三篇** | 中(自生成代码) | 研究→早期产品 |
| L3 代码级自改写 | 改写 agent 自身源码 | Sakana **Darwin Gödel Machine**、Gödel Agent | 高(递归自改) | 研究阶段 |

本报告聚焦的"自我进化 Skill"主要落在 **L2**(并向 L1 借记忆、向 L3 警示风险)。

### 1.3 与相邻概念的辨析

- **vs Self-Improving Agents**:后者是更大的伞,涵盖 prompt/架构/代码等一切自改进;"自进化 skill"是其中**以 skill 为载体**的具体路线。
- **vs Continual Learning(持续学习)**:经典持续学习靠**更新参数**,核心痛点是灾难性遗忘;自进化 skill **刻意不动参数**,把"记住的东西"外化到 skill 库 —— 用外部存储绕过遗忘,代价是受上下文/检索能力约束(见 §8 理论边界)。
- **vs Memory(记忆)**:记忆通常是**陈述性/情节性**(facts、对话片段);skill 是**程序性**(怎么做一类事)。二者常被合并讨论,但程序性 skill 更接近"可执行能力"。
- **vs RSI(Recursive Self-Improvement,递归自我改进)**:RSI 指系统改进"改进自己的能力"本身,是 L3 的理论极限,伴随对齐与控制风险([Anthropic: When AI Builds Itself](https://www.anthropic.com/institute/recursive-self-improvement))。自进化 skill 在 L2 时通常**不触及** RSI,但若让 agent 改进"生成 skill 的 skill",就会滑向 RSI。

---

## 2. 为什么现在(2026)突然重要

1. **Skill 成了开放标准**。Anthropic Agent Skills(2025-10 推出,2025-12 开放标准),随后 OpenAI、Cursor、Manus、Devin 等迅速采纳同构的 `SKILL.md` 格式。"可移植的能力包"有了统一载体,自进化才有了可累积的标的物。

2. **"权重不动也能变强"的工程吸引力**。重训/微调昂贵、慢、且有灾难性遗忘;而把经验沉淀成 skill 是**即时、可审计、可回滚、可跨模型迁移**的。SPARK(2605.09192)甚至展示:从轨迹蒸馏的 skill 能让**便宜的学生模型超过人写 skill**,推理成本低至教师模型的 1/1000 —— 这是直接的降本增效论证。

3. **Agentic Engineering 的范式叙事**。Andrej Karpathy 在 2026 年提出"我们进入了 agentic engineering 时代,人类不再写大多数代码,而是指挥、监督、编排 agent",并以其 AutoResearch(~630 行 Python + 一个 markdown prompt,在单 GPU 上跑 700 个实验、自动发现 20 项优化)作为"自改进循环"的范例([nextbigfuture 综述](https://www.nextbigfuture.com/2026/03/andrej-karpathy-on-code-agents-autoresearch-and-the-self-improvement-loopy-era-of-ai.html))。"Karpathy Loop"(提议→测试→评估→提交→重复)成了自进化的口号化模板。
   > 注:Karpathy 言论与 AutoResearch 细节为新闻/博客二手来源,未逐条核验。

---

## 3. 核心机制:自进化 Skill 的理论骨架

把各家方案抽象出来,自进化 skill 都是同一个**闭环**:

```
        ┌───────────────────────────────────────────────┐
        │                                               │
   (1) 生成/提议 ──> (2) 执行(在真实/仿真环境) ──> (3) 验证/打分      │
   propose            run                       verify          │
        ▲                                           │           │
        │                                           ▼           │
   (6) 检索复用 <── (5) 沉淀入库(版本化) <── (4) 蒸馏/修订/归因  │
   retrieve         store                      distill/revise   │
        │                                                       │
        └────────────────── 经验反馈 ───────────────────────────┘
```

- **(1) 生成**:从任务、失败、用户反馈或已有轨迹中产生候选 skill。
- **(2) 执行**:在环境里真正跑(数字环境=代码/工具调用;具身环境=动作)。
- **(3) 验证 ← 全场最难**:没有人写好的标准答案时,怎么知道这个 skill 好不好?这是整条链的咽喉。
- **(4) 蒸馏/修订/归因**:把"成功的做法"提炼成可复用片段;把"失败"归因(是 skill 内容错,还是执行没照做?)。
- **(5) 沉淀**:入库,带版本、作者、content_hash、状态(draft/active/stale/archived)。
- **(6) 检索复用**:库一大,检索本身成为瓶颈(见 §8)。

**为什么 (3) 验证是咽喉**:如果验证不可靠,闭环就退化成"在自己产生的噪声上自我强化"→ 误差雪崩、伪进化(§8)。**你给的三篇论文,正是三种不同的"验证/grounding"方案**,这就是它们的共同价值所在。

---

## 4. 三篇代表论文精读(已逐条核对原文)

### 4.1 CoEvoSkills:用"共进化验证器"解决无标注验证(arxiv [2604.01687](https://arxiv.org/abs/2604.01687))

- **作者/出处**:Hanrong Zhang、Shicheng Fan、Henry Peng Zou … Philip S. Yu 等(cs.AI,2026-04,代码待发布)。
- **问题**:Skill 由人手写,既**标注昂贵**,又存在**人机认知错位**(human–machine cognitive misalignment)—— 人以为讲清楚了,agent 却理解偏,在 SkillsBench 上反而**拖累**性能。所以要让 agent **自主生成 skill**。但 skill 比 tool 复杂(多文件、相互依赖),为 tool 设计的自进化方法搬不过来。
- **方法**:**CoEvoSkills = Skill Generator + Surrogate Verifier 的共进化**。
  - Skill Generator 迭代式精化一个多文件 skill 包。
  - **Surrogate Verifier(代理验证器)与生成器一起进化**,在**拿不到 ground-truth 测试内容**的前提下,给出"有信息量、可执行"的反馈。
  - 关键创新就是:验证器不是固定的、也不依赖标准答案,而是**跟着生成器一起变强** —— 这样才能持续提供有效信号,避免生成器对着一个静态/弱验证器"刷分"。
- **结果**:SkillsBench 上,在 **Claude Code 与 Codex** 两个载体上对比五个 baseline 均取得最高通过率;并对额外 6 个 LLM 展现强泛化。
- **局限**:验证器自身可能错(verifier 也是模型);"无 ground truth"下的可靠性上界仍未知;主要在数字/代码 skill 域验证。
- **一句话定位**:**回答了"谁来当裁判"——让裁判和选手一起进化。**

### 4.2 SPARK / PDI:证据胜于计划,稳健 skill 必须"后验"(arxiv [2605.09192](https://arxiv.org/abs/2605.09192))

- **作者/出处**:Yang Zhou、Zihan Dong、Zhenting Wang … Dimitris N. Metaxas 等(Rutgers 系,cs.AI,2026-05;代码 https://github.com/EtaYang10th/spark-skills)。
- **核心论断(标题即观点)**:**Evidence Over Plans**。现有 skill 生成大量依赖"偏好日志 / 先验计划",结果收益微弱甚至为负。作者诊断这是一个**根本的"时机(timing)瓶颈**:稳健的 skill 应该是**后验的(posterior)—— 从真实环境交互的经验里蒸馏出来,而不是从执行前的计划里写出来**。
- **方法**:
  - 提出 **PDI(Posterior Distillation Index,后验蒸馏指数)**:一个**轨迹级**指标,量化"一个蒸馏出来的 skill 到底有多扎根于任务-环境的证据"。
  - 提出 **SPARK** 管道(Structured Pipelines for Autonomous Runnable tasKs and sKill generation):保存完整任务执行证据,做全轨迹级分析;生成**环境验证过的轨迹**来计算 PDI,并把 PDI 当作**在线诊断与干预信号**,确保 skill 是后验形成的。
- **结果**:跨 86 个可运行任务,SPARK 生成的 skill 稳定超过"无 skill"基线,并在**学生模型**上**超过人写 skill**,推理成本低至教师模型的 **1/1000**。
- **局限**:依赖能"真跑"的可验证环境(runnable tasks);PDI 作为代理指标的普适性待检验。
- **一句话定位**:**回答了"验证的依据是什么"——必须是事后的环境证据,而非事前的计划。**

### 4.3 EmbodiSkill:用"技能感知反思"把失败归因拆开(arxiv [2605.10332](https://arxiv.org/abs/2605.10332))

- **作者/出处**:Ruofei Ju、Xinrui Wang … Ting Cao、Yunxin Liu 等(cs.AI,2026-05;偏 MSRA 风格作者群)。
- **问题**:具身环境(布局、物体状态、执行因素各异)要求 skill 必须**从执行轨迹里自进化**。但把数字环境的做法直接搬到具身上有个陷阱:**一次任务失败,可能不是 skill 内容错了,而是 agent 没照着有效指导去执行(execution lapse)**。如果不区分这两者,就会"错杀"本来正确的 skill。
- **方法**:**EmbodiSkill,一个 training-free 框架**,做"技能感知反思 + 定向修订":
  - 对每条轨迹**相对当前 skill** 做解读;
  - 用 **skill-changing evidence(该改 skill 的证据)** 去更新 skill 主体;
  - 用 **execution-lapse evidence(只是没执行到位的证据)** 去**保留并强化**那些本来有效的指导。
- **结果**:ALFWorld、EmbodiedBench 上稳定提升;ALFWorld 上让一个**冻结的 Qwen3.5-27B 执行器**达到 **93.28%** 任务成功率,比"无 skill 直接用 GPT-5.2"高 **31.58 个百分点**。
- **局限**:具身仿真环境;反思归因本身的准确度依赖模型。
- **一句话定位**:**回答了"失败了怎么改"——先归因(内容错 vs 执行错),再定向修订,别错杀好 skill。**

### 4.4 三篇的统一主线与互补关系

把它们放在 §3 的闭环里看,**三篇打的是同一个咽喉(验证/grounding),但补在闭环的不同环节**:

| 论文 | 攻克环节 | 核心机制 | 环境域 | 一句话 |
|---|---|---|---|---|
| CoEvoSkills | (3) 验证 | 共进化的代理验证器(无需 ground truth) | 数字/代码 | 让裁判和选手一起进化 |
| SPARK / PDI | (3)→(4) 验证依据与蒸馏时机 | 后验、环境证据驱动的 PDI 在线干预 | 可运行任务 | 证据胜于计划 |
| EmbodiSkill | (4) 归因与修订 | 反思区分"内容错 vs 执行错" | 具身 | 归因后再定向修订 |

**共识(这是最值得记住的)**:自进化 skill 能不能成立,**取决于验证环节是否扎根于真实证据**。三篇都拒绝"靠模型自我感觉良好/靠先验偏好打分",转而要求"裁判可进化 / 依据是事后环境证据 / 失败先归因"。这正是把"伪进化"和"真进化"区分开的技术分水岭。**这条结论直接决定了 Expert Work 该怎么做(见 §9)。**

---

## 5. 更广的学术脉络(谱系)

> 本节经典工作(2023-2025)为公认真实来源;2025-2026 的 skill-* 系列为检索来源、未逐条核验。

**起源与综述**
- *A Survey of Self-Evolving Agents: What, When, How, and Where to Evolve*(2025,[2507.21046](https://arxiv.org/abs/2507.21046))—— 该领域首批系统综述,确立 What/When/How/Where 四维框架。
- 社区资源库 [Awesome-Self-Evolving-Agents](https://github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents)。

**技能库范式(skill library)**
- **Voyager**(2023,[2305.16291](https://arxiv.org/abs/2305.16291))—— 开山之作:Minecraft 里 LLM agent 把学到的技能写进不断增长的 skill 库,自验证 + 迭代,后续所有 skill 自进化工作的 baseline。
- **ADAS**(Automated Design of Agentic Systems,2024,[2408.08435](https://arxiv.org/abs/2408.08435))—— meta agent 自动设计 agent 代码,维护一个"发现档案库"。
- **Gödel Agent**(2024,[2410.04444](https://arxiv.org/abs/2410.04444))—— 递归自我改进框架,agent 可改自己的逻辑乃至"用于自改的代码"。

**工具创造(tool creation,skill 的前身)**
- **Toolformer**(2023,[2302.04761](https://arxiv.org/abs/2302.04761))—— LLM 自监督学会何时/如何调用工具,奠定"模型自主产出工具调用"的基础。

**记忆与经验驱动的自改进**
- **Reflexion**(2023,[2303.11366](https://arxiv.org/abs/2303.11366))—— 失败后写自然语言反思,prepend 到下次尝试;无梯度自改进的范式起点(与 EmbodiSkill 的"反思"一脉相承)。
- **Generative Agents**(2023,[2304.03442](https://arxiv.org/abs/2304.03442))—— LLM+动态记忆+反思的经典架构。
- **ExpeL**(2023,[2308.10144](https://arxiv.org/abs/2308.10144))—— 从训练任务自主收集经验、提炼 insight,测试时作 in-context 复用。
- **A-MEM**(2025,[2502.12110](https://arxiv.org/abs/2502.12110))—— Zettelkasten 式自组织记忆网络。

**Skill 作为一等公民(2025-2026 爆发,检索来源、未逐条核验)**
据检索,2026 年涌现一大批以 skill 全生命周期为对象的工作,例如:SkillOS(skill curation 的 RL 学习)、MemSkill(把记忆操作做成可进化的 memory skill)、MUSE-Autoskill(creation→memory→management→evaluation 全生命周期)、SkillComposer(create/merge/improve 三操作平衡 specificity vs generality)、Trace2Skill(从轨迹蒸馏可迁移 skill)、Graph-of-Skills(依赖图 + Personalized PageRank 做大规模 skill 检索)、SoK: Agentic Skills(给出 skill 全生命周期与 7 类设计模式)。这批工作共同把 skill 从"研究概念"推向"可工程化的一等对象"。

**评测基准(检索来源、未逐条核验)**
- SkillsBench(CoEvoSkills/SPARK 都在其上评测)、SkillLearnBench(持续 skill 学习,据称发现"self-feedback 会导致 recursive drift,外部反馈才有效"——与三篇论文的"必须外部 grounding"结论互证)、SkillRet(大规模 skill 检索基准)。

---

## 6. 工程实践全景(工业界)

> 本节多为厂商博客/新闻二手来源,未逐条核验;Anthropic 官方文档除外。

| 厂商/产品 | 自进化形态 | 机制要点 | 档位 |
|---|---|---|---|
| **Anthropic Claude Code** | 外部学习日志 | `.learnings/` 记录失败/纠正/缺口,任务前读取([Self-Improving Agent skill](https://mcpmarket.com/tools/skills/self-improving-agent));官方原生不让模型自动写 skill | L0-L1 |
| **OpenAI AgentKit / Skills** | 评估-优化循环 | Agent Optimizer:消费生产日志→评估→生成候选改进(prompt/tool/skill)→验证→推荐,带 lineage/diff/回滚([Introducing AgentKit](https://openai.com/index/introducing-agentkit/)) | L1-L2 |
| **Devin (Cognition)** | 工具创造 | 自建脚本/工具跨会话复用;`SKILL.md` 入库([Devin 2025 Review](https://cognition.ai/blog/devin-annual-performance-review-2025)) | L2 |
| **Cursor 2.4** | Subagents + Skills Marketplace | Rules(静态常驻)vs Skills(动态按需);`.cursor/agents/`([Cursor 2.4](https://cursor.com/changelog/2-4)) | L1-L2 |
| **Letta / MemGPT** | 记忆优先 | agent 用 tool-call 自编辑记忆;episodic reflection([Agent Memory](https://www.letta.com/blog/agent-memory)) | L1 |
| **LangGraph + LangMem** | 记忆 + prompt 优化 | 跨会话记忆 + 反思更新系统 prompt([LangMem](https://www.langchain.com/blog/langmem-sdk-launch)) | L1 |
| **Sakana Darwin Gödel Machine** | 代码级自改写 | agent 迭代改自己的源码,达尔文式档案;SWE-bench 20%→50%([2505.22954](https://arxiv.org/abs/2505.22954)) | L3 |

**归纳:当前五大成熟范式**(按风险/能力升序)
1. **外部学习日志**(最低风险,被动积累,不改 skill 结构)。
2. **prompt + 记忆优化**(只改指令/记忆,相对安全,改进空间有限)。
3. **工具/技能创造**(可引入全新能力,提升显著,但自生成代码风险最高 → 必须沙箱 + 审查 + 回归门)。
4. **供应链签名 + 版本锁定**(治理范式:skill 加密签名、生产锁版本、纳入 code review)。
5. **共进化验证 / 后验蒸馏**(=你给的三篇代表的研究前沿;自动化程度最高、最接近"真进化",但计算成本高、仍在研究→早期产品)。

---

## 7. 批判与风险(平衡视角,务必读)

自进化 skill 不是只有上行叙事。把"护栏"和"能力"同等对待,是它能否严肃落地的前提。

### 7.1 伪进化(Pseudo-Improvement)—— 最尖锐的批评
许多"自我改进"其实只在改 **prompt/context 管理**(parsing、retry、dispatch),而非交付"基础模型在任何 prompt 下都给不出"的新领域能力。换言之,**看起来在进化,实则只是把上下文整理得更顺**。有立场论文主张:真正的自我改进需要 **内在元认知学习**(agent 能评估自身、规划学什么、判断学没学会),而当前系统普遍缺这一层。
> 来源:*Truly Self-Improving Agents Require Intrinsic Metacognitive Learning*([OpenReview](https://openreview.net/forum?id=4KhDd0Ozqe));*LLM Agents Are Not Always Faithful Self-Evolvers* —— 均为检索来源、未逐条核验。**但这条批判与三篇论文的"必须外部 grounding"结论高度一致**:没有真实证据约束的"自反馈",就是伪进化温床。

### 7.2 失败模式
- **模型坍缩 / 数据自噬**:在自己生成的内容上反复学,多样性衰减、错误复合;研究指出"仅当合成数据完全替代真实数据时坍缩才发生,与真实数据混合则稳定"——意味着**自进化必须持续掺入真实信号**。
- **误差雪崩(error avalanche)**:验证不可靠时,错误非线性放大直至崩溃(印证 §3"验证是咽喉")。
- **灾难性遗忘**:这是参数更新路线的痛点;自进化 skill 用"外化到库"绕开,但代价转移到检索与上下文。
- **Skill 库膨胀 → 检索崩塌**:据检索,大平台已托管 **百万级** skill;当库从几十扩到几千/几万,问题从"用不用 skill"变成"检索出最相关的 skill",且 context 增大时性能可能从 40-50% 跌到 <10%(lost-in-the-middle)。Graph-of-Skills 等正是冲这个问题去的。
> 以上数字为检索来源、未逐条核验。

### 7.3 安全与对齐
- **自生成代码的脆弱性**:据多份 2025-2026 安全报告,AI 生成代码的漏洞密度显著高于人写(常被引为 ~2.74×);自进化 agent 创建的工具中相当比例存在不安全模式,且会被**复用传播**。
- **供应链 / 恶意 skill**:skill 是可执行代码,开放市场出现"语义合规但逻辑有害"的恶意 skill(semantic-execution 脱钩);OWASP 已出 Agentic 方向 Top 10(目标劫持、工具滥用、记忆中毒等)。
- **沙箱逃逸**:2025-2026 报出多个 agent 相关 CVE(代码执行 agent 的沙箱逃逸);"AI 生成的代码被默认可信"是核心隐患 → 必须强隔离(microVM / gVisor 等)。
- **RSI 对齐放大**:每轮自改都可能放大与人类价值的微小偏差;若改进速度快于人类评估,监督失效(Stuart Russell 的"快速起飞"警告)。Yann LeCun 则从更根本处质疑 LLM 路线本身。
> 安全数字与 CVE 为检索/新闻来源、未逐条核验;Russell/LeCun 立场为公开观点。

### 7.4 评测争议
- **benchmark gaming**:排行榜驱动融资,刷分是结构性激励。
- **可复现性危机**:agent 基准标准化不足,难分辨"真改进"与"评估选择差异"。
- **长程实验稀缺**:多数论文只测 1-5 轮自进化,**100 轮、1000 轮之后会怎样几乎无人测**。

### 7.5 理论边界(最该清醒的一条)
**在参数固定的前提下**,基于 in-context / 记忆 / skill 的"进化"受 ICL 表达力上界约束 —— agent 的学习被限制在"当前参数能表示的函数空间"内。把"上下文优化"等同于"真正的能力进化"是**范畴错误**。自进化 skill 能把 agent 推到其参数能力的**上沿**,但越不过那条线;越线仍需重训/参数更新。

### 7.6 批判性总结
- **最站得住脚**:① 验证必须扎根真实证据(三篇 + SkillLearnBench 互证);② 自进化必须掺真实信号防坍缩;③ skill 库规模化后检索是真瓶颈;④ 自生成代码的沙箱/供应链风险是现实而非演习。
- **最被高估/最危险**:① "自进化 → AGI"叙事忽视表达力天花板;② 缺长程实验却宣称"持续进化";③ 把"评估方法在进化"误当成"agent 在进化";④ 对 RSI 的治理乐观主义;⑤ 低估 reward hacking 在自改循环中的指数放大。

---

## 8. 对 expert-work 的意义(落地章节)

> 本节引用的仓库文件路径均真实存在(已探查)。结论:Expert Work 做自进化 skill,**地基已成,缺闭环 glue + 验证/后验蒸馏环节**。

### 8.1 现状盘点 —— 现成杠杆(可直接复用)

| 能力 | 锚点 | 对自进化的价值 |
|---|---|---|
| Skill 生命周期 + 作者归属 | `packages/expert-work-protocol/src/expert_work/protocol/skill.py`(`SkillStatus` DRAFT/ACTIVE/STALE/ARCHIVED;`SkillAuthoredBy = "human" | "agent"`;`SkillVersion` 带 `lazy_load`/`high_risk`/`content_hash`) | **关键**:协议层**已经为"agent 写的 skill"预留了一等身份**;DRAFT 闸门天然隔离自生成 skill |
| 高危 publish gate | Mini-ADR U-24(含 `exec_python`/`http`/`scripts/*` 的 skill DRAFT→ACTIVE 需 admin 审) | **关键护栏**:防 agent 给自己装后门,已预装 |
| 执行轨迹记录 | `services/orchestrator/src/orchestrator/trajectory/recorder.py` + `reader.py`(整 run 序列化为 JSONL,按 tenant/outcome/date 分区) | **SPARK 后验蒸馏的天然原料**:每个 run 的完整对话+工具调用都在 |
| Curation worker | `services/control-plane/src/control_plane/curation_worker.py`(扫 trajectory → 生成 curation candidate,正/负/失败三信号分类) | 自动把"值得学的轨迹"浮上来 |
| 经验数据协议 | `packages/expert-work-protocol/src/expert_work/protocol/eval_dataset.py`(`CurationCandidateRecord`/`CurationSignal`/`TrajectoryOutcome`/`EvalDatasetSource`) | 学习闭环的数据格式已架构化 |
| 长期记忆 + 凝结引擎 | `packages/expert-work-protocol/.../memory_item.py`(transient→consolidated→archived 状态机)+ MemoryConsolidator(Sprint #7,clustering+总结,防误学三重保护) | L1 自进化已基本就绪;防误学框架可直接复用到 skill |
| 反思节点 | `packages/expert-work-protocol/src/expert_work/protocol/reflection.py`(`ReflectionSpec`,触发点 milestone/tool_error/pre_final,per-run budget) | **EmbodiSkill 反思归因的现成接入点** |
| 沙箱 + 工具执行 | `services/orchestrator/src/orchestrator/tools/sandbox.py`(ExecPythonTool,超时/取消/销毁)、`registry.py`(`SideEffectLevel`、HIGH_RISK_TOOLS)、`assembly.py`(动态装配 skill/mcp/builtin 工具)+ gVisor 镜像 | 自生成代码的安全验证环境 + 动态加载新 skill |
| MCP(client-only) | `protocol/tenant_mcp_server.py`、`tools/mcp.py` | 外部能力扩展通道;skill 可声明 `mcp:*` 依赖 |
| 已有设计预约 | `docs/streams/STREAM-J-DESIGN.md` §15.7 + `docs/ITERATION-PLAN.md` **J.7b-1**(`author_skill`/`refine_skill`/`fork_skill`/`propose_skill_to_tenant` 四工具 + 完整审批流)、Sprint #4(curator 自动状态机)、Sprint #7(记忆凝结) | **方向已规划**,只是尚未实装 |

### 8.2 主要缺口(自进化启动的阻塞项)

1. 🔴 **J.7b-1 四工具未实装** —— agent 还不能在 run 内生成/改进 skill(仅设计文档)。
2. 🔴 **Skill 归属/可见性数据列缺** —— `visibility` / `created_by_agent_id` / `forked_from` 三列尚未落库;`agent_private` 隔离与 fork 溯源做不了。
3. 🔴 **trajectory → skill prompt 的蒸馏管道缺** —— 轨迹记录完整,但缺"LLM 从一段轨迹抽取可复用 skill 片段"的环节(这正是 SPARK 的核心)。
4. 🔴 **验证器/后验打分缺** —— 没有 CoEvoSkills 式 surrogate verifier,也没有 SPARK 式 PDI;自生成 skill 目前**无法被自动判好坏**。
5. 🔴 **skill 性能反馈缺** —— skill 没有 `usage_count`/`success_rate`/`user_rating`,agent 无从判断"该改哪个"。
6. 🔴 **reflect → skill 改进闭环缺** —— reflect 节点只在 run 内纠路线,洞见不流向 skill 改进。
7. 🟡 **aux 模型未 wire** —— MemoryConsolidator 的 LLM 判断仍是 no-op 占位(Mini-ADR U-33)。

### 8.3 把三篇论文映射到 Expert Work

| 论文思想 | Expert Work 对应 | 该怎么用 |
|---|---|---|
| **SPARK / PDI:后验、从轨迹蒸馏** | trajectory recorder/reader + curation worker + eval_dataset **已就绪** | **最高杠杆**:Expert Work 几乎是为"后验蒸馏"量身定做。补一个"从 curation candidate 轨迹蒸馏 skill 草案"的 LLM 管道,并用类 PDI 的"轨迹证据扎根度"打分作为 DRAFT→ACTIVE 的依据 |
| **CoEvoSkills:共进化验证器** | 缺 surrogate verifier | 在沙箱里跑自生成 skill,用一个"会进化的代理验证器"打分;短期可先用"在保留轨迹/回归任务上重放"的确定性验证替代,降低对模型裁判的依赖 |
| **EmbodiSkill:反思归因(内容错 vs 执行错)** | `reflection.py` 节点已存在 | 扩展 reflect:失败时先归因(skill 内容错 → 触发 `refine_skill`;只是没执行到位 → **保留** skill 并强化指导),避免"错杀好 skill" |

### 8.4 建议的最小可行演化路径(分优先级)

> 说明:此为研究报告的建议,不是已批准的实施计划。真要做,应按"设计先行 + 零技术债"另开 Stream。

- **P0(让 agent 有能力 + 有边界)**:实装 J.7b-1 四工具 + 落 `visibility`/`created_by_agent_id`/`forked_from` 列;默认 `agent_private` + DRAFT,沿用 U-24 高危 gate。**先给能力,但全程关在审批与隔离里。**
- **P1(后验蒸馏,对标 SPARK,最高杠杆)**:curation candidate → "从轨迹蒸馏 skill 草案"的 LLM 管道;给 candidate 加 `priority_score` / `suggested_skill_name` / `improvement_reason`。
- **P2(验证,对标 CoEvoSkills,补咽喉)**:沙箱内重放 + 确定性回归验证 → 给自生成 skill 打分;打分达标才允许 DRAFT→ACTIVE(初期可仍要人审)。
- **P3(归因闭环,对标 EmbodiSkill)**:reflect 输出经归因 → 决定 `refine_skill` 还是保留;接 skill 性能反馈(`usage_count`/`success_rate`)。
- **P4(治理与防坍缩)**:skill 版本锁 + content_hash 漂移告警 + 定期掺入真实/golden 信号防自噬;curator 自动 stale/archive 控制库膨胀;wire aux 模型并调防误学阈值。

### 8.5 安全护栏(不可省)
DRAFT 默认隔离 + U-24 高危 publish gate + gVisor 沙箱 + 工具级审计(`TOOL_CALL`/`TOOL_BLOCKED`)+ skill 版本锁与回滚 + curator RLS 豁免边界(见既有 memory:skill 表 ENABLE-only 不能加 FORCE)+ "后验/真实证据"约束防伪进化与坍缩。**核心哲学:自进化 ≠ 无界自改,而是"有边界、有审批、能追踪、被真实证据约束"的演化。**

---

## 9. 结论与开放问题

**结论**:
1. 自我进化 Skill 是 2026 年真实且重要的 agent 自改进范式,核心价值是"权重不动、能力可累积、可审计可迁移"。
2. 它的成败系于**验证/grounding** —— 你给的三篇论文(共进化验证器 / 后验轨迹证据 / 反思归因)给出了当前最有说服力的三种答案,共识是"必须扎根真实证据"。
3. 它有明确的天花板与真实风险(伪进化、坍缩、检索崩塌、自生成代码安全、固定参数表达力上界),护栏必须与能力同步建设。
4. 对 Expert Work:**地基基本就位**,最高杠杆是"后验蒸馏"(SPARK 思想 × Expert Work 现成 trajectory/eval 基建),缺口是把已有子系统串成闭环 + 补一个验证环节。
5. **三仓源码实证(§10)印证主论点**:OpenClaw/deer-flow/Hermes 在"生成 + 治理"上趋同且渐次成熟(Hermes 最完整),但**集体缺"效用验证"**——学术三篇正好补这块咽喉,Expert Work 凭服务端 trajectory/eval 基建最有条件补上并超越现有开源实现。

**开放问题**:
- 固定参数下,自进化的能力上沿到底在哪?何时必须回到参数更新?
- 没有 ground truth 时,验证器/PDI 的可靠性上界是多少?会不会被"刷"?
- 100→1000 轮长程自进化的稳定性(坍缩 vs 累积)几乎无人实测。
- skill 库百万级时,检索是"可优化"还是"架构性不可扩展"?
- 自进化与可审计/可解释如何兼得(单 skill 可读 ≠ skill 组合的涌现行为可控)?

---

## 10. 源码实证:三个真实 agent 怎么实现自进化 skill(OpenClaw / deer-flow / Hermes)

> 把抽象框架落到真实代码。本节探查了 `~/src/github` 下三个开源 agent,它们恰好覆盖 §1.2 自进化光谱的不同档位。**可信度**:deer-flow `skill_manage_tool.py` 作者**通读**、Hermes `background_review.py` 提示词作者**精读**(下文摘录均来自原文);其余锚点由 Explore 子代理逐仓核查(带 file:line)+ 作者 `ls` 目录核对;OpenClaw 的"不能自生成 skill"是经多关键词 grep 未命中 + 仅见 loader 文件得出的判断。

### 10.0 为什么挑这三个

它们是三种典型形态:**OpenClaw**(TypeScript,本地多渠道个人助手)、**deer-flow**(Python/LangGraph,research agent)、**Hermes**(Python,本地 CLI agent,以成熟工具调度著称)。三者对"自进化 skill"的实现强度依次递增,刚好是一组天然对照。

### 10.1 OpenClaw —— 完整 skill 基建,但 agent 不能自己写(L1~1.5)

- **Skill 载体/发现**:标准 `SKILL.md` + 扩展元数据(`OpenClawSkillMetadata`:install/requires/exposure/invocation),多源加载(workspace → 配置目录 → 用户级 → 插件),watch 热刷新,`SkillExposure` 控制是否进 prompt。锚点:`src/agents/skills/types.ts`、`workspace.ts`、`refresh.ts`、`frontmatter.ts`、`filter.ts`。
- **agent 能否自生成**:**❌ 不能**。grep `create_skill`/`author_skill`/`write_skill` 无命中;`skills-clawhub.ts` 只做"安装现有 skill"。agent 想新增能力,只能用通用 `edit`/`write` 工具手动拼出文件结构、等人审。
- **沉淀**:ClawHub lockfile 记 `installedVersion`/`installedAt`,但只有 enabled/disabled 两态,**无 draft/active/archived 状态机、无作者归属、无 content hash**。
- **检索**:启动全目录扫描 + 白名单/标签过滤,**无向量/RAG 选 skill**。
- **记忆**:**强项**——`memory-search.ts` 有混合检索(向量 0.7 + BM25 0.3),SQLite 存储,会话转录索引;`MEMORY.md` + `memory/*.md`。
- **定位**:**基建完备,但缺"生成闭环"**。它是一个绝佳反例 —— **拥有 skill 容器 ≠ 拥有自进化**。

### 10.2 deer-flow —— 干净的 agent skill CRUD + 安全门 + 历史归属(L1-L2)

- **agent 能否自生成**:**✅ 能**,通过一个工具 `skill_manage`(`tools/skill_manage_tool.py`,已通读)。6 个动作:`create / edit / patch / delete / write_file / remove_file`。开关由 `config/skill_evolution_config.py` 的 `SkillEvolutionConfig(enabled, moderation_model_name)` 控制(默认关闭)。
- **安全门(每次写都过)**:
  ```python
  # skill_manage_tool.py:53-59
  async def _scan_or_raise(content, *, executable, location):
      result = await scan_skill_content(content, executable=executable, location=location)
      if result.decision == "block":
          raise ValueError(f"Security scan blocked the write: {result.reason}")
      if executable and result.decision != "allow":
          raise ValueError(f"Security scan rejected executable content: {result.reason}")
  ```
  脚本类(`scripts/`)按 executable 更严判定。
- **历史与归属**:每次写都 `append_history` 一条 JSONL,**记录作者、来源线程、前后内容、扫描决策**:
  ```python
  # skill_manage_tool.py:41-50
  def _history_record(*, action, file_path, prev_content, new_content, thread_id, scanner):
      return {"action": action, "author": "agent", "thread_id": thread_id,
              "file_path": file_path, "prev_content": prev_content,
              "new_content": new_content, "scanner": scanner}
  ```
  并发安全:per-skill `asyncio.Lock`(`WeakValueDictionary`,空闲自动回收)。
- **蒸馏指导(prompt 级)**:`agents/lead_agent/prompt.py` 内置 "Skill Self-Evolution" 段——"任务用了 5+ 次工具调用 / 克服了非显然错误 / 用户纠正后才work / 发现可复用工作流"时建议建/改 skill;"prefer patch over edit","新建前先和用户确认"。
- **检索**:**亮点**——deferred tool search(`tools/builtins/tool_search.py`),三种查询语法 `select:Tool1,Tool2` / `+required terms` / 正则关键词,延迟工具先以名字出现在 `<available-deferred-tools>`,需 `tool_search` 取回 schema 才能调用。这是 §3 闭环里"检索复用"做得最好的一个。
- **记忆**:`agents/memory/updater.py` 对话后自动抽取新事实、去重持久化;LangGraph checkpointer 做 thread 隔离。
- **缺口**:安全扫描有了,但**无自动验证/测试(生成的 skill 能不能真用?)、无失败归因/reflection、无强制人审 gate**(`enabled=true` 后 agent 可无限建,仅靠扫描)。
- **定位**:**最干净的"agent skill CRUD + 来源审计 + tool RAG"参考实现**,适合直接照搬结构。

### 10.3 Hermes —— 目前最完整的自进化闭环(L3-L4)

- **agent 能否自生成**:**✅ 能,且机制最完整**。`tools/skill_manager_tool.py` 提供 `skill_manage`(create/edit/patch/delete/write_file),`_create_skill` 走 8 步(名称/分类/frontmatter/大小≤100k/重名/原子写/安全扫描/失败回滚)。
- **后台复查 daemon(最大亮点)**:`agent/background_review.py`(已精读)。每个 turn 后 fork 一个 agent,**隔离上下文、工具白名单只剩 memory + skill_manage**,问自己"这轮该不该存/改 skill",写库不碰主对话与 prompt 缓存:
  > "It runs with a tool whitelist limited to memory and skill management tools; everything else is denied at runtime."(文件 docstring)
- **蒸馏的 4 优先级规则**(原文):`1. UPDATE A CURRENTLY-LOADED SKILL → 2. UPDATE AN EXISTING UMBRELLA → 3. ADD A SUPPORT FILE(references/templates/scripts)→ 4. CREATE A NEW CLASS-LEVEL UMBRELLA`;强制"类级别命名",禁止 `fix-PR-#123` 这种一次性命名。
- **失败归因(= EmbodiSkill 思想的工程雏形)**:复查 prompt **显式列出"不要捕获"的东西**:
  > "Do NOT capture … Environment-dependent failures: missing binaries … 'command not found', unconfigured credentials … Negative claims about tools … Session-specific transient errors that resolved …"
  > "If a tool failed because of setup state, capture the FIX … never 'this tool does not work' as a standalone constraint."
  这正是"**技能内容错 vs 环境/执行失误**"的区分(EmbodiSkill 的核心),只不过用 prompt engineering 实现,而非论文里的结构化反思。
- **来源追踪**:`tools/skill_provenance.py` 用 `ContextVar` 标记写入来源,**只有在 `BACKGROUND_REVIEW` 上下文里建的 skill 才算 "agent-created"**;前台用户要求建的归用户。
- **安全分档**:`tools/skills_guard.py` 8 类威胁模式 + 信任分档 `INSTALL_POLICY`(builtin/trusted/community/agent-created),**agent-created 的 dangerous 发现 → 要求用户确认(ask)**。
- **生命周期策管**:`agent/curator.py` 定期(默认 7 天)跑,按闲置标 stale(30d)/archived(90d),**永不自动删、只归档**;重叠只"提示",不自动合并(保守哲学);pin 保护不被删但仍可改。
- **检索/记忆**:progressive disclosure 三层(`skills_list` 元数据 → `skill_view` 全文 → `skill_view(file)` 支持文件);9 个记忆 provider;nudge 机制每 N 轮触发复查;manifest v2 用 MD5 保护用户对 bundled skill 的修改。**无向量 RAG 选 skill**(纯文本+标签)。
- **缺口**:同样**无自动化验证/回归测试**——生成的 skill 不会被自动跑一遍验证正确性;归因是二元粗分,无深度诊断。
- **定位**:**生产级最完整的自进化闭环**(生成→隔离执行→prompt 级蒸馏与归因→来源/安全/生命周期治理),设计哲学保守——"宁可缺功能,不贸然自动化可能出错的决策"。

### 10.4 横向对照

| 维度 | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| Skill 载体 | SKILL.md + ClawHub | SKILL.md(public/custom) | SKILL.md(agentskills.io 标准) |
| **agent 自生成** | ❌ 不能 | ✅ `skill_manage` 6 动作 | ✅ `skill_manage` + 后台复查 daemon |
| 写时安全门 | 人审/权限策略 | ✅ security scanner(block/allow) | ✅ skills_guard 8 类 + 信任分档 |
| 历史/归属 | lockfile 版本 | ✅ JSONL(author/thread/scanner) | ✅ provenance(ContextVar agent/user) |
| 蒸馏来源 | — | prompt 级建议 | ✅ 后台复查 + 4 优先级规则 |
| **失败归因** | ❌ | ❌ | 🟡 prompt 显式排除环境/执行失败 |
| **自动验证** | ❌ | ❌ | ❌ |
| 生命周期 | enabled/disabled | — | ✅ curator stale/archived/pin |
| 检索 | 标签过滤 | ✅ deferred tool search(tool RAG) | progressive disclosure(无向量) |
| 记忆 | ✅ 混合向量检索 | ✅ memory updater | ✅ 9 provider + nudge |
| **光谱档位** | L1~1.5 | L1-L2 | L3-L4 |

### 10.5 与三篇论文的映射 —— 一个共同的洞

把三仓放进 §3 闭环,有一条扎眼的共性:**它们在 (1) 生成、(5) 沉淀(来源/安全/生命周期)、(6) 检索上各有建树,但在 (3) 自动验证 / grounding 上集体留白**——deer-flow、Hermes 的"验证"都止步于**写时安全扫描**(防恶意),而非**效用验证**(这 skill 真能把任务做对吗?)。没有一家做 surrogate verifier 或后验证据打分。

而你给的三篇论文,主攻的正是这块留白:

| 留白 | 论文补位 | 三仓里最接近的雏形 |
|---|---|---|
| 谁当裁判(无 ground truth) | **CoEvoSkills** 共进化验证器 | 无(三仓都靠"安全扫描 + prompt 自觉") |
| 验证依据是什么 | **SPARK / PDI** 后验环境证据 | 无(都靠先验 prompt 规则) |
| 失败怎么归因 | **EmbodiSkill** 内容错 vs 执行错 | **Hermes 后台复查 prompt**(已是工程雏形) |

**结论:工业界(三仓)已经把"生成 + 治理"做得相当成熟,学术前沿(三篇)正好补的是它们共同缺的"效用验证"咽喉。** 这与本报告主论点完全吻合——自进化的成败系于验证,而验证恰是最难、也最晚被工程化的一环。

### 10.6 对 Expert Work 的增量启示

- **可直接照搬的成熟模式**:① deer-flow 的 `skill_manage`(6 动作 + 写时安全扫描 + JSONL 历史含 author/thread/scanner + per-skill 锁)——这正是 Expert Work J.7b-1 四工具的现成蓝本;② Hermes 的 **后台复查 daemon + 受限工具集**(隔离 fork、只放 memory+skill 工具)——Expert Work 可用它实现"run 后自动蒸馏 skill"而不污染主对话;③ Hermes 的 **provenance(agent vs user)+ curator 生命周期 + 信任分档 guard**——Expert Work 已有 `SkillAuthoredBy`、curator 状态机、U-24 高危 gate,可对齐增强。
- **Expert Work 的差异化机会**:三仓**集体缺的"效用验证"**,恰是 Expert Work 最该押注、也最有条件做的——因为 Expert Work **已有 trajectory recorder + eval_dataset + curation worker**(三仓都没有这种服务端轨迹基建)。把它们接成 **SPARK 式后验蒸馏 + 重放验证**,Expert Work 就能做出三个本地 agent 都做不到的"被真实证据验证过的自进化 skill"。这把 §8.4 的 P1/P2 从"对标论文"升级为"对标论文 + 超越现有开源实现"。

---

## 11. 参考文献

### 已逐条核对原文(本报告核心)
- CoEvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification — https://arxiv.org/abs/2604.01687
- Evidence Over Plans: Online Trajectory Verification for Skill Distillation (SPARK / PDI) — https://arxiv.org/abs/2605.09192 · 代码 https://github.com/EtaYang10th/spark-skills
- EmbodiSkill: Skill-Aware Reflection for Self-Evolving Embodied Agents — https://arxiv.org/abs/2605.10332

### 公认真实(经典奠基 + 官方)
- Voyager — https://arxiv.org/abs/2305.16291
- Reflexion — https://arxiv.org/abs/2303.11366
- Generative Agents — https://arxiv.org/abs/2304.03442
- ExpeL — https://arxiv.org/abs/2308.10144
- Toolformer — https://arxiv.org/abs/2302.04761
- ADAS (Automated Design of Agentic Systems) — https://arxiv.org/abs/2408.08435
- Gödel Agent — https://arxiv.org/abs/2410.04444
- Darwin Gödel Machine — https://arxiv.org/abs/2505.22954
- A-MEM: Agentic Memory for LLM Agents — https://arxiv.org/abs/2502.12110
- Anthropic — Equipping agents for the real world with Agent Skills — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Anthropic — When AI Builds Itself (Recursive Self-Improvement) — https://www.anthropic.com/institute/recursive-self-improvement

### 检索来源,未逐条核验(综述/2026 skill 系列/工程/安全)
- A Survey of Self-Evolving Agents — https://arxiv.org/abs/2507.21046
- Awesome-Self-Evolving-Agents — https://github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents
- Truly Self-Improving Agents Require Intrinsic Metacognitive Learning — https://openreview.net/forum?id=4KhDd0Ozqe
- Your Agent May Misevolve: Emergent Risks in Self-evolving LLM Agents — https://arxiv.org/abs/2509.26354
- OWASP Agentic Skills Top 10 — https://owasp.org/www-project-agentic-skills-top-10/
- OpenAI — Introducing AgentKit — https://openai.com/index/introducing-agentkit/
- Devin 2025 Performance Review — https://cognition.ai/blog/devin-annual-performance-review-2025
- Cursor 2.4 (Subagents + Skills Marketplace) — https://cursor.com/changelog/2-4
- Letta — Agent Memory — https://www.letta.com/blog/agent-memory
- LangMem SDK — https://www.langchain.com/blog/langmem-sdk-launch
- Self-Improving Agent (Claude Code skill) — https://mcpmarket.com/tools/skills/self-improving-agent
- Karpathy / agentic engineering(综述)— https://www.nextbigfuture.com/2026/03/andrej-karpathy-on-code-agents-autoresearch-and-the-self-improvement-loopy-era-of-ai.html
> 其余 2026 年 skill-* 论文(SkillOS / MemSkill / MUSE-Autoskill / SkillComposer / Trace2Skill / Graph-of-Skills / SkillsBench / SkillLearnBench / SkillRet / 供应链安全系列)由自动检索得到、未逐条打开原文核验,引用其精确数字前请自行复核。

### Expert Work 内部锚点(真实存在)
- `packages/expert-work-protocol/src/expert_work/protocol/skill.py`、`eval_dataset.py`、`memory_item.py`、`reflection.py`、`tenant_mcp_server.py`
- `services/orchestrator/src/orchestrator/trajectory/recorder.py`、`reader.py`;`tools/sandbox.py`、`registry.py`、`assembly.py`、`mcp.py`
- `services/control-plane/src/control_plane/curation_worker.py`
- `docs/streams/STREAM-J-DESIGN.md` §15.7;`docs/ITERATION-PLAN.md` J.7b-1 / Sprint #4 / Sprint #7;Mini-ADR U-24 / U-33

### 三仓源码锚点(§10,均在 `~/src/github/` 下,已核对存在)
- **OpenClaw**(`openclaw/`):`src/agents/skills/`(`types.ts`/`workspace.ts`/`refresh.ts`/`frontmatter.ts`/`filter.ts`)、`src/agents/skills-clawhub.ts`、`src/agents/memory-search.ts`、`src/agents/tool-catalog.ts`
- **deer-flow**(`deer-flow/backend/packages/harness/deerflow/`):`tools/skill_manage_tool.py`(通读)、`config/skill_evolution_config.py`、`skills/security_scanner.py`、`skills/storage/local_skill_storage.py`、`tools/builtins/tool_search.py`、`agents/lead_agent/prompt.py`、`agents/memory/updater.py`
- **Hermes**(`hermes-agent/`):`agent/background_review.py`(精读)、`agent/curator.py`、`tools/skill_manager_tool.py`、`tools/skills_guard.py`、`tools/skill_provenance.py`、`agent/skill_commands.py`、`tools/skills_tool.py`、`tools/skills_sync.py`
