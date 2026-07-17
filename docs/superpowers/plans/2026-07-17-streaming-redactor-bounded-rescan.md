# StreamingRedactor 有界后缀重扫 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `StreamingRedactor.feed` 从"每 delta 重扫整个增长 buffer"(≈O(n²))改为"每 delta 只重扫末 WINDOW 字符"(O(n)),对所有 bounded pattern 输出逐字节等价于当前实现,email 残留 no-worse。

**Architecture:** 已 emit 的前缀 provably 不再变(未来 append 只能造出结束于旧 buffer 末尾之后的 match,bounded pattern 最长 19 < HOLD 64 → 已 emit 区不可触及)。维护单调前进的冻结指针 `_frozen_raw`(raw 偏移)+ `_frozen_out`(该处 redacted 字符数),每 feed 只脱敏 `_buf[_frozen_raw:]` 尾片,输出直接从尾片 redaction 切片(免 raw↔redacted 坐标映射)。折叠守卫防 `[redacted]` 折叠致 redacted-length 非单调、进而负索引。

**Tech Stack:** Python 3(orchestrator service),pytest,`expert_work.common.dlp.scan_and_redact` / `expert_work.common.output_screen.screen_output`(既有 regex 守卫,复用不改)。

## Global Constraints

- `HOLD_CHARS = 64` **不改**(emission hold,安全命门:≥ 所有 BLOCK 守卫 min-match 39、≥ 所有 bounded DLP max 19)。
- 新增常量:`RESCAN_LOOKBACK = 64`(须 ≥ 39 且 ≥ 19);`WINDOW = HOLD_CHARS + RESCAN_LOOKBACK`(= 128)。
- **正确性基准**:所有 bounded pattern(`screen_output` 全 credential/exfil + `scan_and_redact` 的 card/id/phone)分片输出 join == `scan_and_redact(whole).redacted`(逐字节等价当前实现);email 无界 = 既有残留,保持 no-worse。
- 公开接口不变:`StreamingRedactor.__init__(*, dlp: bool, screen: bool)`、`feed(text: str) -> str`、`flush() -> str`。`TokenSink` / `make_token_sink` / `TokenPublish` / 帧格式 `{step, channel:"content", text}` 全不变。
- **折叠守卫命门**:`scan_and_redact` 把匹配折成定长 `[redacted]` → redacted-length 对 raw 前缀非单调 → 冻结点 redacted-count 可瞬时超 emit → 负索引回绕。守卫:`_frozen_out + added > _emitted_out` 时本帧不推进冻结,保证 `_frozen_out ≤ _emitted_out` 恒成立。
- 纯内部性能重构,零对外行为变更,单文件 `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`。
- 测试命令:`cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py -v`(裸 `python` 会用坏的系统 3.14,必须 `uv run`)。
- 提交前:CI-scope mypy(`cd services/orchestrator && uv run mypy src/orchestrator/graph_builder/streaming_redact.py`)+ ruff 全库含 tests(`uv run ruff check` / `uv run ruff format --check` from repo root)。

---

## File Structure

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py` | 流式脱敏(buffered-release) | Task 2:新增 `RESCAN_LOOKBACK`/`WINDOW` 常量、重写 `StreamingRedactor` 状态与 `feed`/`flush`、加 `_window_start`/`_advance_frozen` |
| `services/orchestrator/tests/test_streaming_redact.py` | 单测 | Task 1:加 4 个特征/对抗测(等价、straddle、fuzz、长填充 latch、email no-worse);Task 2:加折叠守卫回归测 + O(n) 有界测 |

两个 Task:**Task 1 先把安全网(等价/对抗测)打在当前实现上并证其通过**(锁死"这些断言正确刻画现有行为、能抓回归",与算法改写分离),**Task 2 再换算法**(全部旧测 + Task 1 网 + 新 O(n)/守卫测皆绿)。这是行为保持型重构的正确 TDD:先特征化后重构。

---

## Task 1: 特征化安全网(对抗式等价测,打在当前实现上)

**目的**:在**不改任何生产代码**的前提下,给现有 `StreamingRedactor` 补一组更强的等价/对抗测。它们必须在**当前**实现上全部通过——证明它们正确刻画既有行为、是有效的回归网,而非过拟合到未来的新实现。

**Files:**
- Modify(仅追加测试):`services/orchestrator/tests/test_streaming_redact.py`

**Interfaces:**
- Consumes(既有,不改):`StreamingRedactor(*, dlp: bool, screen: bool)`;`feed(text: str) -> str`;`flush() -> str`;`HOLD_CHARS`;`from expert_work.common.dlp import scan_and_redact`。
- Produces:纯测试,无对外符号。

- [ ] **Step 1: 追加四个特征测到测试文件末尾**

在 `services/orchestrator/tests/test_streaming_redact.py` 末尾追加(保留文件现有全部内容与 import;新增用到的 `import random` 加到文件顶部 import 区):

```python
def test_card_straddling_long_prefix_still_redacted() -> None:
    # 安全长前缀(>128)把发射前沿推得很靠前,再让一张卡号跨多个 delta 完成——
    # 卡号字符落在"已越过发射前沿的旧 buffer 区"之后仍必须整体脱敏、不泄漏。
    prefix = "safe filler text. " * 12  # 216 chars, no PII
    r = StreamingRedactor(dlp=True, screen=False)
    out = r.feed(prefix)
    out += r.feed("account 4111 1111 ")
    out += r.feed("1111 1111 end")
    out += r.flush()
    assert "4111" not in out
    assert out == scan_and_redact(prefix + "account 4111 1111 1111 1111 end").redacted


def test_random_split_equals_oneshot_bounded_corpus() -> None:
    # 含 card / id / phone 的 bounded 语料;多种随机切分点,每种 join 都等于全扫。
    corpus = (
        "contact 13800138000 or card 4111 1111 1111 1111, "
        "id 11010119900307123X, thanks. " + "padding words here. " * 20
    )
    expected = scan_and_redact(corpus).redacted
    rng = random.Random(20260717)  # 固定 seed → 确定性
    for _ in range(25):
        r = StreamingRedactor(dlp=True, screen=False)
        i = 0
        out = ""
        while i < len(corpus):
            step = rng.randint(1, 17)
            out += r.feed(corpus[i : i + step])
            i += step
        out += r.flush()
        assert out == expected, f"mismatch for this split; expected == oneshot redact"


def test_screen_latches_on_credential_after_long_safe_prefix() -> None:
    # 长安全填充(远超 WINDOW)之后才出现凭据:screen 窗必须仍抓到并全扣。
    r = StreamingRedactor(dlp=False, screen=True)
    out = r.feed("x" * 300)          # 已释放一部分安全前缀
    key = "sk-" + "a" * 24           # 命中 _SECRET_PATTERNS
    out += r.feed("here is the key " + key)
    out += r.feed(" trailing")
    out += r.flush()
    assert key not in out            # 凭据不泄漏
    # latch 后不再释放新内容(尾部 " trailing" 也被扣)
    assert "trailing" not in out


def test_long_email_residual_no_worse_than_full_scan() -> None:
    # email 无界:超长本地部分的地址,分片流式与"全扫当前实现"的残留一致
    # (记录既有 provisional 残留,非回归)。断言:流式 join 等于把同一分片喂给
    # 当前实现的结果——用两个独立 redactor 对照,证行为一致。
    text = "mail " + "a" * 200 + "@example.com done " + "z" * 80
    r1 = StreamingRedactor(dlp=True, screen=False)
    r2 = StreamingRedactor(dlp=True, screen=False)
    # 两种不同分片都跑,断言各自 join 相等(等价性对分片不敏感的 bounded 部分成立;
    # email 部分两路一致即证 no-worse 稳定)。
    a = "".join(r1.feed(c) for c in text) + r1.flush()
    chunks = [text[i : i + 13] for i in range(0, len(text), 13)]
    b = "".join(r2.feed(c) for c in chunks) + r2.flush()
    assert a == b
    assert "@example.com" in a or "[redacted]" in a  # 尾部安全内容照常释放
```

- [ ] **Step 2: 运行,确认全部在当前实现上通过**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py -v`
Expected: PASS(全部现有测 + 4 新测全绿)。**若任一新测 FAIL,说明该测过拟合或断言有误——修测,不改生产代码**(本 Task 不动 `streaming_redact.py`)。

> 说明:这几个测在当前 O(n²) 实现上就应通过(当前实现对 bounded 是正确的,只是慢)。它们的价值是 Task 2 换算法后的回归网。

- [ ] **Step 3: Commit**

```bash
git add services/orchestrator/tests/test_streaming_redact.py
git commit -m "test: 强化 StreamingRedactor 等价/对抗测(有界重扫前置安全网)"
```

---

## Task 2: 换成有界后缀重扫 + O(n)/守卫测

**目的**:重写 `StreamingRedactor` 内部为有界后缀重扫;全部旧测 + Task 1 安全网 + 新增的折叠守卫回归测与 O(n) 有界测皆绿。

**Files:**
- Modify:`services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py:29-89`(常量块 `HOLD_CHARS` 之后新增两常量;重写 `StreamingRedactor` 的 `__init__`/`_redact`/`feed`/`flush`,新增 `_window_start`/`_advance_frozen`)
- Modify(追加测试):`services/orchestrator/tests/test_streaming_redact.py`

**Interfaces:**
- Consumes:同 Task 1;另新增模块级 `WINDOW: int`、`RESCAN_LOOKBACK: int`(供 O(n) 测引用 `streaming_redact.WINDOW`)。
- Produces:`StreamingRedactor` 公开签名不变(`__init__(*, dlp, screen)` / `feed(text) -> str` / `flush() -> str`);内部新私有方法 `_window_start(self) -> int`、`_advance_frozen(self) -> None`;新状态 `_emitted_out`/`_frozen_raw`/`_frozen_out`(替换旧 `_emitted_len`)。

- [ ] **Step 1: 先写两个新测(RED)——O(n) 有界 + 折叠守卫回归**

在 `services/orchestrator/tests/test_streaming_redact.py` 末尾追加。注意:第一个测引用 `streaming_redact.WINDOW`(当前实现无此符号 → `AttributeError` → RED);第二个在无守卫实现下会因负索引回绕而 FAIL。

```python
def test_rescan_work_is_bounded_not_quadratic(monkeypatch) -> None:
    # monkeypatch 记录每次传给守卫的文本长度;喂长文,断言 max 入参有界(常数),
    # 证每 feed 重扫量不随总长增长(O(n) 全程,非 O(n²))。
    from orchestrator.graph_builder import streaming_redact as sr

    max_len = 0
    real_scan = sr.scan_and_redact
    real_screen = sr.screen_output

    def spy_scan(text):
        nonlocal max_len
        max_len = max(max_len, len(text))
        return real_scan(text)

    def spy_screen(text, **kw):
        nonlocal max_len
        max_len = max(max_len, len(text))
        return real_screen(text, **kw)

    monkeypatch.setattr(sr, "scan_and_redact", spy_scan)
    monkeypatch.setattr(sr, "screen_output", spy_screen)

    r = sr.StreamingRedactor(dlp=True, screen=True)
    total = "abcdefghij " * 400  # 4400 chars, no PII
    delta = 50
    for i in range(0, len(total), delta):
        r.feed(total[i : i + delta])
    r.flush()
    assert max_len <= 3 * sr.WINDOW  # 384 << 4400 → 每 feed 重扫为常数,非 O(n)


def test_collapse_guard_no_negative_index_leak() -> None:
    # PII 折叠(digits→[redacted])使 redacted-length 瞬时回缩;若冻结点 redacted-count
    # 越过已 emit,下帧 lo=_emitted_out-_frozen_out<0 → Python 负索引回绕取 buffer 尾。
    # 构造:安全前缀把 emit 前沿推到卡号完成点附近,分片完成卡号。
    prefix = "y" * 130 + " your card number is "
    r = StreamingRedactor(dlp=True, screen=False)
    out = r.feed(prefix)
    out += r.feed("4111 1111 1111 ")
    out += r.feed("1111 tail-marker")
    out += r.flush()
    assert "4111" not in out                    # 不泄漏原数字
    assert out.count("tail-marker") == 1         # 无回绕重复
    assert out == scan_and_redact(prefix + "4111 1111 1111 1111 tail-marker").redacted
```

- [ ] **Step 2: 运行两新测,确认 RED**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py::test_rescan_work_is_bounded_not_quadratic tests/test_streaming_redact.py::test_collapse_guard_no_negative_index_leak -v`
Expected: FAIL —— `test_rescan_work_is_bounded_not_quadratic` 报 `AttributeError: module ... has no attribute 'WINDOW'`(或 max_len≈4400 超界);`test_collapse_guard_no_negative_index_leak` 在当前实现下**可能通过**(当前全扫无负索引问题)——这没关系,它是 Task 2 新算法的守卫回归网,Task 2 后必须仍绿。

> 若 `test_collapse_guard_no_negative_index_leak` 当前就通过,不必强求它 RED——它锁的是新算法不得引入负索引回归。`test_rescan_work_is_bounded_not_quadratic` 是本 Task 的真 RED 锚点。

- [ ] **Step 3: 新增常量 + 重写 `StreamingRedactor`**

编辑 `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`。

(a) 在 `HOLD_CHARS = 64` 那行之后(现第 42 行后)、`class StreamingRedactor` 之前,新增:

```python
#: Extra look-back behind the emission hold before the rescan / freeze boundary.
#: The bounded suffix rescan is output-identical to a full-buffer rescan only if
#: no bounded-pattern match straddles the frozen boundary; that holds when this
#: is >= every BLOCK guard's MAX minimum-match length (39, Google API key) AND
#: >= every fixed-shape DLP pattern's MAX match length (19, payment card).
RESCAN_LOOKBACK = 64

#: Size of the raw-buffer suffix rescanned on each ``feed`` (the screen scan
#: window and the frozen-pointer lag). Everything before the window start is
#: finalized and never rescanned again — this is what makes ``feed`` O(1)
#: amortized (O(n) over the whole stream) instead of O(n) per delta.
WINDOW = HOLD_CHARS + RESCAN_LOOKBACK
```

(b) 把 `class StreamingRedactor` 的 `__init__` 到 `flush`(现第 55–89 行)整体替换为:

```python
    def __init__(self, *, dlp: bool, screen: bool) -> None:
        self._dlp = dlp
        self._screen = screen
        self._buf = ""
        #: Redacted chars emitted so far (monotonic; = old ``_emitted_len``).
        self._emitted_out = 0
        #: Raw offset of the finalized boundary: ``_buf[:_frozen_raw]`` redaction
        #: is settled and already emitted, so it is never rescanned again.
        self._frozen_raw = 0
        #: Redacted-char count of ``_buf[:_frozen_raw]``. Invariant (upheld by
        #: the collapse guard in ``_advance_frozen``): ``_frozen_out <= _emitted_out``.
        self._frozen_out = 0
        self._blocked = False

    def _redact(self, text: str) -> str:
        return scan_and_redact(text).redacted if self._dlp else text

    def _window_start(self) -> int:
        return max(0, len(self._buf) - WINDOW)

    def _advance_frozen(self) -> None:
        # Push the frozen boundary up to ``end - WINDOW`` so the rescanned tail
        # stays bounded. Collapse guard: a PII span completing just past the
        # emission frontier makes ``redact`` shrink, so the newly-frozen slice's
        # redacted length can momentarily exceed what we've emitted; freezing it
        # would drive ``_frozen_out > _emitted_out`` and a later negative slice
        # index (Python wraps → tail leak). If so, skip advancing this feed —
        # the collapse is transient; emission catches up within a feed or two.
        new_frozen = max(0, len(self._buf) - WINDOW)
        if new_frozen <= self._frozen_raw:
            return
        added = len(self._redact(self._buf[self._frozen_raw : new_frozen]))
        if self._frozen_out + added > self._emitted_out:
            return
        self._frozen_out += added
        self._frozen_raw = new_frozen

    def feed(self, text: str) -> str:
        if self._blocked:
            return ""
        self._buf += text
        if not text:
            return ""
        if self._screen and screen_output(self._buf[self._window_start() :]).blocked:
            self._blocked = True
            return ""
        tail_red = self._redact(self._buf[self._frozen_raw :])
        full_red_len = self._frozen_out + len(tail_red)
        boundary = max(self._emitted_out, full_red_len - HOLD_CHARS)
        out = tail_red[self._emitted_out - self._frozen_out : boundary - self._frozen_out]
        self._emitted_out = boundary
        self._advance_frozen()
        return out

    def flush(self) -> str:
        if self._blocked:
            return ""
        if self._screen and screen_output(self._buf[self._window_start() :]).blocked:
            self._blocked = True
            return ""
        tail_red = self._redact(self._buf[self._frozen_raw :])
        out = tail_red[self._emitted_out - self._frozen_out :]
        self._emitted_out = self._frozen_out + len(tail_red)
        return out
```

> 注意:`screen` 检查现扫 `_buf[_window_start():]`(有界)而非整 `_buf`——等价性由"HOLD(64) > max screen min-match(39) → 凭据 min-match 到齐那帧即在 WINDOW 内被抓、先于其头 emit"保证(见 spec 正确性论证 §2)。旧 `feed`/`flush` 里的 `self._buf += text`、`if not text` 提前返回、`_blocked` 提前返回、`max(...)` clamp 语义全部保留。

- [ ] **Step 4: 运行整测文件,确认全绿**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py -v`
Expected: PASS —— 全部现有测 + Task 1 四测 + 本 Task 两新测(含 O(n) 有界、折叠守卫)全绿。

- [ ] **Step 5: 跑更广的 orchestrator 回归(TokenSink 经 StreamingRedactor)**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py tests/test_llm_router_streaming.py tests/test_sse_persistence.py -v`
Expected: PASS —— TokenSink 用例(经改写后的 StreamingRedactor)与 token 帧路径无回归。

- [ ] **Step 6: 类型 + lint 闸**

Run:
```bash
cd services/orchestrator && uv run mypy src/orchestrator/graph_builder/streaming_redact.py
```
Expected: `Success: no issues found`。

Run(from repo root):
```bash
uv run ruff check services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py services/orchestrator/tests/test_streaming_redact.py
uv run ruff format --check services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py services/orchestrator/tests/test_streaming_redact.py
```
Expected: 均无 error/无需 reformat。若 `format --check` 报差异,跑 `uv run ruff format <files>` 后重跑。

> CI 的 `ruff check` 跑全库含 tests(历史坑):新增测试文件里的 `import random`、行长、格式都要过。

- [ ] **Step 7: Commit**

```bash
git add services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py services/orchestrator/tests/test_streaming_redact.py
git commit -m "perf: StreamingRedactor 有界后缀重扫(O(n²)→O(n),bounded 逐字节等价)"
```

---

## Self-Review(计划对照 spec)

**1. Spec coverage:**
- 核心不变式 / 有界后缀 → Task 2 Step 3(算法 + 冻结指针)。✅
- 折叠守卫(负索引命门)→ Task 2 Step 3 `_advance_frozen` + Step 1 `test_collapse_guard_no_negative_index_leak`。✅
- 常量 `RESCAN_LOOKBACK`/`WINDOW`、`HOLD_CHARS` 不动 → Task 2 Step 3(a)。✅
- bounded 逐字节等价 → Task 1 straddle/fuzz 测 + 既有等价测。✅
- screen latch 先抓后 emit → Task 1 `test_screen_latches_...`。✅
- email no-worse → Task 1 `test_long_email_residual_...`。✅
- O(n) 结构性证(非壁钟)→ Task 2 `test_rescan_work_is_bounded_not_quadratic`。✅
- 公开接口 / 帧格式 / gate 不变 → Task 2 保留签名;既有 `TokenSink`/`make_token_sink` 测未改动仍绿(Step 5)。✅
- 非目标(不改 HOLD/pattern/帧/内存回收)→ 计划未触及,符合。✅

**2. Placeholder scan:** 无 TBD/TODO;每个 code step 给全码;测试给全断言。✅

**3. Type consistency:** `_emitted_out`/`_frozen_raw`/`_frozen_out`(int)、`_window_start()->int`、`_advance_frozen()->None`、`feed(str)->str`、`flush()->str`、模块级 `WINDOW`/`RESCAN_LOOKBACK`(int)在 Task 2 内一致;测试引用 `streaming_redact.WINDOW`/`sr.scan_and_redact`/`sr.screen_output` 与生产模块符号一致。旧 `_emitted_len` 被 `_emitted_out` 完全取代(无残留引用——旧测不引用私有状态,仅黑盒)。✅
