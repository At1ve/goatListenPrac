# -*- coding: utf-8 -*-
"""
检查并打包仓库源码。

现在仓库本身就是源码目录，所以这个脚本做两件事：
  1. 自检 —— 确认没有个人数据混进仓库
  2. 打包 —— 生成可上传 Release 的源码 zip

用法（在仓库根目录）:
    python build/build_release.py

产出:
    dist/goatListenPrac-<版本>-source.zip
"""
import io
import os
import re
import sys
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import ROOT, DIST, NAME, VERSION   # noqa: E402

PKG = NAME

# 应该出现在仓库里的文件（用于自检）
EXPECT = [
    "README.md", "LICENSE", "requirements.txt", "setup.py",
    "使用说明.md", "启动.bat", "安装.bat", ".gitignore",
    "src/app.py", "src/core.py", "src/resplit.py",
    "src/filedialog_win.py", "src/clear_deck.py", "src/check_js.py",
    "web/index.html",
    "build/build_release.py", "build/build_exe.py",
    "build/build_installer.py", "build/installer.iss",
]

# 不该被提交的东西
BAD_EXT = re.compile(
    r"\.(mp3|wav|m4a|flac|ogg|aac|wma|mp4|mkv|"
    r"srt|log|pyc|bmp|jpg|jpeg|spec)$", re.I)
BAD_JSON = re.compile(r"\.json$", re.I)
BAD_DIRS = {"data", "anki", "ffmpeg", "dist", "release", "runtime",
            "__pycache__", ".git", ".venv", "venv", "node_modules"}
BAD_NAMES = {"ui_state.json", "ui_state.json.tmp", "error.log", "crash.log"}


def log(m):
    print(m, flush=True)


def scan():
    """扫仓库，返回 (问题列表, 会被打包的文件列表)"""
    problems = []
    files = []

    for root, dirs, fs in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in BAD_DIRS]
        for f in fs:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")

            if f in BAD_NAMES:
                problems.append(rel + "  ← 用户数据/日志")
                continue
            if BAD_EXT.search(f):
                problems.append(rel + "  ← 不该提交的文件类型")
                continue
            if BAD_JSON.search(f):
                problems.append(rel + "  ← JSON（可能是用户数据）")
                continue
            files.append(rel)

    return problems, sorted(files)


def check_traces():
    """
    扫源码里的个人痕迹。

    注意：跳过本文件自己 —— 它含有关键字定义，会误报。
    """
    self_path = os.path.abspath(__file__)
    pats = [
        (re.compile(r"[A-Za-z]:\\+work"), "本机绝对路径"),
        (re.compile(r"congroo"), "用户名"),
        (re.compile(r"CB\d{3}_"), "个人音频名"),
        (re.compile(r"10-06-1"), "个人音频名"),
        (re.compile(r"账户\s*\d"), "Anki 配置名"),
    ]
    hits = []
    for root, dirs, fs in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in BAD_DIRS]
        for f in fs:
            if not f.endswith((".py", ".md", ".html", ".bat", ".iss", ".txt")):
                continue
            p = os.path.join(root, f)
            if os.path.abspath(p) == self_path:
                continue                      # 跳过自己
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            try:
                t = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for pat, desc in pats:
                for m in pat.finditer(t):
                    hits.append(rel + ": " + desc + " -> " + m.group(0)[:40])
    return hits


def main():
    log("=" * 58)
    log("  goatListenPrac 源码检查与打包")
    log("=" * 58)

    # ---- 1. 必需文件 ----
    log("\n[1/4] 必需文件")
    miss = []
    for f in EXPECT:
        if os.path.exists(os.path.join(ROOT, f)):
            log("  OK " + f)
        else:
            log("  ! 缺少 " + f)
            miss.append(f)

    # ---- 2. 数据检查 ----
    log("\n[2/4] 数据检查")
    problems, files = scan()
    if problems:
        log("  ! 发现问题:")
        for p in problems:
            log("    " + p)
    else:
        log("  OK 无音频 / 转写 / 标记 / 状态文件")

    # ---- 3. 个人痕迹 ----
    log("\n[3/4] 个人痕迹")
    hits = check_traces()
    if hits:
        log("  ! 发现可疑内容:")
        for h in hits:
            log("    " + h)
    else:
        log("  OK 无个人痕迹")

    ok = (not miss) and (not problems) and (not hits)

    # ---- 4. 打包 ----
    log("\n[4/4] 打包")
    os.makedirs(DIST, exist_ok=True)
    zp = os.path.join(DIST, PKG + "-" + VERSION + "-source.zip")
    if os.path.exists(zp):
        os.remove(zp)

    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(os.path.join(ROOT, rel), os.path.join(PKG, rel))

    log("  OK " + zp)
    log("     %d 个文件, %.1f KB" % (len(files), os.path.getsize(zp) / 1024))

    log("\n" + "=" * 58)
    if ok:
        log("  检查通过，可以推送到 GitHub")
    else:
        log("  ! 有问题需要处理（见上）")
    log("=" * 58)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
