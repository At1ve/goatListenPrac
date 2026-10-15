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
    #   正确做法：
    # 合并策略：
    #   · 上句没收尾 → 【必须】接上，不受长度限制
    #     （"…for every cow or sheep" + "taken off the land…" 是一句话，
    #      哪怕接完 35 词也得接 —— 断在中间比长一点糟糕得多）
    #   · 只是太短 → 有长度限制地合并，避免把两句并成一句
    MIN_DUR, MIN_WORDS = 1.2, 4
    # 可接受的长度上限：用户明确说过「30 词以内都行，只要语义连贯」
    MAX_DUR, MAX_WORDS = 14.0, 30
    # 合并的硬上限：先把句子接完整，之后第 4 步再统一按从句切开。
    # 设得比 MAX 宽，是因为「接完整再切」远比「留在半句上」重要 ——
    # 切在从句处读起来自然，切在半句上一定别扭。
    HARD_WORDS, HARD_DUR = 45, 26.0
    # 接完整之后还能容忍的长度。超过这个才真的去切。
    # 比 MAX 宽是有意的：接完整的长句宁可略长，也不要再切开。
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

            must_merge = unfinished(p["text"])
            too_short = (dur < MIN_DUR or w < MIN_WORDS)

            if must_merge:
                # 上句没说完 → 一定要接上（除非会拼出离谱的长句）
                if (pw + w <= HARD_WORDS
                        and pdur + dur <= HARD_DUR):
                    p["text"] = p["text"].rstrip() + " " + s["text"].lstrip()
                    p["end"] = s["end"]
                    p["just_merged"] = True
                    continue
            elif too_short and not complete(s["text"]):
                # 太短的句子要并进上一句 —— 但**仅限它自己不是完整句**时。
                # 像 "I'm Neil." / "Yeah, absolutely." 这种短句
                # 本来就是完整的一句话，并进上一句反而破坏了原意。
                if (pw + w <= MAX_WORDS and pdur + dur <= MAX_DUR):
                    p["text"] = p["text"].rstrip() + " " + s["text"].lstrip()
                    p["end"] = s["end"]
                    continue

        merged.append(dict(s))

    # ---- 4. 过长的句子：切在从句/标点处 ----
    #
    # 重要：这里只处理「本来就长」的句子。
    # 刚刚为了接完整而合并出来的句子（just_merged）不再切 ——
    # 否则等于白合并：
    #   "…not the problem means that if you are not actively trying
    #    to fix a problem,"                     ← 24 词，切了
    #   "you are helping it continue."           ← 5 词碎片
    # 用户明确说过 30 词以内都能接受，只要能保持语义连贯。
    final = []
    for s in merged:
        dur = s["end"] - s["start"]
        words = len(s["text"].split())

        # 完整句子放宽到 KEEP_WORDS，超出才切
        limit_words = KEEP_WORDS if s.get("just_merged") else MAX_WORDS
        limit_dur = KEEP_DUR if s.get("just_merged") else MAX_DUR

        if words <= limit_words and dur <= limit_dur:
            final.append({"text": s["text"], "start": s["start"],
                          "end": s["end"]})
            continue

        chunks = _split_by_clause(s, MAX_WORDS)
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
