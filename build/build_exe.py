# -*- coding: utf-8 -*-
"""
打包成 Windows 可执行程序（文件夹模式）。

用法（在仓库根目录）:
    python build/build_exe.py

产出:
    dist/exe/goatListenPrac/goatListenPrac.exe
    dist/exe/goatListenPrac/                      完整运行目录

注意：
  · 用文件夹模式而非单文件 —— 启动快、体积可控、少被杀软误报
  · Whisper 模型和 ffmpeg 不打进去（太大），首次运行自动下载
  · 产物在 dist/ 下，不影响仓库
"""
import io
import os
import sys
import shutil
import subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import ROOT, DIST, BUILD, WORK, NAME   # noqa: E402


def log(m):
    print(m, flush=True)


def check():
    need = ["src/app.py", "src/core.py", "web/index.html"]
    for f in need:
        if not os.path.exists(os.path.join(ROOT, f)):
            log("缺少 " + f)
            return False
    return True


def write_spec():
    """生成 PyInstaller spec（放在 dist/ 下，不污染仓库）"""
    spec = '''# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_all)

ROOT = r"%s"

# ---------------------------------------------------------------
# 数据文件收集
#
# 关键坑：PyInstaller 只收集 Python 代码，不会自动带上包内的非代码
# 文件。faster_whisper 的 VAD 模型 silero_vad_v6.onnx 就是这样漏掉的，
# 结果运行时抛 ONNXRuntimeError: NO_SUCHFILE，导入音频直接失败。
#
# 另一个坑：collect_data_files 会把资源放进 faster_whisper/assets/，
# 但代码在 PYZ 归档里，导致生成一个没有 __init__.py 的目录 —— 它会
# 把 PYZ 里的同名模块"遮住"，import 反而坏掉。
# 所以这里对 faster_whisper 用 collect_all，把代码和数据一起落盘。
# ---------------------------------------------------------------
datas = [
    (os.path.join(ROOT, "web"), "web"),
]

# faster_whisper：代码 + 资源一起收集（避免目录遮住 PYZ）
datas += collect_all("faster_whisper")[0]      # datas
binaries = collect_all("faster_whisper")[1]    # binaries
hiddenimports_extra = collect_all("faster_whisper")[2]

for pkg in ("ctranslate2", "onnxruntime", "tokenizers", "huggingface_hub"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

for pkg in ("ctranslate2", "onnxruntime", "av"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

hiddenimports = [
    "faster_whisper", "ctranslate2", "tokenizers", "huggingface_hub",
    "onnxruntime", "av", "webview", "webview.platforms.edgechromium",
    "clr_loader", "pythonnet", "tkinter", "tkinter.filedialog", "bottle",
    # 项目自己的模块：app.py 里是运行时 import，静态分析扫不到
    "core", "filedialog_win", "resplit", "clear_deck",
] + list(hiddenimports_extra)

# 排除用不到的大块头，显著减小体积
excludes = [
    "matplotlib", "scipy", "pandas", "IPython", "jupyter", "notebook",
    "pytest", "sphinx", "setuptools", "pip",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "torch", "torchaudio", "torchvision",
    "tensorflow", "keras",
    "numpy.f2py", "numpy.distutils",
]

a = Analysis(
    [os.path.join(ROOT, "src", "app.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries, datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="%s", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[], name="%s",
)
''' % (ROOT.replace("\\", "\\\\"), NAME, NAME)

    p = os.path.join(DIST, NAME + ".spec")
    os.makedirs(DIST, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fp:
        fp.write(spec)
    return p


def clean_artifacts(outdir):
    """清掉打包时从运行目录误带进来的文件"""
    junk = ["ui_state.json", "ui_state.json.tmp", "error.log", "crash.log"]
    removed = []
    for f in junk:
        p = os.path.join(outdir, f)
        if os.path.exists(p):
            os.remove(p)
            removed.append(f)
    for d in ("mp3s", "transcript", "marks", "cards", "trans_cache"):
        p = os.path.join(outdir, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            removed.append(d + "/")
    return removed


def main():
    log("=" * 58)
    log("  打包 " + NAME + ".exe")
    log("=" * 58)

    if not check():
        return 1

    try:
        import PyInstaller  # noqa
    except ImportError:
        log("\n缺少 pyinstaller:")
        log("    pip install pyinstaller")
        return 1

    log("\n[1/3] 生成 spec")
    spec = write_spec()
    log("  OK " + os.path.relpath(spec, ROOT))

    log("\n[2/3] 清理旧产物")
    for d in (BUILD, WORK):
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)
    log("  OK")

    log("\n[3/3] 运行 PyInstaller（约 4 分钟）")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", BUILD, "--workpath", WORK, spec]
    r = subprocess.run(cmd, cwd=DIST)
    if r.returncode != 0:
        log("\n打包失败，退出码 " + str(r.returncode))
        return r.returncode

    outdir = os.path.join(BUILD, NAME)
    exe = os.path.join(outdir, NAME + ".exe")
    if not os.path.exists(exe):
        log("\n没找到生成的 exe")
        return 1

    log("\n  清理运行残留")
    for x in clean_artifacts(outdir):
        log("    删除 " + x)

    tot = cnt = 0
    for root, dirs, files in os.walk(outdir):
        for f in files:
            tot += os.path.getsize(os.path.join(root, f))
            cnt += 1

    log("\n" + "=" * 58)
    log("  打包完成")
    log("=" * 58)
    log("  程序目录: " + outdir)
    log("  文件数:   " + str(cnt))
    log("  总体积:   %.1f MB" % (tot / 1024 / 1024))
    log("")
    log("  测试运行:   " + exe)
    log("  生成安装包: python build/build_installer.py")
    log("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
