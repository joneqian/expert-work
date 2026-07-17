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

已 emit 的前缀 provably 不再变(未来 append 只能造出/延伸**含新字符**的 match,即结束位置 ≥ 旧 buffer 末尾;emission frontier 在 `end - HOLD = end - 64` 处,而 bounded match 最长 19,`64 > 19` → 未成形区的新 match 触不到 emission frontier 附近的已 emit 区)。所以每 feed **不必**从 0 重扫,只需重扫一段有界后缀。

**关键更正(设计一稿曾错)**:不能因此断言"冻结点 `new_frozen = end - WINDOW` 处无 match straddle"。`new_frozen` 是**总 buffer 长的函数**,随 buffer 增长会从后方追上一个**早期** match(当 buflen ≈ match_pos + WINDOW),落其中间。故冻结**不能**盲目推进到 `end - WINDOW`,而要在推进前用**精确分割等价**判据校验 `new_frozen` 是全上下文脱敏的干净切点(见 `_advance_frozen`),straddle 时 defer。输出正确性始终由全上下文 `tail_red = redact(_buf[_frozen_raw:])` 保证,`_frozen_raw` 只停在已验证的干净点上。

email 无界 → 一个 email 可从早已 emit 的位置延伸至今,既有全扫也无法追溯改写已 emit 的头(`_emitted_len` 已越过)→ 这正是既有残留,有界重扫保持一致。

## 算法

替换 `StreamingRedactor` 的内部状态与 `feed`/`flush` 实现。公开接口(`__init__(*, dlp, screen)`、`feed(text) -> str`、`flush() -> str`)不变。

### 常量

```python
HOLD_CHARS = 64            # 不变:emission hold(≥ 所有 BLOCK 守卫 min-match 39、≥ 所有 bounded DLP max 19)
RESCAN_LOOKBACK = 64       # 新增:screen 窗在 hold 之外的回看;须 ≥ max screen min-match(39),使凭据 min-match 到齐当帧即落窗被抓(§2)
WINDOW = HOLD_CHARS + RESCAN_LOOKBACK   # = 128:screen 扫描窗大小 + 冻结指针目标滞后量(性能界)
```

> 注:bounded 等价的**正确性**不依赖 WINDOW 取值——由 `_advance_frozen` 的精确分割等价判据保证(见下)。WINDOW 只决定 ① screen 扫描窗(须够大以先抓后 emit)② 每 feed 重扫量(性能)。故 `RESCAN_LOOKBACK` 的约束是 `≥ 39`(screen),非"≥19 防 straddle"(该防线由分割等价判据接管)。

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
    self._advance_frozen(tail_red)
    return out
```

- `_window_start()` = `max(0, len(self._buf) - WINDOW)`。
- `out` 直接从 `tail_red` 切片,用 `_emitted_out - _frozen_out` 定位,**无需 raw↔redacted 坐标映射**(旧代码同样在 redacted 空间切,这里只是把参照系从"全 redacted"换成"tail_red + 冻结偏移")。
- `boundary` 的 `max(_emitted_out, …)` clamp 保留:redacted 长度因 `[redacted]` 折叠回缩时,boundary 不回退到已 emit 之下(既有 `test_max_clamp_boundary_retreat` 覆盖)。

### _advance_frozen

```python
def _advance_frozen(self, tail_red: str) -> None:
    new_frozen = max(0, len(self._buf) - WINDOW)
    if new_frozen <= self._frozen_raw:
        return
    head_red = self._redact(self._buf[self._frozen_raw:new_frozen])
    retained_red = self._redact(self._buf[new_frozen:])
    # 洁净判据 = 精确分割等价:只有当 buffer 的脱敏恰好在 new_frozen 处一分为二
    # (redact(head) ++ redact(retained) == 全上下文 tail_red)才冻结到 new_frozen。
    if head_red + retained_red != tail_red:
        return  # new_frozen straddle 了某个 match → 本帧不推进
    added = len(head_red)
    if self._frozen_out + added > self._emitted_out:
        return  # 折叠守卫:不冻结尚未 emit 的字符,保 lo>=0
    self._frozen_out += added
    self._frozen_raw = new_frozen
```

- **洁净判据(命门 1 —— 曾错、已更正)**:`new_frozen = end - WINDOW` 随 buffer 增长会**从后方追上一个早期 match**(当 buflen ≈ match_pos + WINDOW),落在其中间。**前缀判据 `tail_red.startswith(head_red)` 不够**:`scan_and_redact` 用**定长** `[redacted]` 替换,一个 cut 若形成**另一个**折叠 match(实证:18 位 id_card 的前 16 位数字独立命中 credit_card 形状),head 与 tail 都得到 `[redacted]` → 前缀判据**假阳性** → 冻结进 match 中间 → `_frozen_out` 计数错 → 下帧重复发射(observed:`words`→`wordrds`)。正解是**精确分割等价** `redact(head) ++ redact(retained) == tail_red`:straddle 时 `[redacted]3X…` ≠ `[redacted], …` → 判否 → defer。等价成立即证 `new_frozen` 是全上下文脱敏的干净切点、`added` 精确、`_frozen_out` 与 `len(redact(_buf[:new_frozen]))` 同步。
- **折叠守卫(命门 2)**:`[redacted]` 折叠使 redacted-length 对 raw 前缀**非单调**——PII 跨度在 emit frontier 之后完成时,`len(redact(_buf[:end-WINDOW]))` 可**瞬时超过** `_emitted_out`。若仍推进,`_frozen_out > _emitted_out` → 下帧 `_emitted_out - _frozen_out < 0` → Python 负索引**回绕**取 buffer 尾 → 泄漏。守卫 `_frozen_out + added > _emitted_out` 时**本帧不推进**,保证 `_frozen_out <= _emitted_out` 恒成立 → `lo >= 0` 绝无负索引。
- 两道判据都会 defer(不推进冻结),但**只影响性能不影响正确性**:输出永远取自全上下文 `tail_red`,`_frozen_raw` 停在旧的合法干净点。
- 停滞有界:straddle 由 bounded match(≤19)引起时,new_frozen 数帧内越过;`_frozen_raw` 只在等价成立时前进,`tail = _buf[_frozen_raw:]` 常态 ≤ ~`2·WINDOW`。**病态长 email**(无界)straddle 冻结点时停滞时长 ∝ email 长,tail 退化为 O(email 长)—— 仍远优于原 O(n²) 全扫,且 email 是已声明残留,罕见,可接受(graceful degradation)。
- 每 feed 的三次 `_redact`(tail + head + retained)长度均有界(常态 ≤ ~`2·WINDOW + 单 delta`)→ **每 feed O(WINDOW),全程 O(n)**。

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

1. **bounded 等价**:输出 = `tail_red[lo:hi]`,其中 `tail_red = redact(_buf[_frozen_raw:])`。只要 `_frozen_raw` 是全上下文脱敏的干净切点(`redact(_buf) == redact(_buf[:_frozen_raw]) ++ tail_red`)且 `_frozen_out == len(redact(_buf[:_frozen_raw]))`,则 `tail_red == redact(_buf)[_frozen_out:]`,`tail_red[lo:hi] == redact(_buf)[_emitted_out:boundary]` → 任意分片下 join == 全扫。这两条不变式由 `_advance_frozen` 的**精确分割等价**判据维护(仅当 `redact(head) ++ redact(retained) == tail_red` 才推进冻结,基:`_frozen_raw=0` 平凡干净)。经 20000 次随机分片 + 全定长块 + 逐字 fuzz(含 id_card/credit_card 重叠、相邻 PII、window 边界 PII 等对抗语料)对照 `scan_and_redact` oracle 验证。既有 `test_prefix_monotonic_chunked_equals_oneshot` / `test_max_clamp_boundary_retreat` 继续通过。
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
