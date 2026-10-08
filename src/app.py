# -*- coding: utf-8 -*-
"""
精听工具 — 桌面程序入口

启动一个本地 HTTP 服务（用于音频流播放）+ pywebview 原生窗口。

用法:
    python app.py
"""
import os
import sys
import io
import json
import threading
import functools
import mimetypes
import traceback
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

# ---- 打包成无控制台程序时，stdout/stderr 可能是 None ----
# 直接包 TextIOWrapper 会崩，所以先判断再处理。
if sys.stdout is not None:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    except Exception:
        pass
if sys.stderr is not None:
    try:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      errors="replace")
    except Exception:
        pass


def _log_path():
    """崩溃日志写在 exe / 项目目录下"""
    base = (os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "error.log")


def log(msg):
    """写日志（打包后没有控制台，只能写文件）"""
    line = str(msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(_log_path(), "a", encoding="utf-8") as fp:
            from datetime import datetime
            fp.write("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] "
                     + line + "\n")
    except Exception:
        pass


def show_fatal(title, detail):
    """弹窗提示致命错误（用户能看到，而不是静默退出）"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            detail + "\n\n详细信息已写入:\n" + _log_path(),
            title, 0x10)   # MB_ICONERROR
    except Exception:
        pass


import core

ROOT = core.ROOT
WEB_DIR = core.WEB_DIR

PORT_CANDIDATES = [8770, 8771, 8772, 8899, 9123]
_start_port = None


class Handler(SimpleHTTPRequestHandler):
    """静态文件 + /audio/<name> 音频流"""

    def log_message(self, *a):
        pass  # 静音

    def do_GET(self):
        if self.path.startswith("/audio/"):
            return self.serve_audio(self.path[len("/audio/"):].split("?")[0])
        # 默认走 web 目录
        if self.path == "/" or self.path.startswith("/index"):
            self.path = "/index.html"
        return super().do_GET()

    def serve_audio(self, name):
        from urllib.parse import unquote
        name = unquote(name)
        path = core.find_audio(name)
        if not path or not os.path.exists(path):
            self.send_error(404, "audio not found")
            return
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "audio/mpeg"

        # 支持 Range 请求（拖动进度条需要）
        range_hdr = self.headers.get("Range")
        start, end = 0, size - 1
        if range_hdr and range_hdr.startswith("bytes="):
            try:
                r = range_hdr[6:].split("-")
                if r[0]:
                    start = int(r[0])
                if len(r) > 1 and r[1]:
                    end = int(r[1])
                end = min(end, size - 1)
            except Exception:
                pass

        length = end - start + 1
        self.send_response(206 if range_hdr else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if range_hdr:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with open(path, "rb") as f:
            f.seek(start)
            remain = length
            while remain > 0:
                chunk = f.read(min(65536, remain))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError):
                    break
                remain -= len(chunk)


class Api:
    """暴露给前端（pywebview js_api）"""

    def __init__(self):
        self._window = None
        self.task = {"running": False, "stage": "", "percent": 0, "message": ""}

    def set_window(self, w):
        self._window = w

    # ---------------- 界面状态持久化 ----------------
    # 不用浏览器 localStorage（WebView2 下可能被禁用或丢失），
    # 统一存到项目目录的 ui_state.json

    def load_state(self):
        try:
            return {"ok": True, "state": core.load_ui_state()}
        except Exception as e:
            return {"ok": False, "reason": str(e), "state": {}}

    def save_state(self, state):
        try:
            return core.save_ui_state(state)
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def get_state(self, key, default=None):
        try:
            return {"ok": True, "value": core.get_state(key, default)}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def set_state(self, key, value):
        try:
            return core.set_state(key, value)
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    # ---------------- 全屏 ----------------

    def toggle_fullscreen(self):
        """切换真正的全屏（无标题栏、铺满屏幕）"""
        try:
            w = self._window
            if w is None:
                return {"ok": False, "reason": "窗口未就绪"}
            cur = bool(getattr(w, "fullscreen", False))
            w.toggle_fullscreen()
            return {"ok": True, "fullscreen": (not cur)}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def is_fullscreen(self):
        try:
            return {"ok": True,
                    "fullscreen": bool(getattr(self._window, "fullscreen", False))}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    # ---------------- 材料 ----------------

    def list_materials(self):
        return core.list_materials()

    def load_material(self, name):
        try:
            data = core.load_segments(name)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        marks = core.load_marks(name).get("marks", {})
        return {
            "ok": True,
            "name": name,
            "segments": data["segments"],
            "duration": data["duration"],
            "marks": marks,
            "audio_url": f"http://127.0.0.1:{_start_port}/audio/{name}",
        }

    # ---------------- 导入新材料 ----------------

    def pick_file(self):
        """
        弹出文件选择框。
        不用 pywebview 的 create_file_dialog（WebView2 下会抛
        "CoreWebView2Controller members can only be accessed from the UI thread"），
        改用 tkinter 原生对话框，完全绕开 WebView2 线程限制。
        """
        if self.task["running"]:
            return {"ok": False, "reason": "已有导入任务在进行中"}

        try:
            import filedialog_win
        except Exception as e:
            return {"ok": False, "reason": f"无法加载文件对话框模块：{e}"}

        initial = os.path.join(core.ROOT, "mp3s")
        r = filedialog_win.pick_audio_file(initialdir=initial)

        if r.get("error"):
            return {"ok": False, "reason": f"对话框出错：{r['error']}"}
        if not r.get("path"):
            return {"ok": False, "reason": "cancelled"}
        return {"ok": True, "path": r["path"],
                "file": os.path.basename(r["path"])}

    def import_path(self, src_path, model_size="small"):
        """前端拿到路径后调用，开始后台导入"""
        if self.task["running"]:
            return {"ok": False, "reason": "已有导入任务在进行中"}
        if not src_path or not os.path.exists(src_path):
            return {"ok": False, "reason": f"文件不存在：{src_path}"}
        self.start_import(src_path, model_size)
        return {"ok": True, "started": True,
                "file": os.path.basename(src_path)}

    def pick_and_import(self, model_size="small"):
        """兼容旧调用：选文件并直接开始导入"""
        r = self.pick_file()
        if not r.get("ok"):
            return r
        return self.import_path(r["path"], model_size)

    def start_import(self, src_path, model_size="small"):
        """后台线程执行导入"""
        self.task = {"running": True, "stage": "start", "percent": 0,
                     "message": "准备中…", "name": ""}

        def worker():
            def prog(stage, done, total, msg):
                pct = int(done * 100 / total) if total else 0
                self.task.update({"stage": stage, "percent": pct, "message": msg})
            try:
                res = core.import_material(src_path, progress=prog, model_size=model_size)
                if res.get("ok"):
                    self.task.update({"running": False, "stage": "done",
                                      "percent": 100, "name": res["name"],
                                      "message": f"完成：{res['segments']} 句"})
                else:
                    self.task.update({"running": False, "stage": "error",
                                      "message": res.get("reason", "导入失败")})
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.task.update({"running": False, "stage": "error",
                                  "message": f"出错：{e}"})

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def import_status(self):
        """前端轮询进度"""
        return dict(self.task)

    def cancel_import(self):
        """标记取消（转写本身不可中断，仅停止前端轮询）"""
        self.task.update({"running": False, "stage": "cancelled",
                          "message": "已取消"})
        return {"ok": True}

    def material_info(self, name):
        """删除前的信息提示"""
        try:
            return {"ok": True, "info": core.material_info(name)}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def delete_material(self, name, delete_audio=False):
        """删除材料；delete_audio=True 时连原始音频一起删"""
        try:
            return core.delete_material(name, delete_audio=delete_audio)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "reason": str(e)}

    def rename_material(self, old, new):
        try:
            return core.rename_material(old, new)
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    # ---------------- 翻译 ----------------

    def translate(self, text, target="zh-CN"):
        try:
            return core.translate(text, target)
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def translate_material(self, name):
        """翻译整份材料的所有句子"""
        try:
            data = core.load_segments(name)
        except Exception as e:
            return {"ok": False, "reason": str(e)}

        out = {}
        for s in data["segments"]:
            r = core.translate(s["text"])
            if r.get("ok"):
                out[str(s["id"])] = r["text"]
        return {"ok": True, "translations": out, "count": len(out)}

    def lookup_word(self, word):
        try:
            return core.lookup_word(word)
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    # ---------------- 标记 ----------------

    def set_mark(self, name, seg_id, mtype, fields):
        marks = core.set_mark(name, seg_id, mtype, fields)
        return {"ok": True, "marks": marks}

    # ---------------- 制卡 / 导入 Anki ----------------

    def build_cards(self, name):
        try:
            return core.build_cards(name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "reason": str(e)}

    def import_to_anki(self, name):
        try:
            return core.import_to_anki(name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "reason": str(e)}

    def anki_status(self):
        return {
            "running": core.anki_running(),
            "ankiconnect": core.ankiconnect_available(),
        }


def start_server():
    global _start_port
    handler = functools.partial(Handler, directory=WEB_DIR)
    for p in PORT_CANDIDATES:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), handler)
            _start_port = p
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            log("本地服务已启动: http://127.0.0.1:%d" % p)
            return p
        except OSError:
            continue
    raise RuntimeError("所有候选端口都被占用（8770~9123）")


def _preflight():
    """启动前自检，把问题变成清晰的提示"""
    problems = []

    if not os.path.isdir(WEB_DIR):
        problems.append("找不到界面文件目录: " + WEB_DIR)
    elif not os.path.exists(os.path.join(WEB_DIR, "index.html")):
        problems.append("找不到界面文件: " + os.path.join(WEB_DIR, "index.html"))

    try:
        import webview  # noqa
    except Exception as e:
        problems.append("缺少 pywebview（窗口组件）: " + str(e))

    try:
        import faster_whisper  # noqa
    except Exception as e:
        problems.append("缺少 faster-whisper（语音识别）: " + str(e))

    return problems


def main():
    try:
        log("=" * 50)
        log("精听工具启动")
        log("frozen=" + str(getattr(sys, "frozen", False)))
        log("ROOT=" + ROOT)
        log("WEB_DIR=" + WEB_DIR)

        problems = _preflight()
        if problems:
            msg = "启动失败：\n\n" + "\n".join("· " + p for p in problems)
            log(msg)
            show_fatal("精听工具 - 启动失败", msg)
            return 1

        core.ensure_dirs()
        port = start_server()

        import webview

        api = Api()
        window = webview.create_window(
            "精听工具 — 英语听力训练",
            url="http://127.0.0.1:%d/index.html" % port,
            js_api=api,
            width=1280,
            height=840,
            min_size=(940, 620),
            text_select=True,
            frameless=False,
        )
        api.set_window(window)
        webview.start()
        log("正常退出")
        return 0

    except Exception:
        tb = traceback.format_exc()
        log("启动异常:\n" + tb)
        show_fatal("精听工具 - 启动异常", tb[-1200:])
        return 1


if __name__ == "__main__":
    sys.exit(main())
