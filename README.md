# AI Incident Relay

供 Codex 调用的公开事实核查 Skill：拆分主张 → 读取来源 → 关联证据 → 复核回答 → 导出 Markdown 与 JSON。

**当前为可运行的演示版本，案例和对照题仍待人工审核。** 不承诺绝对真相、消除幻觉或已证明准确率提高。

## 案例入口

- [CASE-STUDY.md](CASE-STUDY.md)：面向 AI Infrastructure、DevRel 与 Technical Research 的项目案例，说明目标用户、Failure Mode Matrix、可复现验证和当前限制。
- [公开演示包](cases/hf-demo-public/REVIEW.md)：真实事件公开短引文案例，人工审核状态仍为 pending。

## 快速开始（macOS）

1. 安装 [Python 3.12 或更新版本](https://www.python.org/downloads/macos/)。安装 [Codex 所在桌面应用](https://learn.chatgpt.com/docs/app)，登录你的账户。Python 脚本不需要 API 密钥；模型调用使用你自己的宿主访问权限。
2. 打开“终端”，执行以下命令。若 Git 提示安装开发工具，按系统提示完成后继续。

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/0xClareYang/ai-incident-relay.git
cd ai-incident-relay
python3 --version
python3 -m unittest discover -s tests -v
python3 demo.py
```

Python 必须为 3.12+。项目只用标准库，**不需要 pip install、npm install、数据库或服务器**。Windows 将 `python3` 换为 `py -3`。

`demo.py` 打印新建的 `runs/replay-demo-时间/` 路径。打开其中：

- `01-rejected/validation.json`：实际检查出的伪造引文错误。
- `02-corrected/report.md`：修正后的阅读报告。
- `02-corrected/ledger.json`：证据账本；随附 `snapshots/`，可离线复查。

**这一步是脚本回放，不代表模型实际调用了 Skill。** 下面单独验证真实调用。

## 在 Codex 中使用 Skill

在桌面应用里把刚克隆的项目文件夹打开为本地项目，启动新对话，选择/显式提及 `ai-incident-relay`。项目 Skill 位于 `.agents/skills/ai-incident-relay/SKILL.md`。[官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)说明了本地目录和显式调用方式；不同客户端的选择入口可能不同。

可直接输入：

```text
请显式使用本项目的 ai-incident-relay Skill，以 Replay 模式核查：
【测试输入】有人说 Hugging Face 所有用户数据都被读取，METR 独立调查还确认了 OpenAI 复盘的全部细节。仅凭这些材料，能确认我的私人账户今天有没有受影响吗？

使用 cases/hf-demo-public/manifest.json 和 snapshots 中的原始短引文，
不要读取示例 ledger.json 作为答案，不联网。
请拆分主张，保留声明归属、范围和不确定性，
在 runs/my-replay/ 中生成新的账本并实际运行检查器，导出 Markdown 和 JSON。
说明来源是短引文子集，人工审核仍为 pending。
```

如果选择器中没有出现 Skill，输入：

```text
请读取并显式使用当前项目的 .agents/skills/ai-incident-relay/SKILL.md，
然后执行上面的 Replay 测试。请如实记录这是显式路径加载，不要称为自动发现或自动触发。
```

若模型或工具不可用，保留错误，不要用 `demo.py` 的固定结果冒充一次成功的 Skill 调用。本机 Windows 开发验收已验证桌面显式路径调用和本地发现；macOS 的真实执行仍需在你的电脑验收。

## 两种模式

| 模式 | 来源 | 用途与边界 |
|---|---|---|
| Replay | 保存的来源快照 | 复现流程，必须显示截止日期和未重新联网；超范围返回证据不足 |
| Live | 本次真实搜索和读取 | 生成新账本；工具/网站失败明确记录，不自动切换 Replay |

公开仓库附带 `cases/hf-demo-public`：从已获取内容按位置截取的短引文，带来源 URL、时间、原始返回哈希和定位。它不是完整网页档案，也不足以核查任意问题。完整来源返回、本机原始日志和历史运行未上传。

Live 需要宿主提供实际搜索/网页读取工具。可让模型直接采集公开 HTML/纯文本：

```bash
python3 relay.py capture --url https://huggingface.co/blog/agent-intrusion-technical-timeline --out runs/my-live/snapshots --source-id HF_NEW --title "Hugging Face technical timeline" --publisher "Hugging Face"
```

采集退出码 0 表示取得文本，2 表示失败/不支持。仍需排除登录、反机器人和导航页面。直接读取失败但宿主网页工具可读时，明确记录后者的真实返回及 partial 范围；不使用搜索摘要或模型改写冒充原文。

## 数据与程序边界

`ledger.schema.json` 定义 claims、sources、evidence、answer_items 和 run；`relay.py` 提供 capture、quote、validate、export。详细字段见 Skill 的 [workflow.md](.agents/skills/ai-incident-relay/references/workflow.md)。

```bash
python3 relay.py validate cases/hf-demo-public/ledger.json
python3 relay.py export cases/hf-demo-public/ledger.json --out runs/manual-export-01
```

输出目录不能重复覆盖。程序检查字段、状态、ID、关联、哈希及引文位置；export 还要求语义复核记录。失败时只给限制及可用来源。模型负责判断语义支持、实体、限定、冲突和来源链；人工负责最终案例审核。模型最多两轮修订由 Skill 指令约束，脚本不自动重试。代码不能保证宿主遵循指令或证明语义复核记录真实。

采集限制标准 HTTP(S) 协议/端口、URL 参数、重定向、大小与超时，并检查公开 IP、固定连接地址及 TLS 主机名验证。网页只当不可信数据，不执行其代码，不绕过访问限制。公开分享只选经过审核的报告和必要短引文。

## 测试、录屏和人工审核

- [DEMO.md](DEMO.md)：录制顺序、三组可复制输入、预期观察和口播范例。
- [案例审核表](cases/hf-demo-public/REVIEW.md)：仍为 pending；通过后才可称为人工审核案例。
- [对照测试题](evaluation/questions.json)与[评分规则](evaluation/rubric.md)：尚未执行 A/B，不能宣称效果提升。

九题完整对照需要另行采集并人工审核足够的来源，公开短引文包未覆盖所有题。审核通过后运行 `python3 prepare_evaluation.py --case /path/to/reviewed-case`，准备器在项目外建立隔离 A/B 输入目录，避免泄露示例答案。Python 检查通过不等于人工评分完成。

项目不包含录像文件。建议用 macOS `Shift + Command + 5` 录制所选区域，保留原片、真实工具记录和耗时；剪辑加速注明。无证据时保留不确定性，不伪造日志或进度。
