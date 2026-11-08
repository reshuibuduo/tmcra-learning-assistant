# 本地独立使用：不依赖项目方服务器

本项目默认可以在你自己的电脑上运行。你不需要我们的服务器、SSH 隧道、项目方账号或项目方提供的 API 中转；我们关闭服务器后，你的本地安装仍可使用。

**这里的“本地”指应用和工作区在本机，不代表模型完全离线。** 豆包语音负责识别，DeepSeek 或 OpenAI 负责翻译、解释和问答；本机直接使用你自己的 API Key 调用它们。网络、服务开通情况和额度由对应提供商决定。

## 1. 安装到自己的电脑

准备 Python 3.12 和 Git；基础流程不需要 GPU。安装位置使用你自己的目录，不要放进正在录音的旧工作区。

```bash
git clone https://github.com/reshuibuduo/tmcra-learning-assistant.git
cd tmcra-learning-assistant
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r workbench/requirements.lock.txt
cp .env.example .env
```

也可以从 GitHub 下载源码 ZIP，解压后在项目目录执行创建虚拟环境及后续命令。本指南覆盖 macOS / Linux；当前启动器使用 POSIX 文件权限接口，Windows 独立安装还需完成相应适配和验证。macOS 悬浮窗仅支持 Mac。

## 2. 填入自己的 Key

用本地文本编辑器打开 `.env`，填写：

```dotenv
VOLC_API_KEY=your_speech_service_key
DEEPSEEK_API_KEY=your_deepseek_key
```

- `VOLC_API_KEY` 来自豆包语音服务，不是方舟通用模型 Key；需开通对应的流式语音识别资源。
- `DEEPSEEK_API_KEY` 用于翻译、概念解释和划线问答。模型名称可在初始化后的 `runtime/config.json` 中调整。
- 不需要填写我们的任何服务器地址、账户或密码；基础功能也不需要 `TMCRA_API_KEY`。
- `.env` 只保存在自己的电脑，不发群、不提交 Git；可以复制 `.env.example`，不要复制别人的真实配置或密钥。

```bash
python scripts/workspace.py init --env-file .env
python scripts/workspace.py serve
```

保持这个终端运行，它就是本地处理服务。程序只监听本机，不向局域网开放。`init` 会创建本机配置和独立访问令牌，不会开始录音。重复执行初始化不会清空已有录音或替换已有访问令牌；已有配置仍会沿用。

首次初始化若希望用 OpenAI，把 `.env` 中的推理 Key 改为 `OPENAI_API_KEY`，执行 `python scripts/workspace.py init --env-file .env --provider openai`。已经初始化的工作区需要同时调整自己的配置和对应凭证，`--provider` 不会覆盖旧设置。

## 3. 打开界面，手动录音

另开一个终端，进入同一项目目录并激活虚拟环境：

```bash
source .venv/bin/activate
python scripts/workspace.py login
```

在浏览器中打开输出的本机链接。它是 90 秒有效的一次性登录链接，不是向项目方注册账号，也不是付费服务登录。链接失效时重新执行 `login`。

1. 打开页面不会自动开启麦克风。
2. 点击“开始录音”，浏览器授权后才采集声音。
3. 根据需要开启字幕翻译、主动解释或划线提问。
4. 手动“结束采集”，等待待上传音频归零及转写收尾，再退出服务。

如果默认端口已被占用，不要结束正在使用的旧进程。给 `serve` 和 `login` 都加上同一个空闲端口，例如 `--port 18790`；独立安装应使用独立目录和 `runtime/`。

macOS 悬浮窗可按 README 的构建步骤启用，连接同一台电脑上的本地服务。它不是使用本地版的前置条件。

## 数据保存在哪里、发送到哪里

|内容|默认位置或接收方|
|---|---|
|网页、样式、脚本、图标|由本机服务提供，不依赖项目方网站或 CDN|
|录音、字幕、问答、恢复检查点|自己的 `runtime/` 工作区|
|待上传音频、界面学习档案|本机浏览器存储；清理网站数据可能删除它们|
|识别所需音频|本机直接发送到自己配置的豆包语音服务|
|翻译、解释、问答所需文本和学习背景|本机直接发送到所选 DeepSeek / OpenAI 服务|
|启用记忆后的相关学习片段|进入自己配置的 TMCRA；召回后的相关片段可能随推理请求发送给所选模型服务|

因此，本地版避免了依赖项目方服务器，但不是“所有数据都不离开电脑”。密钥保存在本机配置文件中；模型服务的费用按你自己的账户计费。

## 不安装记忆核心，也可以使用

默认 `memory_recall_enabled=false`、`tmcra_write=false`。实时字幕、翻译、按填写的学习背景主动解释、划线问答都不要求启动 TMCRA。

若要联系以前的学习进度与相关讨论，需要另外运行你自己的 [TMCRA 记忆核心](https://github.com/reshuibuduo/tmcra)，再按 [记忆接入指南](memory.md) 配置并确认导入。记忆核心的模型、硬件要求和存储独立于本项目；不要把基础功能不需要 GPU 理解为大模型记忆核心也没有硬件要求。

## 验证本机安装

```bash
python scripts/smoke_test.py
```

测试使用独立临时工作区、合成文本和测试 Key；检查本地启动、资源加载、登录、手动结束与字幕落盘，并阻止服务进程访问非本机地址。测试不使用麦克风、不调用付费模型，不会连接或停止你已经在运行的服务。通过它证明本地应用链路可独立启动，不代表你的真实语音 Key、模型额度或实际识别质量已经通过测试。

真正的识别效果需要你在页面手动开始录音后验证。若有音频但没有字幕，先检查语音服务开通情况、凭证与资源；若字幕正常但解释失败，检查所选推理服务的模型名称与额度。备份时同时保留录音、字幕、问答和检查点，不要只保留浏览器缓存。
