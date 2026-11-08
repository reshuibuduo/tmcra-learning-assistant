# TMCRA Learning Assistant · 随堂学习助手

把会议或课堂中的声音变成**字幕、概念解释，以及可选的学习记忆关联**。

**本地独立运行，自带 API Key，不依赖项目方服务器。** 网页和处理服务运行在你自己的电脑上，不需要项目方账号、远程服务器或 SSH 隧道。语音识别和 AI 推理由本机直接调用你配置的云服务；这不等于完全离线运行。

录音由用户手动开始和结束；上传断线时先保存本地缓存，再按服务器确认的字节位置补传。它是独立应用，通过 API 连接 [TMCRA 记忆核心](https://github.com/reshuibuduo/tmcra)，不是 TMCRA 本体，也不包含模型权重。

> **v0.1.0 / early release**：适合个人、单用户工作区。默认不连接、不写入长期记忆；核心流程不需要 GPU。云端 ASR 与推理 API 的费用由使用者承担。

## 能做什么

- **实时转写**：豆包语音流式 ASR，匿名声音分组；保留原始 PCM 和带出处的定稿字幕。
- **字幕辅助**：整句翻译、整句加生词、仅生词提示；按英语词汇背景调节解释。
- **随堂解释**：主动解释新概念，按学习背景调整深度、例子和前置知识；不连接记忆也能独立使用。
- **划线提问**：选中原文或译文，流式显示回答，不等待整段完成。
- **滚动窗口**：翻译与辅助学习合并短窗口、按需触发；不是每句话都发独立请求，也不是一条永不结束的模型调用。
- **音频恢复**：IndexedDB 本地提交、字节偏移上传、落盘确认、重复片段去重、断线重连。
- **学习记忆与个性化建议**：显式开启后，结合已导入资料中的学习进度、具体提问与历史讨论，给出带出处的知识关联、复习建议和后续追问。
- **macOS 悬浮窗**：主窗口最小化后保留手动录音控制和提示展示；另附离线交互演示。

## 界面预览

以下截图来自实际 Web 界面和 macOS 悬浮窗，使用独立演示数据；不包含真实会议、个人学习记录或 API 密钥。截图用于展示交互，不代表实时模型输出、延迟或识别准确率测试。

点击任意截图查看原始大图。

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>实时字幕</h3>
      <a href="https://raw.githubusercontent.com/reshuibuduo/tmcra-learning-assistant/main/docs/images/realtime-subtitles.jpg"><img src="docs/images/realtime-subtitles.jpg" alt="实时字幕：英文原文与中文字幕对照" width="100%"></a>
      <p>原文、译文、说话人编号与时间位置对照；图中为专注模式下的演示转写回看。</p>
    </td>
    <td width="50%" valign="top">
      <h3>主动解释新概念</h3>
      <a href="https://raw.githubusercontent.com/reshuibuduo/tmcra-learning-assistant/main/docs/images/proactive-concepts.jpg"><img src="docs/images/proactive-concepts.jpg" alt="主动解释新概念：Query、Key 与 Value 的直觉和例子" width="100%"></a>
      <p>给出直觉、具体例子及课堂原句；没有历史记忆，也能帮助跟上讲解。</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>划线提问</h3>
      <a href="https://raw.githubusercontent.com/reshuibuduo/tmcra-learning-assistant/main/docs/images/selection-qa.jpg"><img src="docs/images/selection-qa.jpg" alt="划线提问：选中字幕、问题、回答和原句出处" width="100%"></a>
      <p>选中原文或译文后提问，保留引用出处；支持流式显示，图中为已完成的演示回答。</p>
    </td>
    <td width="50%" valign="top">
      <h3>macOS 悬浮窗</h3>
      <a href="https://raw.githubusercontent.com/reshuibuduo/tmcra-learning-assistant/main/docs/images/floating-window.jpg"><img src="docs/images/floating-window.jpg" alt="macOS 悬浮窗：采集控制、提示开关和概念解释" width="100%"></a>
      <p>主窗口收起后仍可控制采集与提示；图中为内置离线演示，不使用麦克风或模型。</p>
    </td>
  </tr>
</table>

## 主动解释与个性化学习建议

### 先帮助你听懂：按学习背景解释新概念

助手结合当前讲解和你明确填写的学习档案，选择解释深度：通俗模式侧重直觉与小例子，标准模式补充定义与应用，深入模式可以使用公式和机制。档案还可标注熟悉的概念、暂不需要解释的概念，减少重复打扰。背景未知时先用通俗表达，不据此判断你的实际水平。

学习背景可以手动填写，也可以在入口一键复制提示词，让保留了你学习记录的其他 AI 整理，再粘贴结果、预览并确认保存。这条概念解释分支不依赖记忆检索；讲者已解释清楚或证据不足时，会保持安静。

### 再联系过去怎么学：用学习记忆给出个性化建议

接入 TMCRA 并确认导入学习资料后，助手不只关注“你是否见过这个词”，还可以结合记录里的**学习进度、当时讨论的问题、解释方式和具体卡点**，判断哪些过去的内容能帮助理解当前课程：

- **连接已学知识**：把当前的新概念与以前学过或讨论过的相关知识联系起来，而不是从头重复整段讲解。
- **回顾相关讨论**：遇到类似问题时，提醒你回看当时的提问、例子或尚未讲清的地方，并附上学习原文出处。
- **给出下一步建议**：根据当前讲解与已有记录，提示值得先复习的前置知识，或给出一个有针对性的后续问题。

例如（功能示意，非用户真实记录）：如果你曾与 Codex 讨论“点积为什么能衡量相似度”，现在课程讲到自注意力，助手可以联系那段讨论解释 Query 与 Key 的打分，并建议继续追问“为什么还要按向量维度缩放分数”。这种提示需要召回到相关学习证据，不会凭空假定你以前学过。

这里的“学习进度”来自你确认的**曾讨论、学习过、复习过**等状态，以及资料中保留的具体讨论，不是自动评分或完整的知识掌握度模型。导入过、讨论过、学过，都不等于已经掌握；缺少相关记录时，记忆提示保持安静，独立的概念解释仍可工作。

支持导入 Codex、ChatGPT、Claude 等学习对话和文本资料；Work 对话可使用导出的 JSON 或文本。资料先预览、再确认写入，不扫描或自动上传其他聊天。默认仍为 `memory_recall_enabled=false`、`tmcra_write=false`，可单独开启召回而保持课堂转写自动写入关闭，见 [TMCRA 记忆接入](docs/memory.md)。

## 五分钟启动

下面所有步骤都在**你自己的电脑**上执行；无需 SSH 登录任何服务器。完整的本地安装、数据去向和常见问题见 [本地独立使用指南](docs/local.md)。

准备 **Python 3.12**。前端不需要 npm 构建；Node 22 只用于测试。当前音频适配器依赖 `audioop`，暂不支持 Python 3.13+。

```bash
git clone https://github.com/reshuibuduo/tmcra-learning-assistant.git
cd tmcra-learning-assistant
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r workbench/requirements.lock.txt
cp .env.example .env
```

在 `.env` 中填写：

```dotenv
VOLC_API_KEY=your_speech_service_key
DEEPSEEK_API_KEY=your_deepseek_key
```

`VOLC_API_KEY` 是**豆包语音服务**凭证，不是火山方舟通用 Key；需开通对应流式识别资源。默认资源为 `volc.seedasr.sauc.duration`，如账号使用其他资源，请修改生成的 `runtime/config.json`。

```bash
python scripts/workspace.py init --env-file .env
python scripts/workspace.py serve
```

在另一个已激活虚拟环境的终端运行：

```bash
python scripts/workspace.py login
```

打开输出的 90 秒一次性登录链接，进入 `http://127.0.0.1:18782/`。**页面加载不启动麦克风**：点击“开始录音”并授权才采集，点击“结束采集”才收尾。

`init` 将密钥写到本地 `runtime/secrets/`，权限为 `0600`；不会打印 provider key。重复初始化不会清空录音、重置访问令牌或覆盖已有配置。不要提交 `.env`、`runtime/` 或真实录音。

### OpenAI 作为可选推理后端

首次初始化时提供 `VOLC_API_KEY`、`OPENAI_API_KEY`，并使用：

```bash
python scripts/workspace.py init --env-file .env --provider openai
```

模型名称在 `runtime/config.json` 中配置。初始化已有工作区会沿用原 provider，不自动切换；更换时需先更新该配置并补齐对应凭证。ASR 仍由豆包语音负责。

## 断线时发生什么

```text
用户点击开始 → 麦克风 PCM → IndexedDB 提交 → 带偏移上传
                                  ↑             ↓
                              未确认片段保留 ← 服务端 fsync 后 ACK

服务器 PCM → 可恢复 ASR → 定稿字幕 → 滚动窗口 → 翻译 / 解释 / 可选召回
用户点击结束 → 先停止麦克风 → 补传缓存 → 完成转写收尾
```

- 断网不会主动停止仍在工作的麦克风；界面显示待上传音频。
- 页面刷新后不会自动开麦。可点击“补传并保存缓存音频”只上传缓存，或手动“继续这场录音”。
- ASR 连接失败与录音存储分离；识别按检查点恢复，原始音频不因识别失败而删除。
- 录音时需保留标签页。浏览器被关闭、系统休眠或音频设备断开后，本项目不承诺继续采集；从未采集到的声音不属于可恢复数据。
- 本地待上传缓存上限 512 MiB；存储失败会提示暂停，而不是假报继续上传。长时间离线请关注提示。
- 声音编号是**单次识别连接内的匿名聚类**，不是身份识别；换连接后编号可能变化。

更多：[架构与数据流](docs/architecture.md) · [部署与恢复](docs/deployment.md) · [TMCRA 接入](docs/memory.md) · [验收边界](docs/verification.md)

## 本地版的数据流

```text
你的浏览器 / Mac 悬浮窗
         │ 本机连接
         ▼
你的电脑上的处理服务 ──音频──→ 你配置的豆包语音服务
         │            ──文本──→ 你配置的 DeepSeek / OpenAI
         ├── runtime/：录音、字幕、问答及恢复检查点
         └── 可选：你自己运行的 TMCRA 记忆核心
```

- 录音、字幕等工作区文件保存在本机；待上传音频还会暂存在浏览器中。识别所需的音频与推理所需的文本会发送给对应模型服务，因此不应表述为“所有数据都不离开电脑”。
- 基础功能不需要部署 TMCRA，也不需要 GPU；历史学习记忆召回需另行接入自己运行的 TMCRA。
- 我们的服务器是否在线，不影响本地版的启动和运行；模型提供商的网络、有效凭证与额度仍是实时 AI 功能的前提。

## macOS 悬浮窗（可选）

需要 Xcode Command Line Tools：

```bash
bash desktop/build.sh
open "dist/TMCRA 学习悬浮窗.app"
```

在应用中连接本机工作台；使用与浏览器相同的一次性登录机制。构建默认使用当前 SDK 和本机架构，也可设置 `TMCRA_SDKROOT`、`TMCRA_ARCH`。产物为本地 ad-hoc 签名，不是已公证的安装包。

## 测试

```bash
python scripts/test_backend.py
node --test tests/*.test.mjs desktop/Tests/*.test.mjs
python scripts/check_release.py
python scripts/smoke_test.py
```

以上测试不需要真实 API key，不使用麦克风、不调用付费模型。真实 ASR、麦克风和长会话需另做验收，不能由单元测试代替。GitHub Actions 运行 Python / Node 回归与 macOS 构建。

## 项目结构

```text
workbench/meeting/       FastAPI、ASR、推理、记忆连接和静态前端
workbench/ops/           通用 DeepSeek 配置，不含私人服务器脚本
desktop/                macOS 悬浮窗源码及测试
scripts/                初始化、启动、登录、离线测试、发布扫描
docs/                   架构、部署、记忆接入和验收说明
runtime/                本地生成的私有数据，Git 忽略
```

## License

[Apache-2.0](LICENSE)。保留 TMCRA 相关素材的原许可证，详见 [NOTICE](NOTICE)。此仓库不分发 TMCRA 模型权重、第三方 API 凭证或真实会议数据。
