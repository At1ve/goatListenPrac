# -*- coding: utf-8 -*-
"""
把拖入/选中的文件的真实路径交给后端。
pywebview 的 WebView2 里，<input type=file> 拿不到真实路径，
但可以用 Windows 原生的 tkinter 对话框（独立进程内，不碰 WebView2 线程）。
"""
import os
import io
import sys
import threading

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


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
