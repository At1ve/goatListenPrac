# -*- coding: utf-8 -*-
"""
按句子边界重新切分 Whisper 的分段。

问题：Whisper 的 VAD 分段是按静音/时长切的，72% 的段尾没有句末标点，
      也就是说换气点大量落在一句话中间。例如：

        段1: ...English learners,          ← 逗号
        段2: where we have natural ...     ← 小写开头（从句）
        段3: ...that we see online,        ← 逗号
        段4: and just everyday things ...  ← 才是句号

      这 4 段其实是【一句话】。

做法（关键改动）：
  1. 以 Whisper 段为基本单元，**先判断每段的结尾是否成句**
  2. 不成句的段与后续段合并，直到出现句末标点
  3. 合并后的超长句，**优先在 Whisper 原有的换气边界处切开**
     （那本来就是说话人自然的停顿，比按字符比例硬切好得多）
  4. 只有在没有换气边界可用时，才退回到逗号/从句处切

用法：
  python resplit.py <材料名>            # 重新生成 segments.json
  python resplit.py <材料名> --dry-run  # 只看结果不写入
"""
import sys
import io
import os
import re
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core


def _fix_console():
    """
    让 Windows 控制台能正确输出中文。

    【必须只在命令行直接运行本文件时调用】
    绝对不能在模块顶层执行 —— core.py 会在转写流程里 `import resplit`，
    那时如果这里重设了 sys.stdout，app.py 先前包好的那层 wrapper 会被
    回收掉，而它析构时会关闭底层的 buffer，导致之后所有 print 都抛
    "I/O operation on closed file"，整个导入流程失败。

    （这个 bug 真实发生过：表现是「识别完音频后提示导入失败」。）
    """
    try:
        if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer,
                                          encoding="utf-8",
                                          errors="replace")
        if sys.stderr is not None and hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer,
                                          encoding="utf-8",
                                          errors="replace")
    except Exception:
        pass


# 句末标点（后面跟空格或引号再跟大写字母，才算句子边界）
SENT_END = re.compile(r'(?<=[.?!])(?=[\s"\']+[A-Z"\'])')
# 常见缩写，不该在这里断开
ABBREV = {"Mr", "Mrs", "Ms", "Dr", "Prof", "St", "Jr", "Sr", "vs", "etc",
          "e.g", "i.e", "U.S", "U.K", "a.m", "p.m", "No", "Inc", "Ltd"}

# 段尾是这些词 → 句子肯定没说完
TAIL_OPEN = re.compile(
    r"\b(a|an|the|and|but|or|nor|so|yet|for|to|of|in|on|at|by|with|from|"
    r"into|onto|over|under|about|as|than|that|which|who|whom|whose|where|"
    r"when|while|if|because|though|although|since|unless|until|after|"
    r"before|is|are|was|were|be|been|being|am|do|does|did|have|has|had|"
    r"will|would|shall|should|can|could|may|might|must|my|your|his|her|"
    r"our|their|its|this|these|those|very|more|most|just|only|also|not)$",
    re.I)

# 段首是这些词 → 大概率是上一句的延续
HEAD_CONT = re.compile(
    r"^(and|but|or|nor|so|yet|because|which|that|who|whom|whose|where|when|"
    r"while|if|though|although|as|than|then|also|just|even|still|plus|"
    r"to|of|in|on|at|by|from|with|for|into|over|about|"
    r"it|they|we|you|he|she|this|these|those|there|here)\b", re.I)

# 句末标点
END_PUNCT = re.compile(r'[.?!]["\')\]]*$')

# 这些词后面【绝对】不该断句（断了语义就悬空）
DANGLE_TAIL = {
    "a", "an", "the",
    "of", "to", "in", "on", "at", "by", "with", "from", "into", "onto",
    "over", "under", "about", "for", "as", "than",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "have", "has", "had",
    "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "my", "your", "his", "her", "our", "their", "its",
    "this", "these", "those", "very", "more", "most", "only",
    # 连词：留在上一句末尾会显得话没说完
    # （"…because" | "then you think…" 读起来断得突兀）
    "and", "but", "or", "nor", "so", "yet", "because", "since",
    "although", "though", "while", "when", "if", "that", "which",
}


def split_sentences(text):
    """把一段文本切成句子列表"""
    text = text.strip()
    if not text:
        return []

    # 先按 句末标点+空格+大写 切
    parts = SENT_END.split(text)

    # 合并被误切的缩写（如 "Mr. Smith"）
    merged = []
    for p in parts:
        if merged:
            prev = merged[-1]
            # 上一段以缩写结尾 → 合并回来
            tail = prev.rstrip().rstrip(".").split()[-1] if prev.rstrip() else ""
            if tail in ABBREV:
                merged[-1] = prev + p
                continue
        merged.append(p)
    return [p.strip() for p in merged if p.strip()]


def resplit(name, dry_run=False):
    """
    重新切分策略（重要）：

    Whisper 的分段大致对应说话人的换气点，本身是有价值的边界。
    但它的切点常常落在句子中间（把 "...our entire" | "library. But..." 切开），
    而且有时一段里塞了好几个完整句子。

    所以做法是：
      1. 以 Whisper 的每段为一个基本单元（保留它作为换气边界）
      2. 段内若含多个完整句子 → 按句子切分，时间按字符比例分配
      3. 段尾若是残句（结尾无句末标点）→ 与下一段开头合并到句子结束
      4. 过短的碎句向后并入
      5. 过长的句子在逗号处切开
    """
    tdir = os.path.join(core.TRANSCRIPT_DIR, name)
    src = os.path.join(tdir, name + ".segments.json")
    if not os.path.exists(src):
        print("找不到:", src)
        return 1

    data = json.load(open(src, encoding="utf-8"))
    old = [s for s in data["segments"] if s["text"].strip()]
    total_dur = data["duration"]
    if not old:
        print("没有内容")
        return 1

    # ---- 1. 先把所有段落拼起来（保留每段的时间戳，用于映射）----
    pieces = []          # [(字符起点, 字符终点, 时间起点, 时间终点)]
    buf = []
    pos = 0
    for s in old:
        t = s["text"].strip()
        if buf:
            buf.append(" ")
            pos += 1
        c0 = pos
        buf.append(t)
        pos += len(t)
        pieces.append((c0, pos, s["start"], s["end"]))
    text = "".join(buf)

    def time_at(cp):
        for c0, c1, st, en in pieces:
            if c0 <= cp < c1:
                if c1 <= c0:
                    return st
                return st + (en - st) * ((cp - c0) / (c1 - c0))
        return pieces[-1][3] if pieces else 0.0

    # ---- 2. 在整篇上按句末标点切 ----
    sents = split_sentences_global(text)

    new_segs = []
    for t, cs, ce in sents:
        st = time_at(cs)
        en = time_at(max(cs, ce - 1))
        new_segs.append({"text": t.strip(), "start": round(st, 3),
                         "end": round(en, 3)})

    # ---- 3. 合并过短 / 悬空的碎句 ----
    #
    # 关键修复：
    #   旧版本的条件是
    #       (太短 or 上句没收尾) and 上句没超过上限
    #   那个「上句没超过上限」会把该合并的长句挡在外面 —— 结果是
    #       "…and why is my co-host,"  |  "Anna."
    #       "…I'm like, okay,"        |  "I'm glad I'm not into that."
    #   这种一句被劈开的情况留着不合并（实测有 42 处）。
    #
    # 合并策略（重要）
    #
    # 用户的要求：「一个分句里要能得到一份完整的语意，
    #              避免出现小于 15 词的分句，但允许出现。」
    #
    # 所以 15 词是一个**目标**，不是硬性规定。具体分两种情况：
    #
    #   A. 残句（结尾没有句末标点，语义接得上下一句）
    #      → 【必须】接着合并，直到凑够 15 词或句子自然结束。
    #        这是真正要消灭的对象：
    #          "…and why is my co-host,"  |  "Anna."
    #          "…revealed the answer"     |  "to your question?"
    #
    #   B. 完整短句（有句末标点，自己就是一句）
    #      → 【保留】。"Goodbye." / "Hello, Anna." / "That's right."
    #        这些是对话里的正常应答，硬并进邻句反而把不相干的话粘一起。
    #
    # 长度上限沿用之前的数据：平均 13 词、最长 30 词左右听感最好。
    TARGET_WORDS = 15          # 目标下限：残句合并要凑够这个数
    MIN_DUR, MIN_WORDS = 1.2, 4
    MAX_DUR, MAX_WORDS = 14.0, 30
    # 合并的硬上限：先把句子接完整，之后第 4 步再统一按从句切开。
    HARD_WORDS, HARD_DUR = 45, 26.0
    # 接完整之后还能容忍的长度。超过这个才真的去切。
    KEEP_WORDS, KEEP_DUR = 40, 22.0

    def unfinished(t):
        """句子是否没说完（结尾不是句末标点）"""
        t = t.strip()
        if not t:
            return False
        if END_PUNCT.search(t):
            return False
        # 以逗号/分号/破折号结尾，或干脆没标点 → 没说完
        return True

    def complete(t):
        """是不是一个自成一体、不需要接别的句子"""
        return bool(END_PUNCT.search(t.strip()))

    merged = []
    for s in new_segs:
        if merged:
            p = merged[-1]
            pdur = p["end"] - p["start"]
            pw = len(p["text"].split())
            dur = s["end"] - s["start"]
            w = len(s["text"].split())

            # 上句没说完 → 一定接上
            if unfinished(p["text"]):
                # 接上之后如果还不到目标词数，就继续接（循环会处理）
                if (pw + w <= HARD_WORDS
                        and pdur + dur <= HARD_DUR):
                    p["text"] = p["text"].rstrip() + " " + s["text"].lstrip()
                    p["end"] = s["end"]
                    p["just_merged"] = True
                    continue

            # 上句已经完整，但还是偏短，而下一句是「残句的开头」
            # （即下一句自己不成句）→ 把下一句拉进来凑语义
            elif (pw < TARGET_WORDS and not complete(p["text"])):
                if (pw + w <= MAX_WORDS and pdur + dur <= MAX_DUR):
                    p["text"] = p["text"].rstrip() + " " + s["text"].lstrip()
                    p["end"] = s["end"]
                    p["just_merged"] = True
                    continue

            # 兜底：极短的碎屑（不到 MIN_WORDS）且不成句 → 并进去
            elif ((dur < MIN_DUR or w < MIN_WORDS)
                    and not complete(s["text"])):
                if (pw + w <= MAX_WORDS and pdur + dur <= MAX_DUR):
                    p["text"] = p["text"].rstrip() + " " + s["text"].lstrip()
                    p["end"] = s["end"]
                    p["just_merged"] = True      # 别漏这个标志，
                    continue                     # 否则第 4 步会把它又切开

        merged.append(dict(s))

    # ---- 3.5 收尾：把偏短的段合并到 15 词左右 ----
    #
    # 这一步处理三种情况（都要让「一个分句里能得到完整语义」）：
    #
    #   1. 残句（不成句）→ 必须接着合，直到成句
    #   2. 残句且仍偏短 → 继续吃下一句，凑到 TARGET_WORDS
    #   3. 连续多个完整短句，且语义接得上 → 也合并
    #
    # 第 3 种是关键。看这个真实例子：
    #     [123]  7词 | However, prices have gone up a lot.
    #     [124]  5词 | There's a lot of inflation.
    #     [125]  6词 | That's not necessarily the case anymore.
    #     [126]  7词 | Of course, it's still much more convenient.
    #   四句都在讲同一件事（方便但涨价了），合成 25 词一句更好读。
    #
    # 但下面这种不能合 —— 是另一个人在插话：
    #     [140]  3词 | Okay, that's wild.
    #     [141]  4词 | Burger and chips, right?
    #   所以要有「话题是否接得上」的判断（见 _topic_break）。
    changed = True
    guard = 0
    while changed and guard < 20:
        changed = False
        guard += 1
        out = []
        i = 0
        while i < len(merged):
            cur = dict(merged[i])        # 拷贝，避免改到原对象
            consumed = 1                 # 本项吃掉了几项

            # 连续往后吃，直到不满足合并条件
            while i + consumed < len(merged):
                nxt = merged[i + consumed]
                cw = len(cur["text"].split())
                cdur = cur["end"] - cur["start"]
                nw = len(nxt["text"].split())
                ndur = nxt["end"] - nxt["start"]

                cur_ok = complete(cur["text"])
                nxt_ok = complete(nxt["text"])
                room = (cw + nw <= HARD_WORDS and cdur + ndur <= HARD_DUR)

                merge_this = False
                if room:
                    if not cur_ok:
                        # 本句没说完 → 必须把下一句接上来
                        merge_this = True
                    elif not nxt_ok and cw >= TARGET_WORDS:
                        # 本句完整且够长，下一句残缺。
                        # 不要把它拉进来 —— 那会把
                        # "…a lot of smoke. But because Canada has…" 粘一起。
                        # 让残缺的那句自己去接它后面（外层循环会处理）。
                        merge_this = False
                    elif cw < TARGET_WORDS and not _topic_break(cur, nxt):
                        # 两句都完整，但本句偏短、话题接得上 → 合读更顺
                        merge_this = True

                if not merge_this:
                    break

                cur["text"] = cur["text"].rstrip() + " " + nxt["text"].lstrip()
                cur["end"] = nxt["end"]
                cur["just_merged"] = True
                consumed += 1
                changed = True

            out.append(cur)
            i += consumed
        merged = out

    # ---- 4. 过长的句子：切在从句/标点处 ----
    #
    # 但有个原则（用户明确要求）：
    #   「一个分句里要能得到一份完整的语意」
    # 所以如果切出来的某一块太短（比如只剩 "so far really." 这种尾巴），
    # 就宁可不切 —— 长句顶多听着累，碎片会让整句意思不完整。
    #
    # 长度上限：合并过的句子放宽（那是有意接完整的），
    #          本来就长的句子按 MAX_WORDS。
    final = []
    for s in merged:
        dur = s["end"] - s["start"]
        words = len(s["text"].split())

        limit_words = KEEP_WORDS if s.get("just_merged") else MAX_WORDS
        limit_dur = KEEP_DUR if s.get("just_merged") else MAX_DUR

        if words <= limit_words and dur <= limit_dur:
            final.append({"text": s["text"], "start": s["start"],
                          "end": s["end"]})
            continue

        chunks = _split_by_clause(s, MAX_WORDS)

        # 切出来的块能不能独立成立？
        # 不行的两种情况：
        #   1. 太短（不到 MIN_CHUNK 词）
        #   2. 以逗号结尾且不到目标词数 —— 语义上还要靠下一块
        #      （"But because Canada has so much forest and just wilderness,"）
        # 出现任一种就整句保留不切：
        # 长句顶多听着累，把一句话劈成语义不完整的碎片才是真难用。
        MIN_CHUNK = max(6, MAX_WORDS // 3)
        bad = False
        for c in chunks:
            t = c["text"].strip()
            w = len(t.split())
            if w < MIN_CHUNK:
                bad = True
                break
            if w < TARGET_WORDS and not complete(t):
                bad = True
                break

        if len(chunks) <= 1 or bad:
            final.append({"text": s["text"], "start": s["start"],
                          "end": s["end"]})
            continue

        for c in chunks:
            final.append({"text": c["text"], "start": round(c["start"], 3),
                          "end": round(c["end"], 3)})

    merged = final

    # ---- 5. 时间单调性 + 编号 ----
    for i in range(1, len(merged)):
        if merged[i]["start"] < merged[i - 1]["end"]:
            merged[i]["start"] = merged[i - 1]["end"]
        if merged[i]["end"] <= merged[i]["start"]:
            merged[i]["end"] = merged[i]["start"] + 0.5
    for i, s in enumerate(merged):
        s["id"] = i

    new_data = {"file": data.get("file"), "duration": total_dur,
                "language": data.get("language", "en"), "segments": merged}

    print(f"原 {len(old)} 段  ->  新 {len(merged)} 段")
    print(f"平均每段 {total_dur/max(1,len(merged)):.1f}s\n")

    print("=== 前 12 句 ===")
    for s in merged[:12]:
        w = len(s["text"].split())
        print(f"  [{s['id']+1:3d}] {s['end']-s['start']:5.1f}s {w:2d}词  {s['text'][:76]}")

    multi = sum(1 for s in merged
                if re.search(r"[.?!]\s+[A-Z]", re.sub(r"[.?!]+$", "", s["text"])))
    noend = sum(1 for s in merged if not re.search(r"[.?!,;:]$", s["text"].strip()))
    long_ = sum(1 for s in merged if s["end"] - s["start"] > 12)
    print(f"\n含多句的段: {multi}   (原 228)")
    print(f"结尾无标点: {noend}   (原 182)")
    print(f"超过 12 秒: {long_}")

    if dry_run:
        print("\n[dry-run] 未写入")
        return 0

    bak = src.replace(".segments.json", ".segments.old.json")
    if not os.path.exists(bak):
        import shutil
        shutil.copy2(src, bak)
        print(f"\n原文件已备份: {os.path.basename(bak)}")

    json.dump(new_data, open(src, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    write_txt_srt(tdir, name, merged)
    print("已写入 segments.json / txt / srt")
    return 0



def resplit_file(name, quiet=True):
    """
    重新切分某份材料，供程序内部调用（不打印）。

    用途：算法改进后，让已有材料也能用上新切分 —— 不必重新跑 Whisper。
    会先备份原来的 segments.json 到 .old.json。

    返回 {"ok": bool, "before": n, "after": m}
    """
    import contextlib
    tdir = os.path.join(core.TRANSCRIPT_DIR, name)
    src = os.path.join(tdir, name + ".segments.json")
    if not os.path.exists(src):
        return {"ok": False, "reason": "找不到文字稿"}

    # 先统计原始句数
    try:
        before = len(json.load(open(src, encoding="utf-8"))["segments"])
    except Exception:
        before = 0

    # 备份（只在第一次备份，保留最原始的版本）
    bak = src.replace(".segments.json", ".segments.old.json")
    if not os.path.exists(bak):
        try:
            import shutil
            shutil.copy2(src, bak)
        except Exception:
            pass

    # 跑到一个丢弃输出的流上，避免污染 GUI 的 stdout
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = resplit(name, dry_run=False)
    except Exception as e:
        return {"ok": False, "reason": str(e), "detail": buf.getvalue()[-500:]}

    if rc != 0:
        return {"ok": False, "reason": "切分失败", "detail": buf.getvalue()[-500:]}

    try:
        after = len(json.load(open(src, encoding="utf-8"))["segments"])
    except Exception:
        after = 0

    return {"ok": True, "before": before, "after": after, "output": buf.getvalue()}


def _topic_break(a, b):
    """
    判断两句之间是不是「话题断了」—— 断了就不该合并成一句。

    只能靠表面线索判断（没有说话人信息），用这几条：

      1. 时间间隔太远        → 中间有明显停顿，多半换人了
      2. 下一句是明显的应答语 → "Okay." / "Yeah, absolutely." / "Right?"
      3. 下一句以呼语开头     → "Hello, Anna." / "Thanks, Neil."
      4. 下一句是问句而本句已结束 → 一问一答，不该粘一起

    宁可保守（少合一点），也不要合出两句不相干的话。
    """
    # 1. 时间间隔
    gap = b["start"] - a["end"]
    if gap > 1.2:
        return True

    bt = b["text"].strip()
    words = bt.split()
    if not words:
        return True

    first = words[0].lower().strip(",.")
    low = bt.lower()

    # 2. 应答语开头
    REPLY_STARTS = {
        "okay", "ok", "yeah", "yep", "yes", "no", "nope", "right", "sure",
        "exactly", "absolutely", "definitely", "totally", "wow", "oh",
        "hmm", "well", "thanks", "thank", "sorry", "please", "hello",
        "hi", "hey", "bye", "goodbye", "cool", "nice", "great", "wow,",
    }
    if first in REPLY_STARTS:
        return True

    # 3. 很短的应答（≤3 词）单独看
    if len(words) <= 3:
        return True

    # 4. 明显的呼语："Hello, Anna." / "Thanks, Neil."
    if re.match(r"^(hello|hi|hey|thanks|thank you|sorry|bye|goodbye)\b",
                low):
        return True

    # 5. 结尾有"? "且下一句是陈述 → 一问一答
    #    （但要排除本句是 "..., right?" 这种反问尾巴）
    if bt.endswith("?") and len(words) <= 8:
        return True

    return False


def _split_by_clause(seg, max_words):
    """
    没有换气点可用时的退路：优先在从句起点切，其次在标点处切。

    从句起点如 "where / which / that / and / but / because"，
    在这些词前面断开，两边都是语法完整的片段。
    """
    text = seg["text"]
    if len(text.split()) <= max_words:
        return [seg]

    # 切出来的每一半至少要这么多词，否则就是不值得的碎片
    MIN_SIDE = max(5, max_words // 4)

    # ---- 1. 从句起点（最自然的切点）----
    #
    # 在从句引导词【之前】断开，前半是完整的主句/从句，后半也自成一体：
    #   "…English learners,"           ← 主句
    #   "where we have natural …"       ← 从句
    # 比切在 where 之后自然得多。
    clause_re = re.compile(
        r"\s+(?=(?:where|which|that|who|whom|whose|when|while|because|"
        r"though|although|if|so|and|but|or|as|since|after|before|"
        r"then|also|plus|even)\b)", re.I)

    # ---- 2. 标点处（次选）----
    punct_re = re.compile(r"[,;:]\s+")

    def best_cut(cands):
        """
        在候选切点里挑一个。

        硬性约束：切点左边那个词不能是「悬空词」。
          冠词 / 介词 / 助动词 / 连词 后面断开，语义就断了：
            "But on top of"       |  "that, even better, …"    ← 错
            "she'll call me over" |  "and show me …"           ← 可以（over 是副词）

        选法：优先保证「切完两半都不超过 max_words」，在这个前提下
              选前半最长的（块大一点、碎片少一点）。
              如果所有候选都会留下超长的一半，就选让超长部分最小的那个，
              反正后面递归还会继续切。
        """
        usable = []
        for cp in cands:
            left = text[:cp].strip()
            right = text[cp:].strip()
            left_w = len(left.split())
            right_w = len(right.split())

            # 两边都不能太短 —— 否则切出来的是「孤儿碎片」。
            # 例如 "…it's been a terrible summer" | "so far really."
            # 右边只有 3 词，属于残句，宁可不切。
            if left_w < MIN_SIDE or right_w < MIN_SIDE:
                continue

            # 左侧不能以悬空词结尾
            last = left.split()[-1].lower().strip(",;:") if left.split() else ""
            if last in DANGLE_TAIL:
                continue

            usable.append((left_w, right_w, cp))

        if not usable:
            return None

        # 第一优先：两半都不超限
        fit = [u for u in usable if u[0] <= max_words and u[1] <= max_words]
        if fit:
            # 都合规时选前半最长的
            fit.sort(reverse=True)
            return fit[0][2]

        # 没有理想切点 → 选「超出的总量」最小的
        usable.sort(key=lambda u: max(0, u[0] - max_words)
                    + max(0, u[1] - max_words))
        return usable[0][2]

    # 先试标点边界（逗号/分号/冒号）——说话人真实的停顿，最自然
    cut_at = best_cut([m.end() for m in punct_re.finditer(text)])

    # 标点都不合适，再看连词/从句引导词
    if cut_at is None:
        cut_at = best_cut([m.end() for m in clause_re.finditer(text)])

    # ---- 3. 找不到合适的切点 → 宁可保持原样 ----
    #
    # 以前这里会「按词数硬切」，结果切出 "so far really." 这种孤儿碎片，
    # 比一句长一点糟糕得多。用户明确说过：
    #   「可以接受一个句子长度在 30 词以下，
    #     只要是一个语意强连贯的句子，极力避免极短碎片」
    # 所以没有自然切点时，就让它长着 —— 长句顶多听着累，
    # 碎片会让整句意思不完整。
    if cut_at is None:
        return [seg]

    # ---- 4. 切成两半，后半递归处理 ----
    left = text[:cut_at].strip()
    right = text[cut_at:].strip()
    tot_len = len(left) + len(right) or 1
    span = seg["end"] - seg["start"]
    mid = seg["start"] + span * (len(left) / tot_len)

    out = [{"text": left, "start": round(seg["start"], 3),
            "end": round(mid, 3)}]
    out.extend(_split_by_clause(
        {"text": right, "start": mid, "end": seg["end"]}, max_words))
    return out


def split_long(text, max_words):
    """（保留给旧调用）把长句按标点切成若干块"""
    if len(text.split()) <= max_words:
        return [text]

    parts = re.split(r'(?<=[,;—])\s+', text)
    if len(parts) <= 1:
        words = text.split()
        return [" ".join(words[i:i + max_words])
                for i in range(0, len(words), max_words)]

    out, cur = [], ""
    for p in parts:
        cand = (cur + " " + p).strip() if cur else p
        if len(cand.split()) <= max_words:
            cur = cand
        else:
            if cur:
                out.append(cur)
            cur = p
    if cur:
        out.append(cur)
    return out


def split_sentences_global(text):
    """
    在整篇文本上切句，返回 [(句子, 起始字符位置, 结束字符位置)]
    """
    if not text.strip():
        return []

    # 找出所有可能的句子边界位置
    bounds = [0]
    for m in SENT_END.finditer(text):
        pos = m.start()
        if pos > 0:
            bounds.append(pos)
    bounds.append(len(text))

    # 去掉被误切的缩写点（如 "Mr. Smith"）
    cleaned = [bounds[0]]
    for b in bounds[1:]:
        if b >= len(text):
            cleaned.append(b)
            continue
        # 看这个点前面一个单词是不是缩写
        left = text[:b].rstrip()
        word = re.split(r"[\s(]", left)[-1].strip(".")
        if word in ABBREV:
            continue
        cleaned.append(b)

    # 组装句子
    result = []
    for i in range(len(cleaned) - 1):
        cs, ce = cleaned[i], cleaned[i + 1]
        seg = text[cs:ce].strip()
        if seg:
            # 重新计算去空白后的实际位置
            lead = len(text[cs:ce]) - len(text[cs:ce].lstrip())
            result.append((seg, cs + lead, cs + lead + len(seg)))
    return result



def write_txt_srt(tdir, name, segs):
    with open(os.path.join(tdir, name + ".txt"), "w", encoding="utf-8") as fp:
        for s in segs:
            fp.write(f"{s['id'] + 1:3d}. {s['text']}\n")

    def ts(sec, sep=","):
        h = int(sec // 3600); m = int(sec % 3600 // 60); s2 = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        if ms == 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{s2:02d}{sep}{ms:03d}"

    with open(os.path.join(tdir, name + ".srt"), "w", encoding="utf-8") as fp:
        for s in segs:
            fp.write(f"{s['id'] + 1}\n{ts(s['start'])} --> {ts(s['end'])}\n"
                     f"{s['text']}\n\n")


if __name__ == "__main__":
    _fix_console()          # 只有命令行直接跑时才动 stdout
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="材料名")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(resplit(args.name, args.dry_run))
