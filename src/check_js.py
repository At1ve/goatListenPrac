# -*- coding: utf-8 -*-
"""
检查 web/index.html 里的 JS 语法（用 node --check）。
启动程序前可手动跑一次：python check_js.py
"""
import os
import re
import sys
import io
import subprocess
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "index.html")


def main():
    if not os.path.exists(HTML):
        print("找不到界面文件:", HTML)
        return 1

    html = open(HTML, encoding="utf-8").read()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not scripts:
        print("界面里没有 <script> 块")
        return 0

    ok = True
    for i, js in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fp:
            fp.write(js)
            tmp = fp.name
        try:
            r = subprocess.run(["node", "--check", tmp],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            if r.returncode == 0:
                print(f"script 块 {i}: 语法 OK ({len(js)} 字符)")
            else:
                ok = False
                print(f"script 块 {i}: 语法错误！")
                print(r.stderr[:1500])
        finally:
            os.unlink(tmp)

    # 附带检查 id 引用完整性
    ids = set(re.findall(r'id="([a-zA-Z0-9_]+)"', html))
    refs = set(re.findall(r"\$\('([a-zA-Z0-9_]+)'\)", html))
    missing = refs - ids
    if missing:
        ok = False
        print("引用了不存在的 id:", missing)
    else:
        print("id 引用完整")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
