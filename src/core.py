# -*- coding: utf-8 -*-
"""
精听工具 - 核心逻辑
材料扫描、标记存储、制卡、Anki 导入
"""
import os
import io
import re
import sys
import json
import shutil
import zipfile
import urllib.request
import urllib.parse
import subprocess
from datetime import datetime


def _resolve_root():
    """
    确定"工作目录"（用户数据放这里）。

    打包成 exe 后，__file__ 指向 PyInstaller 解压的临时目录，
    数据写在那里会在退出时被清掉。所以：

      · 打包运行（frozen）→ 用 exe 所在目录
      · 源码运行           → 用项目根目录（src 的上一级）
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_web():
    """界面文件的位置（打包后在 _MEIPASS 里）"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "web")
    return os.path.join(ROOT, "web")


ROOT = _resolve_root()
WEB_DIR = _resolve_web()

TRANSCRIPT_DIR = os.path.join(ROOT, "transcript")
CARDS_DIR = os.path.join(ROOT, "cards")
MARKS_DIR = os.path.join(ROOT, "marks")
MEDIA_DIR_MP3S = os.path.join(ROOT, "mp3s")
TRANS_DIR = os.path.join(ROOT, "trans_cache")

MODEL_NAME = "听力音频卡"
DECK_NAME = "英语听力::02-音频卡片"



def _find_ffmpeg():
    """定位 ffmpeg：优先项目内，其次 PATH"""
    local = os.path.join(ROOT, "ffmpeg", "bin",
                         "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if os.path.exists(local):
        return local
    import shutil as _sh
    return _sh.which("ffmpeg") or local


FFMPEG = _find_ffmpeg()


# ---------------------------------------------------------------- Anki 定位
# Anki 的数据目录可能因用户配置而不同，启动时自动探测。
ANKI_BASE = os.environ.get("ANKI_BASE") or os.path.join(ROOT, "data")
_collection_cache = {"path": None, "media": None}


def _locate_collection():
    """
    找到 collection.anki2 所在位置。
    支持：
      1. 环境变量 ANKI_COLLECTION 直接指定
      2. <ANKI_BASE>/<profile>/collection.anki2
      3. 系统默认 Anki 数据目录 %APPDATA%\\Anki2\\<profile>\\collection.anki2
    """
    if _collection_cache["path"]:
        return _collection_cache["path"], _collection_cache["media"]

    # 1) 显式指定
    env = os.environ.get("ANKI_COLLECTION")
    if env and os.path.exists(env):
        d = os.path.dirname(env)
        _collection_cache.update({"path": env, "media": os.path.join(d, "collection.media")})
        return _collection_cache["path"], _collection_cache["media"]

    # 2) 项目内 data/
    for base in (ANKI_BASE,
                 os.path.join(os.environ.get("APPDATA", ""), "Anki2")):
        if not base or not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if not os.path.isdir(p) or name == "addons21":
                continue
            col = os.path.join(p, "collection.anki2")
            if os.path.exists(col):
                _collection_cache.update(
                    {"path": col, "media": os.path.join(p, "collection.media")})
                return _collection_cache["path"], _collection_cache["media"]

    # 3) 兜底：ANKI_BASE 下第一个 profile
    if os.path.isdir(ANKI_BASE):
        for name in sorted(os.listdir(ANKI_BASE)):
            p = os.path.join(ANKI_BASE, name)
            if os.path.isdir(p) and name != "addons21":
                col = os.path.join(p, "collection.anki2")
                _collection_cache.update(
                    {"path": col, "media": os.path.join(p, "collection.media")})
                return _collection_cache["path"], _collection_cache["media"]

    fallback = os.path.join(ANKI_BASE, "collection.anki2")
    _collection_cache.update(
        {"path": fallback, "media": os.path.join(ANKI_BASE, "collection.media")})
    return _collection_cache["path"], _collection_cache["media"]


def get_collection_path():
    return _locate_collection()[0]


def get_anki_media_dir():
    return _locate_collection()[1]


# 标记类型定义
MARK_TYPES = {
    "w": {
        "label": "生词",
        "color": "#e5484d",
        "icon": "W",
        "fields": ["word", "meaning"],
        "prompts": ["哪个词不认识？", "中文意思？"],
    },
    "p": {
        "label": "短语",
        "color": "#f76b15",
        "icon": "P",
        "fields": ["phrase", "meaning"],
        "prompts": ["哪个短语/搭配？", "中文意思？"],
    },
    "s": {
        "label": "句型",
        "color": "#3e63dd",
        "icon": "S",
        "fields": ["structure"],
        "prompts": ["什么结构没反应过来？"],
    },
    "l": {
        "label": "连读",
        "color": "#8e4ec6",
        "icon": "L",
        "fields": ["misheard"],
        "prompts": ["听成了什么？（原音 -> 误听）"],
    },
    "x": {
        "label": "整句",
        "color": "#6b7280",
        "icon": "X",
        "fields": ["note"],
        "prompts": ["备注"],
    },
}


def ensure_dirs():
    for d in (TRANSCRIPT_DIR, CARDS_DIR, MARKS_DIR):
        os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------- 界面设置
# 不依赖浏览器 localStorage（WebView2 下可能被禁用 / 不持久），
# 改为存在项目目录里的 JSON 文件，用户也能直接查看和修改。

STATE_FILE = os.path.join(ROOT, "ui_state.json")


def load_ui_state():
    """读取界面状态（主题、播放位置、各类开关…）"""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


def save_ui_state(state):
    """整体保存界面状态"""
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(state or {}, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def get_state(key, default=None):
    """取一个键"""
    return load_ui_state().get(key, default)


def set_state(key, value):
    """设置一个键（局部更新，避免覆盖其他字段）"""
    st = load_ui_state()
    st[key] = value
    return save_ui_state(st)

# ---------------------------------------------------------------- 材料

def find_audio(name):
    """按材料名找音频文件"""
    # 1) 常见位置直接命中
    for d in (MEDIA_DIR_MP3S, ROOT):
        for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg"):
            p = os.path.join(d, name + ext)
            if os.path.exists(p):
                return p
    # 2) 有限深度递归（跳过程序/数据目录）
    skip = {"anki", "ffmpeg", "data", "cards", "web", "transcript", "marks", "__pycache__"}
    for base in (MEDIA_DIR_MP3S, ROOT):
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for f in files:
                if os.path.splitext(f)[0] == name and f.lower().endswith(
                        (".mp3", ".wav", ".m4a", ".flac", ".ogg")):
                    return os.path.join(root, f)
    return None


def list_materials():
    """列出所有已转写的材料"""
    ensure_dirs()
    out = []
    if not os.path.isdir(TRANSCRIPT_DIR):
        return out
    for name in sorted(os.listdir(TRANSCRIPT_DIR)):
        d = os.path.join(TRANSCRIPT_DIR, name)
        if not os.path.isdir(d):
            continue
        seg = os.path.join(d, name + ".segments.json")
        if not os.path.exists(seg):
            continue
        try:
            with open(seg, encoding="utf-8") as fp:
                data = json.load(fp)
            n = len(data["segments"])
            dur = data["duration"]
        except Exception:
            continue
        out.append({
            "name": name,
            "segments": n,
            "duration": dur,
            "duration_text": f"{int(dur // 60)}:{int(dur % 60):02d}",
            "has_audio": find_audio(name) is not None,
            "marked": len(load_marks(name)["marks"]),
        })
    return out


def load_segments(name):
    """读取某材料的句子列表"""
    p = os.path.join(TRANSCRIPT_DIR, name, name + ".segments.json")
    if not os.path.exists(p):
        raise FileNotFoundError(f"找不到 {name} 的转写数据")
    with open(p, encoding="utf-8") as fp:
        data = json.load(fp)
    segs = []
    for s in data["segments"]:
        segs.append({
            "id": s["id"] + 1,          # 对外用 1 起始编号
            "start": s["start"],
            "end": s["end"],
            "text": s["text"],
        })
    return {"name": name, "duration": data["duration"], "segments": segs}


# ---------------------------------------------------------------- 转写

_whisper_model = None
_model_lock = None


def _get_model(model_size="small"):
    """惰性加载并缓存 Whisper 模型"""
    global _whisper_model, _model_lock
    import threading
    if _model_lock is None:
        _model_lock = threading.Lock()
    with _model_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _whisper_model


def fmt_ts(sec, sep=","):
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def transcribe_file(audio_path, model_size="small", lang="en", progress=None):
    """
    转写单个音频，产出 transcript/<材料名>/ 下的三个文件。
    progress: 可选回调 progress(stage, done, total, message)
    """
    def rep(stage, done, total, msg=""):
        if progress:
            try:
                progress(stage, done, total, msg)
            except Exception:
                pass

    name = os.path.splitext(os.path.basename(audio_path))[0]
    outdir = os.path.join(TRANSCRIPT_DIR, name)
    os.makedirs(outdir, exist_ok=True)

    rep("model", 0, 1, "加载语音识别模型（首次约需 1 分钟）…")
    model = _get_model(model_size)

    rep("decode", 0, 1, "正在识别音频…")
    segments, info = model.transcribe(
        audio_path,
        language=lang,
        beam_size=1,              # 1=贪心解码，比 beam_size=5 快约 2-3 倍，
                                  # 对清晰人声的准确率损失很小
        condition_on_previous_text=False,   # 避免长音频里的错误累积传播
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    total_dur = info.duration or 1.0
    segs = []
    for s in segments:
        segs.append({
            "id": len(segs),
            "start": round(s.start, 3),
            "end": round(s.end, 3),
            "text": s.text.strip(),
        })
        pct = min(0.99, s.end / total_dur)
        rep("decode", int(pct * 100), 100,
            f"已识别 {len(segs)} 句 · {int(s.end // 60)}:{int(s.end % 60):02d} / "
            f"{int(total_dur // 60)}:{int(total_dur % 60):02d}")

    rep("save", 0, 1, "写入文字稿…")

    # 1) 带编号文字稿
    with open(os.path.join(outdir, name + ".txt"), "w", encoding="utf-8") as fp:
        for s in segs:
            fp.write(f"{s['id'] + 1:3d}. {s['text']}\n")

    # 2) SRT
    with open(os.path.join(outdir, name + ".srt"), "w", encoding="utf-8") as fp:
        for s in segs:
            fp.write(f"{s['id'] + 1}\n{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}\n"
                     f"{s['text']}\n\n")

    # 3) 分段 JSON
    with open(os.path.join(outdir, name + ".segments.json"), "w", encoding="utf-8") as fp:
        json.dump({"file": os.path.abspath(audio_path), "duration": info.duration,
                   "language": info.language, "segments": segs},
                  fp, ensure_ascii=False, indent=2)

    # 4) 按句子边界重新切分（Whisper 的 VAD 分段会把句子切断/粘合）
    try:
        import resplit
        rep("save", 0, 1, "按句子边界整理…")
        resplit.resplit(name)
    except Exception as e:
        print("句子重切失败（保留原始分段）:", e)

    rep("done", 1, 1, f"完成，共 {len(segs)} 句")
    return {"ok": True, "name": name, "segments": len(segs),
            "duration": info.duration, "dir": outdir}


def import_material(src_path, progress=None, model_size="small"):
    """
    导入一份新材料：复制音频到 mp3s/ -> 转写 -> 返回材料名
    progress 回调同 transcribe_file
    """
    def rep(stage, done, total, msg=""):
        if progress:
            try:
                progress(stage, done, total, msg)
            except Exception:
                pass

    if not os.path.exists(src_path):
        return {"ok": False, "reason": f"找不到文件：{src_path}"}

    name = os.path.splitext(os.path.basename(src_path))[0]
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".mp4", ".mkv"):
        return {"ok": False, "reason": f"不支持的格式：{ext}"}

    ensure_dirs()
    os.makedirs(MEDIA_DIR_MP3S, exist_ok=True)
    dest = os.path.join(MEDIA_DIR_MP3S, name + ext)

    # 已在 mp3s 里就不用复制
    if os.path.abspath(src_path) != os.path.abspath(dest):
        rep("copy", 0, 1, "复制音频到 mp3s…")
        shutil.copy2(src_path, dest)

    rep("copy", 1, 1, "音频已就位")
    res = transcribe_file(dest, model_size=model_size, progress=progress)
    if res.get("ok"):
        res["audio"] = dest
    return res


# ---------------------------------------------------------------- 翻译

_trans_cache = None


def _load_trans_cache():
    global _trans_cache
    if _trans_cache is None:
        ensure_dirs()
        os.makedirs(TRANS_DIR, exist_ok=True)
        p = os.path.join(TRANS_DIR, "cache.json")
        if os.path.exists(p):
            try:
                _trans_cache = json.load(open(p, encoding="utf-8"))
            except Exception:
                _trans_cache = {}
        else:
            _trans_cache = {}
    return _trans_cache


def _save_trans_cache():
    if _trans_cache is None:
        return
    p = os.path.join(TRANS_DIR, "cache.json")
    try:
        json.dump(_trans_cache, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass


def translate(text, target="zh-CN", use_cache=True):
    """
    英译中。优先 Google 免费接口，失败则用 MyMemory。
    结果本地缓存，不重复请求。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "内容为空"}

    cache = _load_trans_cache()
    key = target + "|" + text
    if use_cache and key in cache:
        return {"ok": True, "text": cache[key], "cached": True}

    # ---- Google 免费接口 ----
    try:
        url = ("https://translate.googleapis.com/translate_a/single"
               "?client=gtx&sl=en&tl=" + urllib.parse.quote(target)
               + "&dt=t&q=" + urllib.parse.quote(text))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8"))
        out = "".join(x[0] for x in data[0] if x and x[0])
        if out.strip():
            cache[key] = out
            _save_trans_cache()
            return {"ok": True, "text": out, "source": "google"}
    except Exception as e:
        last_err = str(e)

    # ---- MyMemory 兜底 ----
    try:
        url = ("https://api.mymemory.translated.net/get?q="
               + urllib.parse.quote(text[:480]) + "&langpair=en|" + target)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode("utf-8"))
        out = (d.get("responseData") or {}).get("translatedText", "")
        if out and out.strip():
            cache[key] = out
            _save_trans_cache()
            return {"ok": True, "text": out, "source": "mymemory"}
    except Exception as e:
        last_err = str(e)

    return {"ok": False, "reason": f"翻译失败：{last_err}"}


def translate_many(texts, target="zh-CN"):
    """批量翻译（逐条，带缓存）"""
    return [translate(t, target) for t in texts]


def lookup_word(word):
    """查单词：中文释义（有道）+ 音标例句（dictionaryapi）"""
    word = (word or "").strip()
    if not word:
        return {"ok": False, "reason": "空"}
    r = fetch_examples(word)
    if r.get("ok"):
        return r
    # 兜底用翻译
    t = translate(word)
    if t.get("ok"):
        return {"ok": True, "word": word, "meanings": [t["text"]],
                "examples": [], "phonetic": "", "source": t.get("source")}
    return {"ok": False, "reason": r.get("reason", "查询失败")}


# ---------------------------------------------------------------- 标记

def marks_path(name):
    ensure_dirs()
    return os.path.join(MARKS_DIR, name + ".json")


def load_marks(name):
    p = marks_path(name)
    if not os.path.exists(p):
        return {"name": name, "marks": {}, "updated": None}
    try:
        with open(p, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {"name": name, "marks": {}, "updated": None}


def save_marks(name, marks):
    p = marks_path(name)
    data = {
        "name": name,
        "marks": marks,           # { "19": {"type":"w","word":"...","meaning":"..."} }
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    with open(p, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    return data


def set_mark(name, seg_id, mtype, fields):
    """新增或更新一条标记；mtype=None 表示删除"""
    data = load_marks(name)
    marks = data["marks"]
    key = str(seg_id)
    if mtype is None:
        marks.pop(key, None)
    else:
        marks[key] = {"type": mtype, "fields": fields or {}}
    save_marks(name, marks)
    return marks


def move_marks(name, mapping):
    """
    批量把标记从旧编号搬到新编号（用于修改句子编号后）。
    mapping: {"旧id": 新id}
    """
    data = load_marks(name)
    marks = data["marks"]
    new = {}
    for k, v in marks.items():
        nk = str(mapping.get(k, k))
        new[nk] = v
    save_marks(name, new)
    return new


# ---------------------------------------------------------------- 材料管理

def material_info(name):
    """汇总一份材料的占用情况，用于删除前提示"""
    info = {"name": name, "audio": None, "audio_mb": 0,
            "transcript": False, "card_clips": 0, "marks": 0, "segments": 0}
    a = find_audio(name)
    if a and os.path.exists(a):
        info["audio"] = a
        info["audio_mb"] = round(os.path.getsize(a) / 1024 / 1024, 1)

    td = os.path.join(TRANSCRIPT_DIR, name)
    if os.path.isdir(td):
        info["transcript"] = True
        seg = os.path.join(td, name + ".segments.json")
        if os.path.exists(seg):
            try:
                info["segments"] = len(
                    json.load(open(seg, encoding="utf-8"))["segments"])
            except Exception:
                pass

    cd = os.path.join(CARDS_DIR, name)
    if os.path.isdir(cd):
        info["card_clips"] = sum(1 for root, _, fs in os.walk(cd)
                                 for f in fs if f.endswith(".mp3"))

    mp = marks_path(name)
    if os.path.exists(mp):
        try:
            info["marks"] = len(
                json.load(open(mp, encoding="utf-8")).get("marks", {}))
        except Exception:
            pass
    return info


def delete_material(name, delete_audio=False):
    """
    删除一份材料。
    delete_audio=False（默认）只删转写/卡片/标记，保留 mp3s 里的原始音频。
    """
    removed = []
    try:
        for label, d in (("转写", os.path.join(TRANSCRIPT_DIR, name)),
                         ("卡片", os.path.join(CARDS_DIR, name))):
            if os.path.isdir(d):
                shutil.rmtree(d)
                removed.append(label)

        mp = marks_path(name)
        if os.path.exists(mp):
            os.remove(mp)
            removed.append("标记")

        if delete_audio:
            a = find_audio(name)
            if a and os.path.exists(a):
                os.remove(a)
                removed.append("音频")

        return {"ok": True, "removed": removed}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "reason": str(e)}


def rename_material(old, new):
    """重命名材料（音频 + 转写 + 卡片 + 标记一起改）"""
    new = (new or "").strip()
    if not new or new == old:
        return {"ok": False, "reason": "名称未改变"}
    if "/" in new or "\\" in new:
        return {"ok": False, "reason": "名称不能含斜杠"}
    if find_audio(new) or os.path.isdir(os.path.join(TRANSCRIPT_DIR, new)):
        return {"ok": False, "reason": f"已存在材料「{new}」"}

    try:
        a = find_audio(old)
        if a and os.path.exists(a):
            ext = os.path.splitext(a)[1]
            shutil.move(a, os.path.join(os.path.dirname(a), new + ext))

        src = os.path.join(TRANSCRIPT_DIR, old)
        if os.path.isdir(src):
            dst = os.path.join(TRANSCRIPT_DIR, new)
            shutil.move(src, dst)
            for fn in os.listdir(dst):
                if fn.startswith(old):
                    os.rename(os.path.join(dst, fn),
                              os.path.join(dst, fn.replace(old, new, 1)))

        src = os.path.join(CARDS_DIR, old)
        if os.path.isdir(src):
            shutil.move(src, os.path.join(CARDS_DIR, new))

        mp = marks_path(old)
        if os.path.exists(mp):
            data = json.load(open(mp, encoding="utf-8"))
            data["name"] = new
            with open(marks_path(new), "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            os.remove(mp)

        return {"ok": True, "name": new}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "reason": str(e)}


# ---------------------------------------------------------------- 例句

def _http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_examples(word, limit=2):
    """拉取释义和例句。依次尝试多个免费源，任一成功即返回。"""
    word = word.strip().lower()
    if not word:
        return {"ok": False, "reason": "没有词"}
    single = " " not in word

    errors = []

    # ---- 源 1: dictionaryapi.dev（有音标+例句）----
    if single:
        try:
            data = _http_json("https://api.dictionaryapi.dev/api/v2/entries/en/"
                              + urllib.parse.quote(word))
            if isinstance(data, list) and data:
                entry = data[0]
                phonetic = entry.get("phonetic") or ""
                if not phonetic:
                    for ph in entry.get("phonetics", []) or []:
                        if ph.get("text"):
                            phonetic = ph["text"]
                            break
                meanings, examples = [], []
                for m in entry.get("meanings", []) or []:
                    pos = m.get("partOfSpeech", "")
                    for d in m.get("definitions", []) or []:
                        if d.get("definition") and len(meanings) < 3:
                            meanings.append(f"({pos}) {d['definition']}")
                        if d.get("example") and len(examples) < limit:
                            examples.append(d["example"])
                    if len(examples) >= limit and len(meanings) >= 2:
                        break
                if meanings or examples:
                    return {"ok": True, "source": "dictionaryapi.dev",
                            "word": entry.get("word", word), "phonetic": phonetic,
                            "meanings": meanings, "examples": examples}
        except Exception as e:
            errors.append(f"dictionaryapi: {e}")

    # ---- 源 2: 有道词典建议接口（给中文释义）----
    try:
        u = ("https://dict.youdao.com/suggest?num=1&doctype=json&q="
             + urllib.parse.quote(word))
        data = _http_json(u)
        entries = (data.get("data") or {}).get("entries") or []
        if entries:
            e = entries[0]
            explain = e.get("explain", "")
            return {"ok": True, "source": "youdao", "word": e.get("entry", word),
                    "phonetic": "", "meanings": [explain] if explain else [],
                    "examples": []}
    except Exception as e:
        errors.append(f"youdao: {e}")

    return {"ok": False, "reason": "；".join(errors) or "未找到释义"}


# ---------------------------------------------------------------- 制卡

def cut_audio(audio, start, end, out_path, pad=0.15):
    """用 ffmpeg 切音频"""
    if not os.path.exists(FFMPEG):
        raise FileNotFoundError("找不到 ffmpeg")
    s = max(0.0, start - pad)
    e = end + pad
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-ss", f"{s:.3f}", "-to", f"{e:.3f}",
        "-i", audio,
        "-ac", "1", "-ar", "22050", "-b:a", "64k",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore")[:300])


def build_cards(name, progress=None):
    """按标记生成卡片"""
    def log(msg):
        if progress:
            progress(msg)

    data = load_segments(name)
    segs = {s["id"]: s for s in data["segments"]}
    marks = load_marks(name)["marks"]

    audio = find_audio(name)
    if not audio:
        return {"ok": False, "reason": f"找不到音频文件（{name}.mp3）"}

    todo = []
    for sid_str, m in sorted(marks.items(), key=lambda kv: int(kv[0])):
        sid = int(sid_str)
        if sid in segs:
            todo.append((sid, m))

    if not todo:
        return {"ok": False, "reason": "还没有任何标记"}

    outdir = os.path.join(CARDS_DIR, name)
    media = os.path.join(outdir, "media")
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(media, exist_ok=True)

    log(f"开始处理 {len(todo)} 个标记…")

    rows = []
    for i, (sid, m) in enumerate(todo, 1):
        seg = segs[sid]
        mtype = m.get("type", "x")
        flds = m.get("fields", {}) or {}
        meta = MARK_TYPES.get(mtype, MARK_TYPES["x"])

        fname = f"seg_{i:04d}.mp3"
        cut_audio(audio, seg["start"], seg["end"], os.path.join(media, fname))

        # 组装：原文（高亮版）和解说（背面内容）分开
        text = seg["text"]
        explain = []

        if mtype == "w":
            word = flds.get("word", "").strip()
            meaning = flds.get("meaning", "").strip()
            if word:
                text = highlight(text, word)
                log(f"  [{i}] 拉取释义: {word}")
                ex = fetch_examples(word)
                if ex.get("ok"):
                    head = f'<div class="word">{html_escape(word)}'
                    if ex.get("phonetic"):
                        head += f' <span class="ph">{html_escape(ex["phonetic"])}</span>'
                    head += "</div>"
                    explain.append(head)
                    if meaning:
                        explain.append(f'<div class="mean">{html_escape(meaning)}</div>')
                    if ex.get("meanings"):
                        explain.append('<div class="en">' + "<br>".join(
                            html_escape(x) for x in ex["meanings"]) + "</div>")
                    if ex.get("examples"):
                        exs = "<br>".join(f"· {html_escape(x)}" for x in ex["examples"])
                        explain.append(f'<div class="ex">{exs}</div>')
                else:
                    if meaning:
                        explain.append(f'<div class="mean">{html_escape(meaning)}</div>')
                    explain.append(
                        f'<div class="ex dim">自动释义失败：{html_escape(ex.get("reason",""))}</div>')
            elif meaning:
                explain.append(f'<div class="mean">{html_escape(meaning)}</div>')

        elif mtype == "p":
            phrase = flds.get("phrase", "").strip()
            meaning = flds.get("meaning", "").strip()
            if phrase:
                text = highlight(text, phrase)
                explain.append(f'<div class="word">{html_escape(phrase)}</div>')
            if meaning:
                explain.append(f'<div class="mean">{html_escape(meaning)}</div>')

        elif mtype == "s":
            st = flds.get("structure", "").strip()
            if st:
                explain.append(f'<div class="word">{html_escape(st)}</div>')

        elif mtype == "l":
            mis = flds.get("misheard", "").strip()
            if mis:
                explain.append(f'<div class="word">听成：{html_escape(mis)}</div>')

        else:  # x
            note = flds.get("note", "").strip()
            if note:
                explain.append(f'<div class="word">{html_escape(note)}</div>')

        meta_text = (f'{name} · {meta["label"]} · 第{sid}句 · '
                     f'{int(seg["start"]//60)}:{int(seg["start"]%60):02d}')

        rows.append({
            "audio": f"[sound:{fname}]",
            "text": text,
            "explain": "".join(explain),
            "meta": meta_text,
        })

    # 写 CSV：音频 / 原文 / 解说 / 元信息
    import csv as _csv
    csv_path = os.path.join(outdir, "import.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fp:
        w = _csv.writer(fp, delimiter="\t")
        for r in rows:
            w.writerow([r["audio"], r["text"], r["explain"], r["meta"]])

    log(f"完成：{len(rows)} 张卡")
    return {"ok": True, "count": len(rows), "dir": outdir, "csv": csv_path}


def html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def highlight(text, target):
    """在句中加粗高亮目标词/短语（大小写不敏感，支持词形）"""
    esc = html_escape(text)
    t = html_escape(target)
    if not t:
        return esc
    # 直接匹配
    pat = re.compile(r"(?<![A-Za-z])" + re.escape(t) + r"(?![A-Za-z])", re.I)
    if pat.search(esc):
        return pat.sub(lambda m: f"<b class='hl'>{m.group(0)}</b>", esc)
    # 宽松匹配（词干）
    stem = re.escape(t.rstrip("s"))
    pat2 = re.compile(r"(?<![A-Za-z])" + stem + r"\w*", re.I)
    return pat2.sub(lambda m: f"<b class='hl'>{m.group(0)}</b>", esc)


# ---------------------------------------------------------------- Anki

ANKICONNECT_URL = "http://127.0.0.1:8765"


def anki_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Anki.exe"],
                             capture_output=True, text=True).stdout
        return "Anki.exe" in out
    except Exception:
        return False


def ankiconnect_available():
    """检测 AnkiConnect 插件是否在运行"""
    try:
        body = json.dumps({"action": "version", "version": 6}).encode()
        req = urllib.request.Request(ANKICONNECT_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
        return bool(data.get("result"))
    except Exception:
        return False


def _anki_invoke(action, **params):
    payload = {"action": action, "version": 6, "params": params}
    req = urllib.request.Request(
        ANKICONNECT_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("result")


# 卡片模板（正面只有音频）
MODEL_FRONT = """<div class="front">
{{音频}}
<div class="hint">听音频，说出意思</div>
</div>"""

MODEL_BACK = """{{FrontSide}}

<hr id=answer>

<div class="back">
  <div class="orig">{{原文}}</div>
  <div class="explain">{{解说}}</div>
  <div class="meta">{{元信息}}</div>
</div>"""

MODEL_CSS = """.card {
  font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
  font-size: 19px; text-align: center; color: #e6e9ef;
  background: #14161a; padding: 28px 22px; line-height: 1.7;
}
.front { margin: 10px 0 24px; }
.hint { font-size: 13px; color: #6b7280; margin-top: 16px; letter-spacing: .5px; }
hr#answer { border: none; border-top: 1px solid #2e3440; margin: 24px 0; }
.back { text-align: left; max-width: 720px; margin: 0 auto; }
.orig { font-size: 21px; font-weight: 600; color: #f0f3f8; line-height: 1.75; margin-bottom: 18px; }
.orig b.hl { color: #ffd166; background: rgba(255,209,102,.12); padding: 1px 5px; border-radius: 4px; }
.explain { font-size: 17px; }
.explain .word { font-size: 20px; font-weight: 700; color: #4c8dff; margin: 12px 0 6px; }
.explain .ph { font-size: 15px; color: #8b93a5; font-weight: 400; }
.explain .en { color: #a8b2c4; font-size: 15px; margin: 8px 0; line-height: 1.65; }
.explain .mean { color: #6ee7a8; font-size: 19px; font-weight: 600; margin: 10px 0; }
.explain .ex { color: #8b93a5; font-size: 15px; font-style: italic; line-height: 1.8;
  margin-top: 10px; padding-left: 12px; border-left: 2px solid #2e3440; }
.explain .dim { color: #6b7280; }
.meta { font-size: 12px; color: #4b5563; margin-top: 22px; padding-top: 12px;
  border-top: 1px solid #232833; }
"""


def ensure_anki_model():
    """在 Anki 中创建笔记类型（若不存在）"""
    _anki_invoke("createModel",
                 modelName=MODEL_NAME,
                 inOrderFields=["音频", "原文", "解说", "元信息"],
                 css=MODEL_CSS,
                 isCloze=False,
                 cardTemplates=[{"Name": "音频->理解",
                                 "Front": MODEL_FRONT, "Back": MODEL_BACK}])
    return True


def import_via_ankiconnect(name, outdir, media, csv_path):
    """通过 AnkiConnect 导入（Anki 可保持运行）"""
    import base64
    import csv as _csv

    # 1. 上传音频
    uploaded = 0
    for f in sorted(os.listdir(media)):
        if not f.lower().endswith(".mp3"):
            continue
        with open(os.path.join(media, f), "rb") as fp:
            b64 = base64.b64encode(fp.read()).decode()
        _anki_invoke("storeMediaFile", filename=f, data=b64)
        uploaded += 1

    # 2. 确保笔记类型存在（不存在就自动创建）
    model_names = _anki_invoke("modelNames") or []
    if MODEL_NAME not in model_names:
        try:
            ensure_anki_model()
        except Exception as e:
            return {"ok": False,
                    "reason": f"Anki 里没有笔记类型「{MODEL_NAME}」，自动创建失败：{e}"}

    # 3. 确保牌组存在
    decks = _anki_invoke("deckNames") or []
    if DECK_NAME not in decks:
        _anki_invoke("createDeck", deck=DECK_NAME)

    # 4. 读 CSV 并建卡
    with open(csv_path, encoding="utf-8", newline="") as fp:
        rows = list(_csv.reader(fp, delimiter="\t"))

    notes = []
    for row in rows:
        if len(row) < 4:
            continue
        notes.append({
            "deckName": DECK_NAME,
            "modelName": MODEL_NAME,
            "fields": {
                "音频": row[0],
                "原文": row[1],
                "解说": row[2],
                "元信息": row[3],
            },
            "options": {"allowDuplicate": True},
        })

    if not notes:
        return {"ok": False, "reason": "CSV 里没有有效卡片"}

    ids = _anki_invoke("addNotes", notes=notes)
    added = len([i for i in (ids or []) if i])

    # 注意：这个版本的 AnkiConnect 会忽略 addNotes 里的 deckName，
    # 卡片会落到「系统默认」。必须显式移动。
    moved = 0
    good_ids = [i for i in (ids or []) if i]
    if good_ids:
        infos = _anki_invoke("notesInfo", notes=good_ids) or []
        card_ids = []
        for n in infos:
            card_ids.extend(n.get("cards") or [])
        if card_ids:
            try:
                _anki_invoke("changeDeck", cards=card_ids, deck=DECK_NAME)
                moved = len(card_ids)
            except Exception as e:
                return {"ok": True, "added": added, "media": uploaded,
                        "mode": "ankiconnect",
                        "warning": f"卡片已添加但移动牌组失败：{e}"}

    return {"ok": True, "added": added, "media": uploaded,
            "moved": moved, "mode": "ankiconnect"}


def import_to_anki(name):
    """把生成的卡片导入 Anki。优先用 AnkiConnect（Anki 可开着），否则直接写数据库。"""
    outdir = os.path.join(CARDS_DIR, name)
    media = os.path.join(outdir, "media")
    csv_path = os.path.join(outdir, "import.csv")
    if not os.path.exists(csv_path):
        return {"ok": False, "reason": "还没生成卡片，请先点「生成卡片」"}

    # --- 路线 A：AnkiConnect ---
    if ankiconnect_available():
        try:
            return import_via_ankiconnect(name, outdir, media, csv_path)
        except Exception as e:
            return {"ok": False, "reason": f"AnkiConnect 调用失败：{e}"}

    # --- 路线 B：直接写数据库（需关闭 Anki）---
    if anki_running():
        return {"ok": False,
                "reason": "Anki 正在运行，且未检测到 AnkiConnect 插件。"
                          "请安装 AnkiConnect，或先完全关闭 Anki。"}

    collection_path = get_collection_path()
    anki_media = get_anki_media_dir()

    if not os.path.exists(collection_path):
        return {"ok": False, "reason": f"找不到 Anki 数据库：{collection_path}\n"
                                       f"可用环境变量 ANKI_COLLECTION 指定完整路径。"}

    copied = 0
    os.makedirs(anki_media, exist_ok=True)
    for f in sorted(os.listdir(media)):
        if f.lower().endswith(".mp3"):
            dst = os.path.join(anki_media, f)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(media, f), dst)
                copied += 1

    import csv as _csv
    from anki.collection import Collection

    col = Collection(collection_path)
    model = col.models.by_name(MODEL_NAME)
    if not model:
        col.close()
        return {"ok": False, "reason": f"找不到笔记类型「{MODEL_NAME}」"}
    did = col.decks.id(DECK_NAME)

    with open(csv_path, encoding="utf-8", newline="") as fp:
        rows = list(_csv.reader(fp, delimiter="\t"))

    added = 0
    for row in rows:
        if len(row) < 4:
            continue
        note = col.new_note(model)
        note.fields[0] = row[0]      # 音频
        note.fields[1] = row[1]      # 原文（高亮版）
        note.fields[2] = row[2]      # 背面内容
        note.fields[3] = row[3]      # 元信息
        col.add_note(note, did)
        added += 1

    col.close()
    return {"ok": True, "added": added, "media": copied}
