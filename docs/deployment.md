# 部署、升级与恢复

## 默认：在自己的电脑上运行

默认部署不依赖项目方服务器。按 [本地独立使用指南](local.md) 安装后，`python scripts/workspace.py serve` 在你的电脑上运行，只监听 `127.0.0.1:18782`。浏览器直接连接本机，不需要 SSH 转发；识别与推理由本机使用你自己的 API Key 直连模型提供商。

浏览器麦克风使用 localhost 安全上下文。打开界面不会自动录音，开始和结束仍由用户手动控制。

## 可选：部署到自己的远端服务器

只有主动选择远端部署时才需要下面的 SSH 转发，普通本地使用跳过本节。服务器安装步骤与 README 相同；优先转发本机端口，而不是直接暴露 HTTP 端口：

```bash
ssh -N -L 18782:127.0.0.1:18782 USER@SERVER
```

服务器执行 `python scripts/workspace.py login`，把输出链接在本机浏览器打开。占位 `USER@SERVER` 使用自己的 SSH 配置；仓库不含 SSH 密码或私钥。

## 服务自启动

以下是 Linux systemd 模板。将 `/opt/tmcra-learning-assistant` 改成安装位置，并以拥有该目录的普通系统用户运行：

```ini
[Unit]
Description=TMCRA Learning Assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tmcra
WorkingDirectory=/opt/tmcra-learning-assistant
ExecStart=/opt/tmcra-learning-assistant/.venv/bin/python scripts/workspace.py serve --root /opt/tmcra-learning-assistant/runtime
Restart=on-failure
RestartSec=3
UMask=0077
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

**服务自启动不等于录音自启动。** 用户仍需手动开始/继续和结束采集。systemd 模板是部署参考，不在本地测试中执行系统重启。

## 更新和回退

1. 用户手动结束录音，等待待上传量归零；不要在采集中强制刷新或重启服务。
2. 备份 `runtime/`，限制备份访问权限。录音、字幕、问答和检查点应一起保存。
3. 记录当前 commit，停止服务，拉取目标版本，更新依赖并跑测试，再启动服务。
4. 回退代码时 checkout 已记录 commit；**不要用旧备份覆盖仍有新录音的 runtime**。v0.1.0 不执行破坏性数据库迁移。

## 故障排查

|现象|先检查|
|---|---|
|界面未连接|先检查本机服务、端口与 `/healthz`，再重新生成一次性登录链接；仅远端部署检查 SSH 转发|
|显示待上传|网络与磁盘；不要清理浏览器网站数据或删除 runtime|
|音频在增长但没有字幕|`asr-state-*.json`、语音服务凭证/资源、配额|
|45000081|上游 8 秒内没有收到音频包，不是会议总时长限制|
|采集已中断|查看原记录，再手动继续；不会自动开启麦克风|
|翻译或解释失败|对应推理服务额度、模型名称及错误元数据|

豆包错误码参考：[官方错误码文档](https://docs.volcengine.com/docs/doubao-voice/error-code-query?lang=zh)。
DeepSeek 模型配置参考：[官方 API 文档](https://api-docs.deepseek.com/)。

浏览器缓存不是永久备份：用户清理网站数据、磁盘不足和浏览器存储策略可能影响它。长期使用应另外备份运行工作区中的音频和字幕：默认是本机 `runtime/`，远端部署时才位于服务器。
