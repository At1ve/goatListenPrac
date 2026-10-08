# -*- coding: utf-8 -*-
"""
按句子边界重新切分 Whisper 的分段。

问题：Whisper 的 VAD 分段是按静音/时长切的，经常把两个句子塞进一段，
      或者把一个句子从中间切断（比如 "...get access to our" | "weekly classes"）。

做法：
  1. 把整篇文本按句子边界（. ? ! 后跟空格+大写）切开
  2. 时间戳按字符数比例分配到每个句子
  3. 过短的碎片向后合并，保证每段是完整句子

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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core


# 句末标点（后面跟空格或引号再跟大写字母，才算句子边界）
SENT_END = re.compile(r'(?<=[.?!])(?=[\s"\']+[A-Z"\'])')
# 常见缩写，不该在这里断开
ABBREV = {"Mr", "Mrs", "Ms", "Dr", "Prof", "St", "Jr", "Sr", "vs", "etc",
          "e.g", "i.e", "U.S", "U.K", "a.m", "p.m", "No", "Inc", "Ltd"}


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

    # ---- 2. 在整篇上切句 ----
    sents = split_sentences_global(text)

    new_segs = []
    for t, cs, ce in sents:
        st = time_at(cs)
        en = time_at(max(cs, ce - 1))
        new_segs.append({"text": t.strip(), "start": round(st, 3),
                         "end": round(en, 3)})

    # ---- 3. 合并过短 / 拆分过长 ----
    MIN_DUR, MIN_WORDS = 1.0, 3
    MAX_DUR, MAX_WORDS = 11.0, 26

    merged = []
    for s in new_segs:
        if merged:
            p = merged[-1]
            pdur = p["end"] - p["start"]
            pw = len(p["text"].split())
            dur = s["end"] - s["start"]
            w = len(s["text"].split())
            # 当前太短 或 上一句是残句 → 合并
            prev_incomplete = not re.search(r"[.?!]\s*$", p["text"].strip())
            if ((dur < MIN_DUR or w < MIN_WORDS or prev_incomplete)
                    and pw < MAX_WORDS and pdur < MAX_DUR):
                p["text"] = p["text"].rstrip() + " " + s["text"].lstrip()
                p["end"] = s["end"]
                continue
        merged.append(dict(s))

    # 拆过长
    final = []
    for s in merged:
        if ((s["end"] - s["start"]) <= MAX_DUR
                and len(s["text"].split()) <= MAX_WORDS):
            final.append(s)
            continue
        chunks = split_long(s["text"], MAX_WORDS)
        if len(chunks) <= 1:
            final.append(s)
            continue
        tot = sum(len(c) for c in chunks) or 1
        span = s["end"] - s["start"]
        cur = s["start"]
        for i, c in enumerate(chunks):
            e = s["end"] if i == len(chunks) - 1 else cur + span * (len(c) / tot)
            final.append({"text": c, "start": round(cur, 3), "end": round(e, 3)})
            cur = e

    merged = final

    # ---- 4. 时间单调性 + 编号 ----
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



def split_long(text, max_words):
    """
    把一个过长的句子按逗号/分号切成若干块，每块不超过 max_words 词。
    尽量在语义自然处切。
    """
    if len(text.split()) <= max_words:
        return [text]

    # 先按逗号、分号、破折号切
    parts = re.split(r'(?<=[,;—])\s+', text)
    if len(parts) <= 1:
        # 没有可切的位置，按词数硬切
        words = text.split()
        return [" ".join(words[i:i + max_words])
                for i in range(0, len(words), max_words)]

    # 贪心合并小片段
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
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="材料名")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(resplit(args.name, args.dry_run))
