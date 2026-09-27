# 段1 验收用例：接口规格未写清之处（待定问题清单）

> **用途**：凡 `docs/spec/04_段1接口规格_v0.1.md`（下称"规格"）没有写清、测试员需要猜才能写测试的地方，逐条列在这里，写明**暂定理解**和**受影响的测试**。测试员不自行发明设计。
> **请 Fred（必要时连同架构师）逐条裁定**：同意暂定理解，或给出别的写法。凡改动暂定理解的，须在合入 main、锁定之前改测试。
> **分两类**：第一节是测试里**已按暂定理解写了断言**的（共 17 条）；第二节是规格没写、**测试没有碰**的（共 7 条），只作提醒。
> 测试代码里以"待定 Qn"标出对应位置。

---

## 一、已按暂定理解写入测试的

| 编号 | 问题（规格出处） | 暂定理解 | 受影响的测试 |
| :-: | :-- | :-- | :-- |
| Q1 | 门1 确认单实测③的"云端岗位"一行（Claude Code 云会话尝试写签字目录，记"不适用（D-206①）"）要不要列入人工清单？本次交办的人工清单只点了三条 | **没有列入** `manual_checks.md`，只在其第三节注明去向 | `manual_checks.md` |
| Q2 | 规格第 2 节只说"加 `--json` 输出机器可读的结果"，除 `pack`（输出完整清单）外，没有规定 `status`、`check`、`verify`、`sign` 的 JSON 结构 | 加 `--json` 时，标准输出**整体是一份合法 JSON**（不夹中文提示行）；测试只断言能解析，**不断言结构**，判定全靠退出码和磁盘上的记录 | `test_00_common.test_json_output_is_json`；全部用例通过 `--json` 调用 |
| Q3 | 规格第 2 节"不在 git 仓库内即报错"，没给退出码 | 退出码**不为 0**，且不写任何文件；不断言具体是几 | `test_00_common.test_outside_git_repo_is_error` |
| Q4 | `TL_NOW` "可以固定当前时间"，没说写入时是否原样照抄 | `created_at`、`signed_at` 与 `TL_NOW` 的字符串**完全相同**（测试用 `2026-10-03T21:14:05+08:00`） | `test_02_pack.test_manifest_location_and_fields`、`test_03_sign.test_confirm_writes_one_line` |
| Q5 | 清单字段 `created_by` 由 `tl pack` 生成时应写什么（段1 设计写"生成者（导引技能）"，规格没给取值） | 只要求是**非空字符串**，不断言具体值 | `test_02_pack.test_manifest_location_and_fields` |
| Q6 | 规格 4.2"不得指向 `packages/` 目录本身"：违反时退出码是几？指哪个任务的 `packages/`？ | 按"参数不合法"处理，退出码 **1**；测试只用本任务的 `.tianlong/work/qf-001/packages/` | `test_02_pack.TestPackRejects.test_file_in_packages_dir` |
| Q7 | 文件指纹"算出每个文件的指纹"：按原始字节还是先统一换行？"按路径字母序"：按什么比较？ | 文件指纹＝文件**原始字节**的 SHA-256，不做换行转换；排序＝路径字符串按字符编码逐字比较（Python 默认排序）。测试所用路径都能在 ASCII 部分分出先后，避开中文排序的歧义 | `test_02_pack.test_file_entries`、`test_multiple_attachments_sorted` |
| Q8 | sign 第 3 步"决定为退回但没给理由"：`--reason ""`（空串）算不算"没给"？ | 空串算没给，退出码 **1** | `test_03_sign.TestSignStep3RejectReason.test_reject_empty_reason` |
| Q9 | verify 按任务核对时"取最严重的退出码"：3、4、5 谁更严重？ | 按**数值取最大**（5 > 4 > 3）。例：一个包签后改件（3）、另一个包未签（4）→ 整体 **4** | `test_04_verify.TestVerifyTask.test_mixed_changed_and_unsigned` |
| Q10 | 签字记录文件**还不存在**时 verify 一个包：算"未签字"还是"记录损坏"？ | 算未签字，退出码 **4**；status 显示"未签" | `test_04_verify.test_unsigned_no_signature_file`、`test_06_status.test_state_unsigned` |
| Q11 | 签字记录里有一行不是 JSON：规格 5.3 第 5 步只写"校验链条"，规格 5.4 只写"链条完好"，退出码 5 的定义里有"格式错误"，但没说 verify、sign 读到坏行时按哪条处理 | 按记录损坏，退出码 **5**（sign 与 verify 相同） | `test_03_sign.TestSignStep5Chain`、`test_04_verify.TestVerifyChain` |
| Q12 | 进度卡 `readiness` 为"适用未就绪"时 `exception_ref` 必填：空串算不算填了？ | 空串算没填，check 退出码 **2** | `test_05_check.test_not_ready_empty_exception_ref` |
| Q13 | 交接卡"六个栏目都不为空"：怎么认栏目？整行删掉算什么？只填空白算什么？ | 按模板表格**第一列文字的开头**认栏目（本步／产出／检查结果／未决事项／下一步／令牌移交），填写内容在第二列；**整行缺失**、**只有空白（含全角空格）**都算空，退出码 **2**；文书头各行不在六栏之内，测试夹具把它们也填满，不作反面测试 | `test_05_check.TestCheckHandover` |
| Q14 | status 的中文输出没有规定版式；签字状态怎么和包对应？ | 签字状态文字（未签／已确认／退回／签后文件已变）与该包的**包编号出现在同一行** | `test_06_status.TestStatusContent` |
| Q15 | status 不带 `--task` 时，某一个任务的进度卡不合法，整体退出码是几？ | 退出码 **2** | `test_06_status.test_invalid_progress_listing` |
| Q16 | 输出编码：Windows 控制台默认编码可能不是 UTF-8，规格只规定了文件编码 | 测试给子进程设 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，按 UTF-8 读输出；**Windows 控制台的实际显示不在自动测试之内** | 全部用例（`seg1_support.py` 的 `run_tl`） |
| Q17 | sign"屏幕回显写入的内容"：回显到什么程度？ | 不带 `--json` 时，屏幕输出至少含**包编号、"已确认"、指纹码** | `test_03_sign.test_echo` |

## 二、规格没写、测试没有碰的（只作提醒）

| 编号 | 问题 | 测试怎么避开 | 建议 |
| :-: | :-- | :-- | :-- |
| Q18 | 假设日志哪些字段必填？规格 4.4 只写"字段见段1 设计 v0.5 §4"，没分必填与选填 | 夹具每行都填齐设计 §4 的全部字段；只测 `cost`、`low_reason`、JSON 格式 | 请架构师列出必填字段，再补反面用例 |
| Q19 | **设计与规格的两处写法不一致**：①段1 设计 3.3 写 `tl sign <包编号> 退回 "<一句理由>"`（理由是位置参数），规格 5.3 写 `--reason "<理由>"`；②段1 设计 §4 固定理由写作"纯命名、纯格式、不涉状态、接口、数据与权限"，规格 4.4 为四个固定值，第四个是"不涉接口数据与权限" | 两处都**以规格为准**：理由只用 `--reason` 传；`low_reason` 只接受规格的四个值，"不涉接口、数据与权限"（带顿号）判为不合法 | 请确认以规格为准，并顺带订正段1 设计 |
| Q20 | **verify 是否要重算清单指纹？** sign 第 1 步明写"重新计算每个文件的指纹和清单指纹"；verify 五项里只写"签字里的指纹码与清单一致""文件当前指纹与清单一致"，没写重算清单指纹。按字面，签后只改清单正文（如 `summary`、`step`）而不动两个指纹字段、不动文件，verify 会判通过 | 没写这条测试（写了就是替规格做设计） | **建议架构师补明**：verify 也重算清单指纹，不符判 3 |
| Q21 | sign／verify 一个**不存在的包编号**、或包编号格式不合法时，退出码是几？ | 没写这条测试 | 请在规格 5.3、5.4 补一句 |
| Q22 | check 的编号核对：包编号格式合法、但任务编号部分与所在任务不一致（如 `qf-001/packages/` 下有 `qf-002-g1-01.json`），算不算不合法？ | 没写这条测试 | 请在规格 5.5 补一句 |
| Q23 | check 时任务目录下**没有** `assumptions.jsonl` 或 `forms/`，算合法还是不合法？进度卡嵌套字段（`token_holder` 下各项、`current_step`、`baseline_commit` 的格式）要不要核对？ | 夹具总是建齐这些文件；进度卡直接用段1 设计第 2 节的示例原样；不作这些反面测试 | 请在规格 4.1、5.5 补一句 |
| Q24 | pack 要不要核对 `--step` 的代号格式？要不要求任务的进度卡已存在？ | 夹具总是先建好合法进度卡；`--step` 只用 `S0-INI`、`S1-SPD` | 请在规格 5.2 补一句 |
