# 架构

## 分层

```
┌──────────────────────────────────────────────────────────┐
│  第 1 层  编排入口                                        │
│  cli.py → runner.py                                       │
│  publish · validate · doctor · platforms · netcheck       │
└───────────────────────────┬──────────────────────────────┘
                            │  PublishRequest（已解析的单平台请求）
┌───────────────────────────▼──────────────────────────────┐
│  第 2 层  平台适配器（防腐层）                             │
│  adapters/base.py  +  adapters/<platform>.py              │
│  每个平台一个文件，通过 @register 注册                     │
└───────────────────────────┬──────────────────────────────┘
                            │  各平台的真实上传调用
┌───────────────────────────▼──────────────────────────────┐
│  第 3 层  执行引擎（第三方 / 官方 API）                    │
│  social-auto-upload · biliup · YouTube Data API v3 ...    │
└──────────────────────────────────────────────────────────┘
```

### 为什么要有第 2 层

第 3 层的项目**会停更**。平台前端一改版，浏览器自动化脚本就失效；官方 API 会升版本。如果编排层直接调用第三方，每次第三方变动都要改编排、改 CLI、改文档。

适配器层把这个变化挡住：**只要 `PublishResult` 契约不变，换掉整条底层链路，上层零改动。**

同时它让「先跑通一个平台，再逐个补」成为可能——没实现的平台走 `NotImplementedAdapter`，明确报「尚未实现」，而不是静默跳过（静默跳过会让「6 个平台只发了 2 个」这种事悄悄发生）。

---

## 数据流

```
meta.yaml
   │
   ├─ schema.py     校验：字段、类型、枚举、时区；报错带行号 + 字段路径
   │
   ├─ runner.py     按 --group / --platform 筛出目标平台
   │
   ├─ netguard.py   网络校验：国内组要求国内 IP，海外组要求海外 IP
   │                （两组混跑直接拒绝；探测失败 fail closed）
   │
   ├─ 并发执行       ThreadPoolExecutor，每个平台独立
   │                preflight 不过 → 该平台失败，其他照常
   │                适配器抛异常 → 兜住，转成 PublishResult
   │
   └─ PublishReport 汇总 → 控制台 / --json / runs/<时间戳>.json
```

---

## 关键设计决策

### 1. 契约先行

`contract.py` 是整个项目最不能省的文件。它同时服务两个消费者：

- **编排层**：不用为任何平台写特例
- **AI Agent**：每次发布只需读回一份汇总 JSON，token 消耗 1–3K；而不是逐页读浏览器快照的 50–150K

失败与成功用**同一个结构**，靠 `ok` 区分。这样失败信息不会被丢掉，调用方也不需要写两套解析逻辑。

`PublishReport.all_ok` 对空报告返回 `False`——避免「什么都没跑」被误判为成功。

### 2. 网络分段是断言，不是文档

`netguard.py` 的取向是 **fail closed**：探测不到归属地时拒绝执行，而不是放行。

理由：放行一次的代价（账号被风控标记）远大于拒绝一次的代价（重跑一条命令）。

校验可以被 `VIDRELAY_ENFORCE_NETGUARD=0` 关掉，但那是显式的、需要主动做的动作，而不是默认行为。

### 3. 定时发布走平台原生定时

不用本地定时器。脚本跑完即结束，不需要机器在那几个小时里一直开机、一直挂着 VPN。

配置里写**时区名**（`Asia/Shanghai`）而不是偏移量，这样跨夏令时不用改配置。

### 4. 凭证分离与最小暴露

- OAuth token 与 Cookie 分开存，因为失效模式完全不同
- `secrets.py` 的 `credential_status()` **只返回「有 / 没有」和路径，绝不返回值**
- `netguard` 打印 IP 时打码（`203.0.113.x`）
- `redact()` 供日志使用

### 5. 行号定位

PyYAML 解析后只剩纯数据，丢失位置信息。`schema.py` 额外扫描一遍源文本，用缩进栈还原「字段路径 → 行号」的映射。

代价是几十行代码，换来的是「错误信息里直接写着第 20 行」，而不是让人自己去找。

---

## 扩展点

### 新增一个平台适配器

```python
# src/vidrelay/adapters/youtube.py
from vidrelay.adapters.base import Adapter, register
from vidrelay.contract import PublishResult


@register
class YouTubeAdapter(Adapter):
    platform_key = "youtube"     # 必须已在 platforms.py 注册

    def preflight(self) -> list[str]:
        """返回问题列表。非空则不会调用 publish()。"""
        if not self.request.video:
            return ["缺少视频路径"]
        return []

    def publish(self) -> PublishResult:
        """必须返回 PublishResult。异常会被兜住，但最好自己处理。"""
        ...
```

注册后 `vidrelay platforms` 会自动显示为「已实现」，CLI 与编排层无需改动。

### 新增一个平台（尚未有引擎）

在 `platforms.py` 的 `PLATFORMS` 里加一条，并在 `schema.py` 的 `PLATFORM_FIELDS` 里声明它允许哪些字段。此时它会被 `NotImplementedAdapter` 接管。

---

## 目录

```
vidrelay/
├── src/vidrelay/
│   ├── cli.py            命令行入口
│   ├── contract.py       统一返回契约 ★
│   ├── platforms.py      平台注册表与分组
│   ├── schema.py         meta.yaml 校验（含行号定位）
│   ├── secrets.py        凭证管理
│   ├── netguard.py       出口 IP 校验（fail closed）
│   ├── doctor.py         环境自检
│   ├── runner.py         编排层
│   └── adapters/
│       ├── base.py       适配器基类 + 注册表
│       └── <platform>.py 各平台实现
├── tests/
│   ├── fixtures/         校验器测试用的 yaml
│   ├── test_contract.py
│   ├── test_schema.py
│   ├── test_netguard.py
│   └── test_runner.py
├── scripts/
│   └── scan_secrets.py   提交前凭证扫描
└── docs/architecture.md
```
