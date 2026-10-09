# -*- coding: utf-8 -*-
"""
文件选择对话框（Windows 原生）。

pywebview 的 WebView2 里，<input type=file> 拿不到真实路径，
所以用 tkinter 的原生对话框（独立线程，不碰 WebView2 线程）。

打包成 exe 后有个坑：tkinter 依赖 Tcl/Tk 的脚本库（init.tcl 等），
PyInstaller 会把它们放在 _tcl_data / _tk_data 下，但不会自动设置
TCL_LIBRARY / TK_LIBRARY 环境变量。如果不设置，Tcl 会去开发者机器的
原始路径找 —— 在别人电脑上就找不到，对话框直接报错，导入功能失效。
所以这里在导入 tkinter 之前手动指好路径。
"""
import os
import sys
import threading

# ---- stdout/stderr 可能是 None（无控制台打包）----
if sys.stdout is not None:
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    except Exception:
        pass
if sys.stderr is not None:
    try:
        import io
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      errors="replace")
    except Exception:
        pass


def _fix_tcl_env():
    """
    打包运行时，把 TCL_LIBRARY / TK_LIBRARY 指向随程序打包的 Tcl/Tk 数据。

    目录名由 PyInstaller 的 tkinter 钩子决定，不同版本可能是
    _tcl_data / _tk_data，也可能是 tcl / tk，这里都试一遍。
    """
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("TCL_LIBRARY") and os.environ.get("TK_LIBRARY"):
        return                                  # 已经设好就不动

    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)

    tcl_names = ["_tcl_data", "tcl", os.path.join("tcl", "tcl8.6")]
    tk_names = ["_tk_data", "tk", os.path.join("tk", "tk8.6")]

    for n in tcl_names:
        p = os.path.join(base, n)
        if os.path.exists(os.path.join(p, "init.tcl")):
            os.environ.setdefault("TCL_LIBRARY", p)
            break

    for n in tk_names:
        p = os.path.join(base, n)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "tk.tcl")):
            os.environ.setdefault("TK_LIBRARY", p)
            break


_fix_tcl_env()


def pick_audio_file(title="选择音频文件", initialdir=None):
    """
    用 tkinter 弹出文件选择框（返回 dict）。
    必须在独立线程里调用，tkinter 自己管理事件循环。
    """
    import os as _os
    if initialdir is None:
        # 默认打开 mp3s 目录（运行目录下的）
        try:
            import core
            base = core.MEDIA_DIR_MP3S
        except Exception:
            base = _os.path.expanduser("~")
        initialdir = base if _os.path.isdir(base) else _os.path.expanduser("~")

    result = {"path": None}

    def run():
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.update()

            types = [
                ("音频文件", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.wma"),
                ("所有文件", "*.*"),
            ]
            p = filedialog.askopenfilename(
                title=title,
                initialdir=initialdir,
                filetypes=types,
            )
            root.destroy()
            if p:
                result["path"] = _os.path.abspath(p)
        except Exception as e:
            result["error"] = str(e)

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=300)
    return result


if __name__ == "__main__":
    r = pick_audio_file()
    print("结果:", r)
