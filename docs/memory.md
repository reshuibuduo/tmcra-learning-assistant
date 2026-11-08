# 可选 TMCRA 记忆连接

默认 `memory_recall_enabled=false`、`tmcra_write=false`，因此没有 GPU 或没有安装记忆核心也能使用转写、翻译、概念解释和划线提问。

TMCRA 核心另行安装：[reshuibuduo/tmcra](https://github.com/reshuibuduo/tmcra)。模型权重和向量库由该项目管理，本仓库不复制模型或私有历史数据。

## 显式开启

1. 在同一服务器或本机通过 loopback 提供 TMCRA 原生 API。
2. 将 `TMCRA_API_KEY` 写入自己的 `.env`，重新执行 `workspace.py init --env-file .env` 更新密钥。
3. 编辑 `runtime/config.json`：

```json
{
  "memory_recall_enabled": true,
  "tmcra_write": false,
  "learner_memory_scopes": ["YOUR_EXISTING_SCOPE"]
}
```

这里是要修改的字段，不是替换整个配置文件。`tmcra_write=false` 保持转写自动写入关闭；若明确希望将之后的转写写入独立会议 scope，再改成 `true`。学习资料导入仍经过预览和确认。

4. 设置本地 TMCRA 端点与 TLS CA 后启动学习服务，例如：

```bash
export TMCRA_BASE_URL=https://127.0.0.1:22109
export TMCRA_CA_FILE=/path/to/tmcra-ca.crt
python scripts/workspace.py serve
```

服务校验 TMCRA 地址为 loopback，不关闭 TLS 证书验证。若测试服务只支持 HTTP，应显式使用 localhost 的 HTTP 地址，而不是禁用证书检查。

连接器使用 scope ingest、job 状态、recall 等原生接口；上游 API 发生变化时须重新做兼容性验收。导入过、讨论过、学过和真正掌握不是同一个状态，界面不会仅凭导入自动判定用户已掌握知识。
