# StreamingRedactor 有界后缀重扫 — 设计

**状态**:设计定案(2026-07-17)
**类型**:性能 fast-follow(流式 epic 子项目 2 的终审遗留 1 Important)
**范围**:`services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py` 单文件内部算法,纯性能,零对外行为变更。

## 背景与动机

token SSE 帧(PR #1000)的流式脱敏 `StreamingRedactor.feed(text)` 在每个 delta 上对**整个增长 buffer** 跑两遍 regex:`scan_and_redact(self._buf)`(DLP)+ `screen_output(self._buf)`(screen)。一条长答案被切成 N 个 delta,累计 char-scan ≈ `4·L²`(L=答案总长),即 **O(n²)**。该脱敏跑在 `graph_builder/builder.py` 的 agent-node 热路径、事件循环内 —— 长答案(20-50KB)在多租户并发下可给其他并发 run 注入 CPU 卡顿。

现状 buffer 几 KB、bounded → 终审判"非阻塞",但子项目 3(playground 打字机)会**催生长答案流式**,把该 O(n²) 从理论风险变实际热路径。故先修。

**目标**:把每个 delta 的重扫从"整 buffer"降为"有界后缀窗口",全程 **O(n)**,且对所有 bounded pattern 输出**逐字节等价**于当前实现;email(无界)保持既有残留、不更差。

## 正确性基准(已确认)

**bounded 逐字节等价 + email no-worse**:

- 对所有 **bounded pattern**(`screen_output` 全 credential/exfil + `scan_and_redact` 的 card/id/phone),`feed` 分片输出 join 后**逐字节等于** `scan_and_redact(whole).redacted`(即等于当前全扫实现)。既有等价测(逐字 feed == oneshot)全部保留。
- **email** 的 regex 无界,是既有已知残留(见 `streaming_redact.py` 模块 docstring):超过 hold 窗的地址头会在 provisional 预览泄漏,由权威 `updates` 帧兜底。有界重扫**保持同一残留包络,不更差**,加测证。
- `HOLD_CHARS = 64`(emission hold,安全命门)**不改**。仅新增 `RESCAN_LOOKBACK` / `WINDOW`。
- 对外:帧格式 `{step, channel:"content", text}`、gate(`make_token_sink`)、`TokenSink`、公开签名全不变。纯内部。

## 核心不变式:为什么后缀有界

已 emit 的前缀 provably 不再变。未来 append 只能造出或延伸**含新字符**的 regex match —— 即 match 结束位置 ≥ 旧 buffer 末尾。bounded pattern 最长匹配 `L_max_bounded = 19`(card `\b\d{4}(?:[ -]?\d{4}){3}\b`;id=18、phone=11)。故任何新 bounded match 的**起点** ≥ `end - 19`。而 emission frontier 位于 `end - HOLD_CHARS = end - 64` 处,`64 > 19` → 已 emit 区不可能被任何新 bounded match 触及,可安全冻结、不再重扫。

email 无界 → 一个 email 可从早已 emit 的位置延伸至今,既有全扫也无法追溯改写已 emit 的头(`_emitted_len` 已越过)→ 这正是既有残留,有界重扫保持一致。

## 算法

替换 `StreamingRedactor` 的内部状态与 `feed`/`flush` 实现。公开接口(`__init__(*, dlp, screen)`、`feed(text) -> str`、`flush() -> str`)不变。

### 常量

```python
HOLD_CHARS = 64            # 不变:emission hold(≥ 所有 BLOCK 守卫 min-match 39、≥ 所有 bounded DLP max 19)
RESCAN_LOOKBACK = 64       # 新增:冻结点/窗起点behind hold 的回看,须 ≥ L_max_bounded(19) 且 ≥ max screen min-match(39)
WINDOW = HOLD_CHARS + RESCAN_LOOKBACK   # = 128:screen 扫描窗 + 冻结指针滞后量
```

`RESCAN_LOOKBACK = 64` 取 `max(39, 19)=39` 之上的整数余量(与 HOLD_CHARS 同值,便于推理)。

### 状态

```python
self._buf = ""            # 全量保留(内存 O(n) 非瓶颈;瓶颈是 CPU 重扫)
self._emitted_out = 0     # 累计已 emit 的 redacted 字符数(= 旧 _emitted_len 语义)
self._frozen_raw = 0      # raw 偏移:_buf[:_frozen_raw] 的脱敏已 final 且已 emit
self._frozen_out = 0      # _buf[:_frozen_raw] 脱敏后的字符数
self._blocked = False     # 不变:screen latch
```

不变式:`_frozen_out <= _emitted_out`(冻结点 `≤ end - WINDOW < end - HOLD ≈` emit frontier)。

### feed

```python
def feed(self, text: str) -> str:
    if self._blocked:
        return ""
    self._buf += text
    if not text:
        return ""
    if self._screen and screen_output(self._buf[self._window_start():]).blocked:
        self._blocked = True
        return ""
    tail = self._buf[self._frozen_raw:]
    tail_red = self._redact(tail)
    full_red_len = self._frozen_out + len(tail_red)
    boundary = max(self._emitted_out, full_red_len - HOLD_CHARS)
    out = tail_red[self._emitted_out - self._frozen_out : boundary - self._frozen_out]
    self._emitted_out = boundary
    self._advance_frozen()
    return out
```

- `_window_start()` = `max(0, len(self._buf) - WINDOW)`。
- `out` 直接从 `tail_red` 切片,用 `_emitted_out - _frozen_out` 定位,**无需 raw↔redacted 坐标映射**(旧代码同样在 redacted 空间切,这里只是把参照系从"全 redacted"换成"tail_red + 冻结偏移")。
- `boundary` 的 `max(_emitted_out, …)` clamp 保留:redacted 长度因 `[redacted]` 折叠回缩时,boundary 不回退到已 emit 之下(既有 `test_max_clamp_boundary_retreat` 覆盖)。

### _advance_frozen

```python
def _advance_frozen(self) -> None:
    new_frozen = max(0, len(self._buf) - WINDOW)
    if new_frozen <= self._frozen_raw:
        return
    added = len(self._redact(self._buf[self._frozen_raw:new_frozen]))
    if self._frozen_out + added > self._emitted_out:
        return  # 折叠守卫:不冻结尚未 emit 的字符(见下),本帧不推进
    self._frozen_out += added
    self._frozen_raw = new_frozen
```

- 冻结点单调推进到 `end - WINDOW`。newly-frozen 片 `_buf[_frozen_raw:new_frozen]` 独立脱敏 == 全扫对应子串,前提是无 match straddle 两端:`new_frozen = end - WINDOW`,未成形区(末 HOLD)的 bounded match 起点 ≥ `end - HOLD - 19 > end - WINDOW = new_frozen` → 不 straddle;`_frozen_raw` 由归纳是合法冻结点(基:0)。email straddle → 既有残留。
- **折叠守卫(命门)**:`scan_and_redact` 把匹配跨度折成定长 `[redacted]`(11 字符),故 redacted-length 对 raw 前缀**非单调**——一个 PII 跨度在 emit frontier 之后完成时,`redact(_buf[:end-WINDOW])` 的字符数可**瞬时超过** `_emitted_out`。若此时仍推进,`_frozen_out > _emitted_out` → 下帧 `_emitted_out - _frozen_out < 0` → Python 负索引**回绕**取到 buffer 尾部 → 泄漏/错乱。守卫 `_frozen_out + added > _emitted_out` 时**本帧不推进**(折叠是局部瞬态,emission 越过该 PII 跨度后下一两帧即恢复推进)。这保证 `_frozen_out <= _emitted_out` 恒成立 → `feed`/`flush` 的切片 `lo = _emitted_out - _frozen_out >= 0`,**绝无负索引**。
- 停滞有界:折叠跨度局限于末 WINDOW 内,emission 落后 buffer 仅 HOLD,故停滞至多数帧;`tail = _buf[_frozen_raw:]` ≤ ~`2·WINDOW`。
- 每 feed 的两次 `_redact`(tail + newly-frozen 片)长度均有界(≤ ~`2·WINDOW + 单 delta`)→ **每 feed O(WINDOW),全程 O(n)**。

### flush

```python
def flush(self) -> str:
    if self._blocked:
        return ""
    if self._screen and screen_output(self._buf[self._window_start():]).blocked:
        self._blocked = True
        return ""
    tail_red = self._redact(self._buf[self._frozen_raw:])
    out = tail_red[self._emitted_out - self._frozen_out :]
    self._emitted_out = self._frozen_out + len(tail_red)
    return out
```

flush 释放 hold 内残余(不再扣 HOLD_CHARS),语义同旧 flush。

## 正确性论证(逐点)

1. **bounded 等价**:`redact(whole) = frozen_redacted ⊕ redact(tail)`,当且仅当无 bounded match straddle `_frozen_raw`。由核心不变式,冻结点始终 ≥ `L_max_bounded` behind 未成形区 → 成立。故任意分片下 join 输出 == 全扫。既有 `test_prefix_monotonic_chunked_equals_oneshot` / `test_max_clamp_boundary_retreat` 继续通过。
2. **screen latch 不漏**:screen 扫 `_buf[end-WINDOW:]`。credential 的 min-match ≤ 39;当其最后一个 min-match 字符首次到达(该 feed 的 buffer 末尾),整个 min-match(≤39)落在末 WINDOW(128)内 → 当帧被抓 → latch。此刻其头位于 `≥ end-38 > emit frontier(end-64)` → 尚未 emit。**先抓后 emit**,靠既有 `HOLD_CHARS(64) > max_screen_min(39)` 不变式,不依赖归纳。
3. **切片合法(无负索引)**:折叠守卫保证 `_frozen_out ≤ _emitted_out` 恒成立 → `lo = _emitted_out - _frozen_out ≥ 0`;`hi = boundary - _frozen_out ≤ full_red_len - _frozen_out = len(tail_red)`;`boundary ≥ _emitted_out` → `hi ≥ lo`。redacted 长度因 `[redacted]` 折叠回缩时,`boundary` 的 `max(_emitted_out, …)` clamp 令切片退化为空串(不回退已 emit),既有 `test_max_clamp_boundary_retreat` 覆盖。
4. **email no-worse**:email 无界 → 可 straddle 冻结点。若 email 头已 emit(地址长于 hold),既有全扫亦无法追溯(`_emitted_len` 越过),两者同泄漏头;email ≤ WINDOW 则全在窗内、redact 相同。残留包络一致。

## 测试

`services/orchestrator/tests/test_streaming_redact.py` —— 保留全部现有测(等价基准),新增:

1. **straddle 冻结点**:构造 >WINDOW 的安全前缀,把一个 card(19)拆成多 delta 且其字符跨越 `_frozen_raw` 边界(即冻结指针推进后 card 仍未成形)→ join == `scan_and_redact(whole).redacted`,`4111` 不泄漏。
2. **超长填充夹尾部 credential**:`"x"*300 + "sk-"+"a"*24`,screen=True,多 delta → `feed`+`flush` 全 `""`(latch 生效,证 screen 窗未漏)。
3. **随机分片 fuzz**:一段含 card/id/phone 的 bounded 语料,多种切分点(逐字、定长块、随机块),每种 join == `scan_and_redact(whole).redacted`。(随机用固定 seed 或参数化切点,不引入不确定性。)
4. **email no-worse**:一个 >WINDOW 的 email(超长本地部分),多 delta;断言与当前 full-scan 行为一致的残留(邮件头泄漏、`@` 后不新增泄漏)—— 记录为 provisional 契约的已知残留,非回归。
5. **折叠守卫 / 负索引回归**:长安全前缀(>WINDOW)后,把 PII 跨度(card)完成点落在 emit frontier 附近(触发 redacted-length 瞬时折叠回缩),多 delta 喂入 → join == `scan_and_redact(whole).redacted` 且**输出无回绕垃圾**(断言不含 buffer 尾部才有的字符/不含 raw 数字)。此测在无守卫的实现下会因负索引取到 buffer 尾而失败。
6. **O(n) 特征**:monkeypatch `streaming_redact.scan_and_redact` 与 `screen_output` 记录每次入参长度;喂一条长文(如 4400 字符,无 PII)分 50 字符 delta;断言 max 入参长度有界(`<= 3 * WINDOW`,远小于总长)→ 证每 feed 重扫量为常数、非随 n 增长。不做壁钟计时(脆)。

`pytest` 全绿(`cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py`)+ CI-scope mypy + ruff(全库含 tests)。

## 非目标 / YAGNI

- 不改 `HOLD_CHARS`、不改 DLP/screen pattern、不改帧格式或 gate。
- 不消 email 残留(无界,须另加脱敏语义,偏离非流式 parity,超范围)。
- 不做 buffer 内存回收(`_buf` 全存 —— 内存 O(n) 非瓶颈;若将来长答案内存成问题,可丢弃 `_buf[:_frozen_raw]` 并以偏移改写,属独立后续)。
- 不做壁钟性能基准测(脆);以"每 feed 重扫长度有界"的结构性断言证 O(n)。
