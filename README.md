# vidrelay

**一条视频，多平台分发。** 一个契约驱动的命令行工具，把同一支视频发布到抖音、快手、B站、TikTok、YouTube、Dailymotion。

> 状态：**M0 地基已完成，适配器尚未实现。** 目前 `publish` 会明确报告「适配器尚未实现」，而不是静默跳过。平台适配器按里程碑逐个补齐。

---

## 它解决什么问题

一条视频要在 6 个平台各走一遍发布流程：上传、填标题、写简介、加标签、选封面、选分区、点发布、等结果。重复的是**填表**，不是创作。

vidrelay 把这件事压成两步：

1. 把 6 个平台的标题 / 文案 / 标签 / 时段写进一份 `meta.yaml`
2. 跑一条命令

```bash
vidrelay publish meta.yaml --group oversea
```

---

## 设计取向

### 1. 平台适配器是防腐层

```
编排入口   vidrelay publish / validate / doctor / platforms / netcheck
   ↓
适配器层   douyin · kuaishou · bilibili · tiktok · youtube · dailymotion
   ↓
执行引擎   social-auto-upload · biliup · 各平台官方 API
```

第三方引擎会停更、会被替换。适配器把这种变化隔离住：只要统一契约不变，底层换引擎不影响上层。

### 2. 统一返回契约

所有适配器——不管底层是官方 API 还是浏览器自动化——都返回同一个结构：

```json
{
  "platform": "youtube",
  "ok": true,
  "video_id": "dQw4w9WgXcQ",
  "url": "https://youtu.be/dQw4w9WgXcQ",
  "scheduled_at": "2026-09-22T01:30:00+00:00",
  "duration_ms": 42130,
  "error": null,
  "dry_run": false
}
```

编排层因此不需要为任何平台写特例。

### 3. 网络分段是硬约束，不是注意事项

国内平台与海外平台对出口 IP 的要求**互斥**：国内平台必须国内直连，海外平台需要海外出口 IP。靠人记着「跑之前先关 VPN」迟早会出事，所以做成了断言：

```
$ vidrelay publish meta.yaml
无法执行：
国内组与海外组不能在同一个命令里一起执行——两者的出口 IP 要求是互斥的。

$ vidrelay publish meta.yaml --group cn     # 此时挂着美国节点
无法执行：
网络校验未通过：国内组要求国内直连，当前出口 IP 归属地是 US。
请断开 VPN / 代理后重试。
```

探测不到归属地时**拒绝执行**（fail closed），而不是放行。

---

## 安装

```bash
git clone <repo-url>
cd vidrelay
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
vidrelay doctor
```

`vidrelay doctor` 会逐项告诉你：运行时、依赖、引擎、凭证哪些就绪、哪些还缺、缺的怎么补。

---

## 快速开始

```bash
# 1. 环境自检
vidrelay doctor

# 2. 看看有哪些平台、适配器实现了没有
vidrelay platforms

# 3. 写一份 meta（照抄 meta.example.yaml 改）
cp meta.example.yaml config/my-video.yaml

# 4. 校验（报错会带行号和字段名）
vidrelay validate config/my-video.yaml

# 5. 先预演，不真发
vidrelay publish config/my-video.yaml --group oversea --dry-run

# 6. 确认无误后真发
vidrelay publish config/my-video.yaml --group oversea
```

---

## 命令

| 命令 | 作用 |
|---|---|
| `vidrelay publish <meta>` | 按 meta 发布。`--group cn\|oversea`、`--platform`、`--dry-run`、`--json` |
| `vidrelay validate <meta>` | 校验 meta，报错带**行号 + 字段路径** |
| `vidrelay doctor` | 环境自检：依赖、引擎、凭证 |
| `vidrelay platforms` | 列出平台、分组、执行引擎、适配器实现状态 |
| `vidrelay netcheck` | 探测出口 IP 归属，判断当前能跑哪一组 |

所有命令都支持 `--json`，输出结构稳定，便于脚本与 Agent 消费。

---

## 平台与分组

| 平台 | 分组 | 接入方式 | 计划引擎 | 适配器 |
|---|---|---|---|---|
| 抖音 | 国内 | 浏览器自动化 + Cookie | social-auto-upload | 待实现 |
| 快手 | 国内 | 浏览器自动化 + Cookie | social-auto-upload | 待实现 |
| B站 | 国内 | 命令行投稿 | [biliup](https://github.com/biliup/biliup) | 待实现 |
| TikTok | 海外 | 官方 Content Posting API | 官方 API + 浏览器兜底 | 待实现 |
| YouTube | 海外 | 官方 API | YouTube Data API v3 | 待实现 |
| Dailymotion | 海外 | 官方 API | Dailymotion Graph API | 待实现 |

**国内组必须国内直连、完全断开 VPN。** 海外组需要海外出口 IP。

---

## 凭证

两类，性质不同，分开存，都在 `secrets/` 下（已被 `.gitignore` 忽略）：

| 类型 | 平台 | 路径 | 有效期 |
|---|---|---|---|
| OAuth token | YouTube / Dailymotion / TikTok | `secrets/<platform>.json` | refresh_token 长期有效 |
| Cookie | 抖音 / 快手 / B站 / TikTok | `secrets/cookies/<platform>_<account>.json` | **会过期**，需重新扫码 |

OAuth 客户端信息通过环境变量注入，参考 `.env.example`。

### 提交前扫描

```bash
python scripts/scan_secrets.py          # 扫 git 已跟踪文件
python scripts/scan_secrets.py --staged # 只扫暂存区
python scripts/scan_secrets.py --all    # 扫整个工作区
```

`.gitignore` 只能挡住已知路径，挡不住「把 token 写进了本该提交的文件里」——比如调试时临时 print 出来的 `refresh_token`，或者示例里填了真值。这个脚本扫的是**内容**，不是路径。

建议装成钩子，让它自动跑：

```bash
sh scripts/install_hooks.sh
```

装完后每次 `git commit` 都会先扫描，发现高危项直接阻止提交。钩子只在本机生效，所以每个克隆都需要跑一次。确认为误报的行可以加 `# scan:allow` 标记跳过。

---

## 开发

```bash
pip install -e ".[dev]"
pytest
```

新增一个平台 = 新增一个文件 + 一个装饰器：

```python
# src/vidrelay/adapters/youtube.py
from vidrelay.adapters.base import Adapter, register
from vidrelay.contract import PublishResult


@register
class YouTubeAdapter(Adapter):
    platform_key = "youtube"

    def preflight(self) -> list[str]:
        return []          # 返回问题列表，空表示可以继续

    def publish(self) -> PublishResult:
        ...
```

CLI 与编排层不需要改动。详见 [`docs/architecture.md`](docs/architecture.md)。

---

## 路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0 地基** | 仓库骨架、统一契约、凭证管理、meta 校验、环境自检 | ✅ 已完成 |
| **M1 海外链路** | YouTube 适配器 → 定时发布 → Dailymotion 适配器 → 海外组编排 | 进行中 |
| **M2 TikTok 双轨** | 设备环境、浏览器轨保底、官方 API 送审、官方轨适配器 | 待开始 |
| **M3 国内链路** | 抖音 / 快手 / B站 适配器、国内组编排 | 待开始 |
| **M4 编排交付** | 内容侧对接、发布窗口配置、失败重试、结果归档 | 待开始 |

---

## 合规提醒

- **国内平台绝不用海外 IP。** 人为篡改 IP 属地发布国内平台，可能被判定为异常行为，且在部分地区属违规。
- 国内链路**先用测试账号跑通**，再上主账号。
- 各平台的服务条款与自动化政策请自行确认。本项目提供的是技术管道，不改变平台规则。
- 不要用本工具搬运、洗稿或发布非自有版权内容。

---

## 许可

[MIT](LICENSE)
