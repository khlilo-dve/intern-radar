# intern-radar · 实习招聘情报捕捉助手

在飞书里丢一条消息（URL 或招聘截图），后台自动用 Claude 解析公司/岗位/匹配度/降维打击策略/短板，写入多维表格沉淀。

## 架构

```
飞书 App → lark-cli event +subscribe (WebSocket 长连接)
         → main.py 按行读 NDJSON
         → text: 抽 URL + trafilatura 抓正文
           image: lark-cli 下载 + vision 识别
         → OpenAI 兼容代理调用 Claude
         → lark-cli base +record-upsert 入库
         → lark-cli im +messages-send 回执
```

**不用 webhook / 不起 HTTP server / 不用 ngrok** —— lark-cli 的 WebSocket 长连接把"内网穿透"这一步省了。

## 一次性前置（飞书开发者后台）

在 https://open.feishu.cn 打开你的 App（`cli_a9418c6214f91bb6`）：

1. **事件订阅 → 订阅模式 → 选"长连接"**
2. **事件列表 → 添加 `im.message.receive_v1`**
3. **权限管理 → 确认以下 scope 已开**：
   - `im:message:receive_as_bot` — 接消息
   - `im:resource` — 下载图片
   - `im:message`（或 `im:message:send_as_bot`）— 发回执
   - `bitable:app` — 建表/写记录
4. **发布版本** —— scope 变更必须发布后才生效
5. 把机器人加到测试群（或直接 1v1）

## 本地部署

```bash
cd ~/projects/intern-radar

# 1. venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置密钥
cp .env.example .env
$EDITOR .env              # 填 LLM_BASE_URL / LLM_API_KEY / 模型名

# 3. config 会首次启动自动生成；如果你想改表名，先复制 example
cp config.yaml.example config.yaml

# 4. 候选人基准可以按需调整
$EDITOR candidate_baseline.md

# 5. 跑
python main.py
```

首次启动会：
- 自动创建 Bitable app + 表 + 9 个字段
- 把 app_token / table_id 写回 `config.yaml`
- 运行一次 vision 自检；代理不支持多模态则自动降级

## 字段 schema

| 字段 | 类型 |
|------|------|
| Company | 文本 |
| Business_Line | 文本 |
| Hard_Tags | 多选（最多 5 个） |
| Match_Score | 数字 1-100 |
| Attack_Strategy | 文本（≤ 50 字） |
| Critical_Gap | 文本（≤ 50 字） |
| Source_URL | URL |
| Created_At | 日期时间 |
| Raw_Input | 文本（原消息/图片名） |

## 使用

发给机器人：
- `https://lagou.com/wn/jobs/12345678` → 解析网页
- 任意招聘页截图 → vision 识别
- 纯聊天 → 回"没 URL，跳过"
- LLM 判定与招聘无关 → 回"跳过"并附原因

## 常见问题

| 症状 | 排查 |
|------|------|
| 启动时 `auth` 相关报错 | `lark-cli auth status` 看 token，过期就 `lark-cli auth login --domain all` |
| 一直收不到事件 | 检查前置 1-4 步，尤其是"发布版本"；`lark-cli event +subscribe --dry-run` 看命令 |
| `413 / permission denied` 下图 | 补 `im:resource` scope，重新发布版本 |
| LLM 输出不 JSON | 查 `LLM_MODEL_TEXT` 是不是太小的模型，或代理裁剪了 prompt |
| 图片模式不工作 | 日志看 `vision self-check failed` —— 代理不支持多模态，只能用 URL |

## 目录结构

```
.
├── main.py                  # 入口 + 事件循环 + 崩溃重连
├── handlers.py              # 按 message_type 分发
├── llm.py                   # OpenAI 兼容代理调用 + pydantic 校验
├── larkcli.py               # lark-cli 子进程封装
├── web.py                   # URL → 正文
├── models.py                # IntelRecord + Bitable 字段 spec
├── candidate_baseline.md    # 候选人基准（prompt 靶子）
├── config.yaml              # bitable token / 表名等（首次自动生成）
├── .env                     # LLM 密钥（不进 git）
├── downloads/               # 临时图片落地
└── requirements.txt
```

## 红线

- `.env` 永远不进 git（已在 `.gitignore`）
- `config.yaml` 的 `app_token` 本质是凭据，也不进 git
- 候选人基准如果写真实姓名/联系方式，自己评估隐私风险
