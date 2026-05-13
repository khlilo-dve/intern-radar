# intern-radar · 实习招聘情报捕捉助手

自动从招聘平台爬取岗位，用 AI 多维度评分，高分岗位入库飞书多维表格并推送通知。也支持手动发送 URL / 截图到飞书机器人解析。

## 架构

```
┌─ 飞书 App（lark-cli WebSocket 长连接）────────────┐
│  用户发 URL / 截图 → 解析 → 评分 → 入库 → 卡片回执  │
└──────────────────────────────────────────────────────┘

┌─ 爬虫调度（APScheduler）──────────────────────────┐
│  定时抓取牛客实习广场 → trafilatura 抓 JD            │
│  → LLM 5 维评分 → 70 分以上入库 + 飞书推送          │
└──────────────────────────────────────────────────────┘
```

## 功能

- **手动模式**：飞书发 URL 或截图，自动解析评分入库
- **自动模式**：定时爬取牛客实习广场，高分岗位自动推送飞书
- **截图补链**：截图模式自动提取职位名+城市，生成 Boss 直聘搜索链接
- **多维度评分**：技术视野 / 产品主导权 / 杂活比例 / AI 杠杆 / 成长天花板
- **70 分门槛**：低于 70 分不入库不推送
- **城市限制**：默认仅限北京

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

# 3. config 会首次启动自动生成；如果你想改配置，先复制 example
cp config.yaml.example config.yaml
$EDITOR config.yaml       # 改 crawler.enabled / 关键词 / 城市

# 4. 候选人基准可以按需调整
$EDITOR candidate_baseline.md

# 5. 跑
python main.py
```

首次启动会：
- 自动创建 Bitable app + 表 + 全部字段
- 把 app_token / table_id 写回 `config.yaml`
- 运行一次 vision 自检；代理不支持多模态则自动降级
- 后续启动自动检测并补齐缺失字段

## 爬虫配置

在 `config.yaml` 中配置：

```yaml
crawler:
  enabled: true              # 改为 true 启用自动爬取
  schedule_hour: 9           # 每天几点扫描（24h）
  schedule_minute: 0
  score_threshold: 70        # 低于此分不入库
  sources:
    - name: nowcoder         # 牛客实习广场（直爬，无需额外服务）
      enabled: true
      platform: nowcoder
      feeds:
        - keyword: "AI产品"  # 关键词过滤
          city: "北京"       # 城市限制
```

### 通知配置

在 `.env` 中添加飞书群 ID，高分岗位会自动推送到该群：

```
NOTIFY_CHAT_ID=oc_xxxxxxxx
```

## 多维度评分体系

每个岗位入库时按 5 个维度打分，加权计算综合匹配度：

| 维度 | 含义 | 评分标尺 |
|------|------|---------|
| Tech_Vision | 技术视野要求 | 80+ 系统架构级 / 60-79 方案选型 / 40-59 了解概念 / <40 纯业务 |
| Product_Dominance | 产品业务主导权 | 80+ 独立负责产品线 / 60-79 有话语权 / 40-59 执行层 / <40 纯执行 |
| Exec_Ratio | 日常执行杂活比例 | <30 策略/架构为主 / 30-49 创作执行各半 / 50-69 执行为主 / 70+ 纯重复 |
| AI_Leverage | AI/自动化杠杆空间 | 80+ 核心流程可加速 / 60-79 部分自动化 / 40-59 少量辅助 / <40 纯人工 |
| Growth_Ceiling | 成长天花板 | 80+ 双晋升路径 / 60-79 有空间 / 40-59 天花板低 / <40 死胡同 |

**加权公式**：`Tech×0.25 + Product×0.25 + (100-Exec)×0.20 + AI×0.15 + Growth×0.15`

**Red_Flags 机制**：检测到纯线性劳动、极度内卷、无产品话语权等警报信号时，分数上限 60。

## 字段 schema

| 字段 | 类型 | 说明 |
|------|------|------|
| Company | 文本 | 公司名称 |
| Job_Title | 文本 | 职位名称 |
| City | 文本 | 工作城市 |
| Business_Line | 文本 | 业务线/产品线 |
| Hard_Tags | 文本 | 核心硬性技能（最多 5 个） |
| Red_Flags | 文本 | 警报词汇（最多 3 个） |
| Tech_Vision | 数字 | 技术视野要求 0-100 |
| Product_Dominance | 数字 | 产品业务主导权 0-100 |
| Exec_Ratio | 数字 | 日常执行杂活比例 0-100 |
| AI_Leverage | 数字 | AI/自动化杠杆空间 0-100 |
| Growth_Ceiling | 数字 | 成长天花板 0-100 |
| Match_Score | 数字 | 综合匹配度 1-100 |
| Attack_Strategy | 文本 | 差异化包装策略 |
| Critical_Gap | 文本 | 核心短板分析 |
| 投递网址 | 文本 | 岗位投递/搜索链接 |
| Status | 单选 | 未投递 / 简历未通过 / 简历通过 / 一面通过 / 二面通过 / 三面通过 |
| Notes | 文本 | 人工备注 |
| Created_At | 日期时间 | 创建时间 |
| Raw_Input | 文本 | 原始输入（消息/图片名） |

## 使用

### 手动模式

发给机器人：
- `https://lagou.com/wn/jobs/12345678` → 解析网页
- 任意招聘页截图 → vision 识别 + 自动补搜索链接
- 纯聊天 → 回"没 URL，跳过"
- LLM 判定与招聘无关 → 回"跳过"并附原因
- 重复发送同一 URL → 自动去重，回复"已入库"

### 自动模式

启动后自动运行：
- 启动时立即扫描一次
- 之后每天定时扫描（默认早 9 点）
- 70 分以上岗位入库飞书表格 + 推送通知卡片

## 目录结构

```
.
├── main.py                  # 入口 + 事件循环 + 爬虫调度
├── handlers.py              # 消息分发 + 卡片回复 + 去重 + 高分推送
├── llm.py                   # OpenAI 兼容调用 + 多维评分 prompt + 搜索链接构造
├── larkcli.py               # lark-cli 子进程封装
├── web.py                   # URL → 正文
├── models.py                # IntelRecord + Bitable 字段 spec
├── config.py                # pydantic 类型安全配置
├── utils.py                 # @retry 通用重试装饰器
├── crawlers/                # 爬虫适配器
│   ├── base.py              # CrawlerAdapter 抽象基类
│   ├── nowcoder.py          # 牛客实习广场直爬
│   └── rsshub.py            # RSSHub 适配器（备用）
├── candidate_baseline.md    # 候选人基准（prompt 靶子）
├── config.yaml              # 配置（首次自动生成）
├── .env                     # LLM 密钥（不进 git）
├── downloads/               # 临时图片落地
├── tests/                   # 单元测试
└── requirements.txt
```

## 运行测试

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## 常见问题

| 症状 | 排查 |
|------|------|
| 启动时 `auth` 相关报错 | `lark-cli auth status` 看 token，过期就 `lark-cli auth login --domain all` |
| 一直收不到事件 | 检查前置 1-5 步，尤其是"发布版本"；`lark-cli event +subscribe --dry-run` 看命令 |
| `413 / permission denied` 下图 | 补 `im:resource` scope，重新发布版本 |
| LLM 输出不 JSON | 查 `LLM_MODEL_TEXT` 是不是太小的模型，或代理裁剪了 prompt |
| 图片模式不工作 | 日志看 `vision self-check failed` —— 代理不支持多模态，只能用 URL |
| 爬虫抓不到数据 | 检查网络是否能访问 nowcoder.com，爬虫带 3 次重试 |
| 爬虫入库失败 | 检查 lark-cli 授权：`lark-cli auth login --domain all` |

## 红线

- `.env` 永远不进 git（已在 `.gitignore`）
- `config.yaml` 的 `app_token` 本质是凭据，也不进 git
- 候选人基准如果写真实姓名/联系方式，自己评估隐私风险
