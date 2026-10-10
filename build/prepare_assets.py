# -*- coding: utf-8 -*-
"""
准备内置资源（语音模型 + 本地词典）。

这些资源不打进源码仓库（太大），但需要在本机准备好，
build_exe.py 会把它们放进安装包。

用法:
    python build/prepare_assets.py

产出:
    build_assets/
      models/faster-whisper-small/   语音识别模型（约 464MB）
      dict/dict.json                 本地词典（离线查词用）
"""
import csv
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "build_assets")


def log(m):
    print(m, flush=True)


# ============================================================
#  1. Whisper 模型
# ============================================================

def prep_model():
    log("\n[1/2] 语音识别模型")
    dst = os.path.join(ASSETS, "models", "faster-whisper-small")

    if os.path.exists(os.path.join(dst, "model.bin")):
        sz = os.path.getsize(os.path.join(dst, "model.bin")) / 1024 / 1024
        log("  已就绪: %.1f MB" % sz)
        return True

    # 从 HuggingFace 缓存找
    hf = os.path.expanduser(r"~\.cache\huggingface\hub")
    snap_root = os.path.join(hf, "models--Systran--faster-whisper-small",
                             "snapshots")
    if os.path.isdir(snap_root):
        snaps = os.listdir(snap_root)
        if snaps:
            snap = os.path.join(snap_root, snaps[0])
            need = ["config.json", "model.bin", "tokenizer.json",
                    "vocabulary.txt"]
            if all(os.path.exists(os.path.join(snap, f)) for f in need):
                os.makedirs(dst, exist_ok=True)
                for f in need:
                    src = os.path.join(snap, f)
                    tgt = os.path.join(dst, f)
                    try:
                        os.link(src, tgt)      # 硬链接，省时间和空间
                    except Exception:
                        shutil.copy2(src, tgt)
                sz = sum(os.path.getsize(os.path.join(dst, f))
                         for f in os.listdir(dst))
                log("  从缓存复制完成: %.1f MB" % (sz / 1024 / 1024))
                return True

    # 缓存里没有 → 触发一次下载
    log("  缓存里没有模型，正在下载（约 464MB，需要联网一次）…")
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel("small", device="cpu", compute_type="int8")
        del m
    except Exception as e:
        log("  下载失败: " + str(e))
        return False

    return prep_model()      # 递归一次，这次缓存里就有了


# ============================================================
#  2. 本地词典
# ============================================================

# 词书来源：kajweb/dict（分级词书，含中文释义、音标、例句）
BOOK_BASE = "https://cdn.jsdelivr.net/gh/kajweb/dict@master/book/"
BOOKS_WANT = [
    "CET4_", "CET6_", "IELTS_", "TOEFL_", "KaoYan_", "Level4_", "Level8_",
    "GMAT_", "GRE_", "SAT_", "PEPGaoZhong_", "ChuZhong_", "BEC_",
]


def _list_books():
    url = "https://api.github.com/repos/kajweb/dict/contents/book"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return [x["name"] for x in json.loads(r.read().decode())]


def _fetch(url, dst):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dst, "wb") as f:
        f.write(r.read())


def prep_dict():
    """
    构建本地词典（断网查词用）。

    词书是 JSONL，结构：
      {headWord, content:{word:{content:{
          trans:[{pos, tranCn}], sentence:{sentences:[{sContent, sCn}]},
          usphone}}}}
    """
    log("\n[2/2] 本地词典")
    out = os.path.join(ASSETS, "dict", "dict.json")
    if os.path.exists(out):
        log("  已就绪: %.1f MB" % (os.path.getsize(out) / 1024 / 1024))
        return True

    tmp = os.path.join(ASSETS, "_tmp_dict")
    os.makedirs(tmp, exist_ok=True)

    try:
        books = _list_books()
    except Exception as e:
        log("  取词书列表失败: " + str(e)[:80])
        return False

    picked = []
    for w in BOOKS_WANT:
        cands = []
        for b in books:
            parts = b.split("_", 1)
            stem = parts[1] if parts[0].isdigit() else b
            if stem.startswith(w) and "luan" not in stem:
                cands.append(b)
        if cands:
            picked.append(sorted(cands)[0])
    picked = sorted(set(picked))
    log("  选中 %d 本词书" % len(picked))

    words = {}
    for i, name in enumerate(picked, 1):
        dst = os.path.join(tmp, name)
        if not (os.path.exists(dst) and os.path.getsize(dst) > 1000):
            try:
                _fetch(BOOK_BASE + urllib.parse.quote(name), dst)
            except Exception as e:
                log("    %-28s 下载失败 %s" % (name[:28], str(e)[:30]))
                continue
        try:
            with zipfile.ZipFile(dst) as z:
                js = [n for n in z.namelist() if n.endswith(".json")]
                raw = z.read(js[0]).decode("utf-8", "replace")
        except Exception as e:
            log("    %-28s 解压失败 %s" % (name[:28], str(e)[:30]))
            continue

        n = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue

            hw = (o.get("headWord") or "").strip().lower()
            if not hw or not hw.replace("-", "").replace(" ", "").isalpha():
                continue
            if hw in words:
                continue
            try:
                inner = o["content"]["word"].get("content") or {}
            except Exception:
                continue

            cn = []
            for t in (inner.get("trans") or []):
                if not isinstance(t, dict):
                    continue
                v = (t.get("tranCn") or "").strip()
                pos = (t.get("pos") or "").strip()
                if v:
                    cn.append((pos + " " + v).strip() if pos else v)
            if not cn:
                continue

            ex = []
            try:
                for s in (inner["sentence"]["sentences"] or [])[:2]:
                    en = (s.get("sContent") or "").strip()
                    zh = (s.get("sCn") or "").strip()
                    if en:
                        ex.append(en + (("|" + zh) if zh else ""))
            except Exception:
                pass

            words[hw] = {
                "t": "；".join(cn)[:200],
                "p": (inner.get("usphone")
                      or inner.get("ukphone") or "").strip()[:40],
                "e": ex,
            }
            n += 1
        log("    [%d/%d] %-28s +%5d (累计 %d)"
            % (i, len(picked), name[:28], n, len(words)))

    if not words:
        log("  没取到任何词条")
        return False

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, separators=(",", ":"))
    log("  收录 %d 词条，写出 %.1f MB"
        % (len(words), os.path.getsize(out) / 1024 / 1024))
    shutil.rmtree(tmp, ignore_errors=True)
    return True


def main():
    log("=" * 58)
    log("  准备内置资源")
    log("=" * 58)
    os.makedirs(ASSETS, exist_ok=True)

    a = prep_model()
    b = prep_dict()

    log("\n" + "=" * 58)
    if a and b:
        log("  全部就绪，可以运行: python build/build_exe.py")
    else:
        log("  部分资源缺失（见上）")
        log("  缺模型 → 用户首次转写需联网")
        log("  缺词典 → 断网时无法查词")
    log("=" * 58)
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(main())
