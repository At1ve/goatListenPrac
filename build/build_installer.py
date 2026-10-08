# -*- coding: utf-8 -*-
"""
生成 Windows 安装包（用 Inno Setup 编译）。

前置：
    python build/build_exe.py       # 先打包成 exe

用法（在仓库根目录）:
    python build/build_installer.py

产出:
    dist/goatListenPrac-Setup-<版本>.exe
"""
import io
import os
import sys
import shutil
import subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import ROOT, DIST, EXEDIR as BUILDDIR, NAME, VERSION  # noqa: E402

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "installer.iss")

ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
]


def log(m):
    print(m, flush=True)


def find_iscc():
    for p in ISCC_CANDIDATES:
        if os.path.exists(p):
            return p
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, "ISCC.exe")
        if os.path.exists(p):
            return p
    return None


def make_icon():
    """从截图生成 ico"""
    src = os.path.join(ROOT, "screenshot.png")
    dst = os.path.join(DIST, "_app.ico")
    if not os.path.exists(src):
        return None
    try:
        from PIL import Image
        im = Image.open(src).convert("RGBA")
        w, h = im.size
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
        im = im.resize((256, 256), Image.LANCZOS)
        im.save(dst, sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                            (128, 128), (256, 256)])
        return dst
    except Exception as e:
        log("  生成图标失败（忽略）: " + str(e))
        return None


def esc(p):
    return p.replace("\\", "\\\\")


def main():
    log("=" * 58)
    log("  生成安装包 " + NAME + "-Setup-" + VERSION + ".exe")
    log("=" * 58)

    log("\n[1/4] 检查前置")
    if not os.path.isdir(BUILDDIR):
        log("  找不到 " + BUILDDIR)
        log("  请先运行: python build/build_exe.py")
        return 1
    exe = os.path.join(BUILDDIR, NAME + ".exe")
    if not os.path.exists(exe):
        log("  找不到 " + exe)
        return 1
    log("  OK 程序目录: " + os.path.relpath(BUILDDIR, ROOT))

    iscc = find_iscc()
    if not iscc:
        log("  找不到 Inno Setup 的 ISCC.exe")
        log("  请安装: winget install JRSoftware.InnoSetup")
        return 1
    log("  OK 编译器: " + iscc)

    if not os.path.exists(TEMPLATE):
        log("  找不到 installer.iss")
        return 1
    log("  OK 脚本: build/installer.iss")

    # 清理残留，避免被打进安装包
    for f in ("ui_state.json", "ui_state.json.tmp", "error.log", "crash.log"):
        p = os.path.join(BUILDDIR, f)
        if os.path.exists(p):
            os.remove(p)
            log("  已清理: " + f)
    for d in ("mp3s", "transcript", "marks", "cards", "trans_cache"):
        p = os.path.join(BUILDDIR, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            log("  已清理: " + d + "/")

    log("\n[2/4] 准备图标")
    icon = make_icon()
    log("  " + ("OK " + os.path.basename(icon)) if icon else "  跳过（用默认）")

    log("\n[3/4] 生成安装脚本")
    t = open(TEMPLATE, encoding="utf-8").read()
    outname = NAME + "-Setup-" + VERSION
    t = t.replace("__OUTDIR__", esc(DIST))
    t = t.replace("__OUTNAME__", outname)
    t = t.replace("__BUILDDIR__", esc(BUILDDIR))
    t = t.replace("__ICON__", esc(icon) if icon else "")
    # 文档与协议从仓库根取（用绝对路径，避免 ISCC 工作目录不同导致找不到）
    t = t.replace("__LICENSE__", esc(os.path.join(ROOT, "LICENSE")))
    t = t.replace('Source: "README.md"',
                  'Source: "%s"' % esc(os.path.join(ROOT, "README.md")))
    t = t.replace('Source: "使用说明.md"',
                  'Source: "%s"' % esc(os.path.join(ROOT, "使用说明.md")))
    t = t.replace('Source: "LICENSE"',
                  'Source: "%s"' % esc(os.path.join(ROOT, "LICENSE")))

    tmp = os.path.join(DIST, "_installer_build.iss")
    with open(tmp, "w", encoding="utf-8-sig") as fp:
        fp.write(t)
    log("  OK")

    log("\n[4/4] 编译安装包（约 2 分钟）")
    r = subprocess.run([iscc, "/Q", tmp], cwd=DIST, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("  编译失败，退出码 " + str(r.returncode))
        if r.stdout:
            log(r.stdout[-3000:])
        return r.returncode

    setup = os.path.join(DIST, outname + ".exe")
    if not os.path.exists(setup):
        log("  没找到生成的安装包")
        return 1

    log("\n" + "=" * 58)
    log("  完成")
    log("=" * 58)
    log("  安装包: " + setup)
    log("  体积:   %.1f MB" % (os.path.getsize(setup) / 1024 / 1024))
    log("")
    log("  上传到 GitHub Releases:")
    log("    https://github.com/At1ve/goatListenPrac/releases/new")
    log("=" * 58)

    for x in (tmp, icon):
        try:
            if x and os.path.exists(x):
                os.remove(x)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
