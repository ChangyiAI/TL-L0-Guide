# Codex 建设说明：本机目录、GitHub 仓库与模板仓库骨架（v1，2026-09-27）

> **给谁**：本文件分两部分。第一部分由 Fred 手工完成（约 10 分钟）；第二部分整段复制给 Codex 桌面版执行。
> **依据**：`10` 卷三 C 统一骨架与命名规则（D-208）、D-206（环境基线）、D-205（规程包建造规则）。
> **边界**：本次只建目录、说明文件、规范文件和空白模板，**不写任何脚本、钩子或技能**。写第一个脚本或钩子才算"规程包建造开工"（D-205），那是之后的事。

---

## 第一部分：Fred 手工完成

| 步 | 做什么 | 怎么确认 |
| :-: | :-- | :-- |
| 1 | GitHub 网页右上角头像 → Settings → Organizations → **New organization** → 选 **Free** → 登录名填 `ChangyiAI`（被占用就依次试 `ChangyiZhiyuan`、`CYZY-Tech`）→ 显示名称填"长忆智元（北京）科技有限公司" | 左上角可以切换到这个组织 |
| 2 | 在组织里新建两个**私有**空库：`TL-L0-Guide`、`qiaofeng-bid`；**不要**勾选"添加 README"，保持完全为空 | 组织首页能看到两个空库 |
| 3 | 在电脑上安装 GitHub 命令行工具（PowerShell 运行 `winget install GitHub.cli`），然后运行 `gh auth login`，按提示用浏览器登录 | 运行 `gh auth status` 显示已登录 |
| 4 | 下载本对话的附件 `TL-L0-Guide_种子.zip`，解压到桌面 | 桌面上有 `TL-L0-Guide` 文件夹 |

完成后，把下面第二部分整段发给 Codex 桌面版。

---

## 第二部分：交给 Codex 的指令（整段复制）

```text
请按以下步骤在本机完成天龙仓库的目录建设。只建目录、复制文件、做 git 初始化与推送；
不写任何脚本、钩子或技能文件；任何一步失败就停下来报告，不要自行变通。

【A. 本机顶层目录】
1. 创建 D:\Tianlong\（如果没有 D 盘，改用 C:\Tianlong\，并在报告中说明）。
2. 创建 C:\TianlongSign\ 和 C:\TianlongSign\qiaofeng-bid\（签字目录，先建空目录）。
3. 确认以上目录都不在 OneDrive 等同步文件夹里。

【B. 模板仓库 TL-L0-Guide】
4. 把桌面上的 TL-L0-Guide 文件夹整体复制到 D:\Tianlong\TL-L0-Guide\，保持内部结构不变。
5. 如果存在 C:\TianlongUpstream\ 下的 Superpowers 备份，把其中版本目录整体复制到
   D:\Tianlong\TL-L0-Guide\upstream\ 下，目录名保持 superpowers-<版本号>；不存在就跳过，并在报告中说明。
6. 在 D:\Tianlong\TL-L0-Guide\ 内执行：
   git init -b main
   git config core.longpaths true
   git config merge.ff only
   git add -A
   git commit -m "骨架：目录、规范、空白模板、段1设计（D-208）"
   git remote add origin https://github.com/ChangyiAI/TL-L0-Guide.git
   git push -u origin main
   （如果组织登录名不是 ChangyiAI，先问我实际名称再推送。）

【C. 乔峰服务核仓库 qiaofeng-bid】
7. 在本机查找现有的 qiaofeng-bid 仓库（可能在 WSL2 或 Windows 的任意位置），报告它的完整路径、
   所有分支名和最近 5 条提交记录。找到之后先停下来，等我确认再继续第 8 步。
8. （我确认后）按以下方式迁移，保留全部分支、标签和历史：
   git clone --bare <原仓库路径> D:\Tianlong\_qiaofeng-bid-temp.git
   cd D:\Tianlong\_qiaofeng-bid-temp.git
   git push https://github.com/ChangyiAI/qiaofeng-bid.git --all
   git push https://github.com/ChangyiAI/qiaofeng-bid.git --tags
   cd D:\Tianlong
   git clone https://github.com/ChangyiAI/qiaofeng-bid.git D:\Tianlong\qiaofeng-bid
   在 D:\Tianlong\qiaofeng-bid\ 内执行：
   git config core.longpaths true
   git config merge.ff only
   git fetch --all
   然后删除临时目录 D:\Tianlong\_qiaofeng-bid-temp.git。
   不删除、不修改原位置的仓库；不调整 qiaofeng-bid 内部的目录结构。

【D. 报告】
9. 报告以下内容：
   - D:\Tianlong\TL-L0-Guide\ 的完整目录树（含隐藏文件）；
   - 两个仓库各自的 git remote -v、git log --oneline -5、git config merge.ff 的输出；
   - C:\TianlongSign\ 的目录树；
   - 有没有跳过或失败的步骤，原因是什么。
```

---

## 第三部分：Fred 核对（Codex 报告回来之后）

| 核对项 | 通过标准 |
| :-- | :-- |
| GitHub 组织首页 | `TL-L0-Guide` 里能看到 `docs`、`src`、`tests`、`upstream` 四个目录和 README；`qiaofeng-bid` 的历史提交数与原仓库一致 |
| 本机 | `D:\Tianlong\` 下只有两个仓库文件夹；`C:\TianlongSign\` 存在 |
| 合并设置 | 两个仓库的 `merge.ff` 都是 `only` |

核对通过后，把 Codex 的报告贴回 Claude 的本对话，由 Claude 在自举记录里补记一行。原来位置的 qiaofeng-bid 仓库，等新位置用过一段时间、确认无误后再由你决定是否删除。
