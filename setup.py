# -*- coding: utf-8 -*-
"""
一键安装：检查依赖、下载 ffmpeg、创建目录。

用法:
    python setup.py
"""
import os
import io
import sys
import zipfile
import shutil
import subprocess
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
FFMPEG_DIR = os.path.join(ROOT, "ffmpeg")

FFMPEG_URLS = {
    "win32": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "darwin": "https://evermeet.cx/ffmpeg/getrelease/zip",
    "linux": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
}


def log(msg):
    print(msg, flush=True)


def check_python():
    v = sys.version_info
    log(f"Python {v.major}.{v.minor}.{v.micro}")
    if v < (3, 9):
        log("  ✗ 需要 Python 3.9 或更高")
        return False
    log("  ✓ 版本满足")
    return True


def check_packages():
    ok = True
    for mod, pkg in [("faster_whisper", "faster-whisper"),
                     ("webview", "pywebview")]:
        try:
            __import__(mod)
            log(f"  ✓ {pkg}")
        except ImportError:
            log(f"  ✗ {pkg} 未安装")
            ok = False
    if not ok:
        log("\n  请先运行:  pip install -r requirements.txt")
    return ok


def find_ffmpeg():
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    local = os.path.join(FFMPEG_DIR, "bin", exe)
    if os.path.exists(local):
        return local
    return shutil.which("ffmpeg")


def install_ffmpeg():
    if find_ffmpeg():
        log(f"  ✓ ffmpeg 已就绪: {find_ffmpeg()}")
        return True

    plat = "win32" if os.name == "nt" else (
        "darwin" if sys.platform == "darwin" else "linux")
    url = FFMPEG_URLS.get(plat)
    if not url:
        log("  ✗ 未知平台，请手动安装 ffmpeg 并加入 PATH")
        return False

    log(f"  下载 ffmpeg ({plat})…")
    tmp = os.path.join(ROOT, "_ffmpeg_dl")
    os.makedirs(tmp, exist_ok=True)
    archive = os.path.join(tmp, "ffmpeg.zip" if plat != "linux" else "ffmpeg.tar.xz")

    try:
        urllib.request.urlretrieve(url, archive)
    except Exception as e:
        log(f"  ✗ 下载失败: {e}")
        log("    请手动下载 ffmpeg 并放到 ffmpeg/bin/ 下")
        return False

    log("  解压…")
    try:
        if archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
            # 找到含 bin/ffmpeg 的目录
            for root, dirs, files in os.walk(tmp):
                if "ffmpeg.exe" in files or "ffmpeg" in files:
                    if os.path.basename(root) == "bin":
                        parent = os.path.dirname(root)
                        if os.path.exists(FFMPEG_DIR):
                            shutil.rmtree(FFMPEG_DIR)
                        shutil.move(parent, FFMPEG_DIR)
                        break
        else:
            subprocess.run(["tar", "-xf", archive, "-C", tmp], check=True)
            for d in os.listdir(tmp):
                p = os.path.join(tmp, d)
                if os.path.isdir(p) and d.startswith("ffmpeg"):
                    if os.path.exists(FFMPEG_DIR):
                        shutil.rmtree(FFMPEG_DIR)
                    shutil.move(p, FFMPEG_DIR)
                    break
    except Exception as e:
        log(f"  ✗ 解压失败: {e}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    exe = find_ffmpeg()
    if exe:
        log(f"  ✓ ffmpeg 安装完成: {exe}")
        return True
    log("  ✗ 安装后仍未找到 ffmpeg")
    return False


def make_dirs():
    for d in ["mp3s", "transcript", "marks", "cards", "trans_cache", "data"]:
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    # 让空目录能进 git
    for d in ["mp3s", "transcript", "marks", "cards", "trans_cache"]:
        p = os.path.join(ROOT, d, ".gitkeep")
        if not os.path.exists(p):
            open(p, "w").close()
    log("  ✓ 目录已创建")


def main():
    log("=" * 56)
    log("  精听工具 — 安装检查")
    log("=" * 56)

    log("\n[1/4] Python 版本")
    if not check_python():
        return 1

    log("\n[2/4] Python 依赖")
    pkgs_ok = check_packages()

    log("\n[3/4] FFmpeg（音频切分）")
    ff_ok = install_ffmpeg()

    log("\n[4/4] 工作目录")
    make_dirs()

    log("\n" + "=" * 56)
    if pkgs_ok and ff_ok:
        log("  ✓ 全部就绪")
        log("\n  启动方式：")
        log("    python src/app.py")
        log("  或双击 启动.bat")
    else:
        log("  ⚠ 有项目未完成，请按上面提示处理后重跑")
    log("=" * 56)
    return 0 if (pkgs_ok and ff_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
