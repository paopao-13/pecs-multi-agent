# 失败案例集（Failure Cases Gallery）

> **为什么有这个文档**：准确率数字只能说明"对了多少"，而**工程师的功力体现在怎么处理失败**。
> 下面 5 个案例全部来自 **2026-07-19 GAIA 官方 Level 1（53 题）PECS 实跑**的真实输出
> （评测脚本已把逐题详情存于 `results/gaia_official_multi_agent.json`）。
> 该次运行 PECS 答对 14/53，失败 39 题；失败分类为：**知识检索放弃(giveup) 26 题、
> 附件/多模态 6 题、计算/数值 6 题、数据泄露跳过 1 题**。
> 这 39 个失败**直接驱动了四杠杆提分方案**（L1 多模态 / L3 检索增强 / L4 放弃即重试）。
>
> ⚠️ 本文件为**升级前快照**。重跑 `python run_gaia_official.py --dump-failures` 后，
> 最新失败集会导出到 `results/gaia_failures.json`，可据此刷新本页。

## 失败分类总览（升级前 53 题）

| 失败类型 | 数量 | 说明 | 对应修复杠杆 |
|----------|:----:|------|--------------|
| 知识检索放弃（giveup） | 26 | 模型输出"无法确定/无法回答"，未找到论文/视频/网页信息 | L3 真实搜索 + L4 放弃即重试 |
| 附件 / 多模态缺失 | 6 | 图片/音视频题因无多模态后端被 skip（tok=0） | L1 多模态后端 |
| 计算 / 数值答非所问 | 6 | 算错或答了过程没给最终数字 | L3 强制 python 实算 + 答案抽取 |
| 数据泄露跳过 | 1 | 答案疑似出现在题干，安全跳过 | （良性，非缺陷） |

---

## 案例 1：数值答案答非所问（计算/数值类）

- **问题（截）**：If Eliud Kipchoge could maintain his record-making marathon pace indefinitely, how many thousands of hours would it take him to run the distance from Earth to the Moon at its closest?（四舍五入至最近千小时）
- **PECS 预测（截）**："根据用户问题，需要计算 Eliud Kipchoge 以他的马拉松纪录配速跑完地球到月球最近距离所需的时间（以千小时为单位，四舍五入到最近千小时）。**关键信息提取与计算：** ……"
- **标准答案**：`17`
- **Token / 耗时**：9079 / 24.7s
- **为何失败**：模型输出了完整推演过程但**未收敛到最终数字 17**，且 Kipchoge 纪录配速等中间量可能算错。
- **Critic 为何没拦**：Critic 评分维度是 completeness/consistency，**没有"最终答案是否为单个数值"的硬性校验**；长文本看起来"完整"，于是放行。
- **已落地的修复方向**：① L3 Planner 规则"数值必用 python 实算"强制算到底；② 增加 final-answer extractor（正则抽取末位数值/单位）再与 gold 比对；③ 评分层加"答案格式吻合度"维度。

## 案例 2：知识检索放弃——论文数据未搜到（giveup 类，26 题代表）

- **问题（截）**：What was the volume in m³ of the fish bag that was calculated in the University of Leicester paper "Can Hiccup Supply Enough Fish to Maintain a Dragon's Diet"?
- **PECS 预测（截）**："根据所有执行步骤的结果，**无法确定**莱斯特大学论文……中计算的鱼袋体积（以 m³ 为单位）。具体原因如下：- 所有搜索步骤均未能找到该论文中关于鱼袋体积的相关数据……"
- **标准答案**：`0.1777`
- **Token / 耗时**：7195 / 28.2s
- **为何失败**：默认搜索（DuckDuckGo/mock）未命中该冷门论文，模型直接冻结为"无法确定"。
- **Critic 为何没拦**：这是**升级前的 L4 漏洞**——Critic 不识别"无法确定"这类放弃型答案，于是把放弃当成正经结论送出。
- **已落地的修复方向**：① **L4 Synthesizer 放弃型答案检测**：命中"无法确定/无法回答"触发一次强制重规划，换搜索 API / 换提示词再试；② **L3 Tavily 真实搜索 API**：提供接地摘要，冷门论文更易命中。

## 案例 3：多模态缺失——YouTube 视频无法处理（giveup 类）

- **问题（截）**：In the video https://www.youtube.com/watch?v=L1vXCYZAYYM, what is the highest number of birds of the same species visible at the same time?
- **PECS 预测（截）**："根据所有步骤的执行结果，**无法确定**视频……中同时出现在镜头中的最高鸟类物种数量。所有尝试获取该视频具体内容（标题、描述、评论）的步骤均未能返回有效信息……"
- **标准答案**：`3`
- **Token / 耗时**：14046 / 59.6s
- **为何失败**：视频/音频类附件**需要多模态后端转写或抽帧**，升级前无该后端，模型只能用文本搜索硬蹭，最终放弃。这是预算/耗时都高（14k tok / 60s）却无效的典型。
- **Critic 为何没拦**：同上 L4 漏洞；且 14k token 的高投入让 Critic 误以为"已充分尝试"。
- **已落地的修复方向**：**L1 多模态后端（PEC_VISION_\*）**：视频抽帧/音频转写后注入上下文；未配置时优雅降级 skip（零回归）。

## 案例 4：知识检索放弃——论文作者未找到（giveup 类）

- **问题（截）**：Of the authors (First M. Last) that worked on the paper "Pie Menus or Linear Menus, Which Is Better?" …
- **PECS 预测（截）**："根据所有步骤的执行结果，**无法回答您的问题**。具体原因如下：1. **论文未找到**：所有针对论文……的搜索（包括 2015 年版本、作者信息、CHI 会议等）均未返回有效结果……"
- **标准答案**：`Mapping Human Oriented Information to Software Age…`（作者机构名）
- **Token / 耗时**：8335 / 25.8s
- **为何失败**：冷门论文标题变体多，默认搜索未命中；模型未做多跳检索（先找会议→再找作者页）。
- **Critic 为何没拦**：L4 漏洞；且"论文未找到"在语义上像"已尽力"，Critic 未强制换工具。
- **已落地的修复方向**：① L3 "先 search 后 web_browse 多跳"规则；② L4 放弃型触发重规划；③ 答案归一化层对机构名做宽松匹配。

## 案例 5：多模态附件被 skip（附件/多模态类，tok=0）

- **问题（截）**：Review the chess position provided in the image. It is black's turn. Provide the best move in algebraic notation.
- **PECS 预测（截）**：`（空）`
- **标准答案**：`Rd5`
- **Token / 耗时**：0 / —
- **为何失败**：图片附件在无多模态后端时被 harness 直接 skip（tok=0，不假跑），属**诚实的"不会就跳过"**，而非错误答案。
- **Critic 为何没拦**：无需——本就未作答。这类题的修复是"能力补全"而非"纠错"。
- **已落地的修复方向**：**L1 多模态后端**：图片经视觉模型识别为文本（棋盘坐标/局面描述）后注入，问题变为可解。未配置时保持 skip，**不污染现有跑分**。

---

## 如何生成最新失败集（重跑后）

```bash
# 1. 配好 LLM Key（+ 可选 PEC_VISION_* / PEC_SEARCH_*）
# 2. 跑全量对比并把 PECS 失败题导出
python run_gaia_official.py --dump-failures
#   → results/gaia_failures.json（含每题 问题/预测/gold/token/耗时）
```

然后按本页结构刷新 5 个案例，重点观察：**L1/L3/L4 升级后，案例 2/3/4/5 是否被回收**。
若重跑后 giveup 类从 26 题显著下降，即是四杠杆提分最硬的证据——比任何准确率数字都更有说服力。
