# -*- coding: utf-8 -*-
"""
构建脚本公共部分：路径解析。

产物统一放到仓库**外面**的 dist/，让仓库目录保持轻量。
如果外层不可写，退回仓库内的 dist/。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)            # 仓库根
NAME = "goatListenPrac"
VERSION = "1.0.0"


def _can_write(d):
    try:
        os.makedirs(d, exist_ok=True)
        t = os.path.join(d, "._wtest")
        open(t, "w").close()
        os.remove(t)
        return True
    except Exception:
        return False


# 优先用仓库外层的 dist/
_outer_parent = os.path.dirname(ROOT)
DIST = (os.path.join(_outer_parent, "dist")
        if _can_write(_outer_parent)
        else os.path.join(ROOT, "dist"))

BUILD = os.path.join(DIST, "exe")               # exe 输出根
WORK = os.path.join(DIST, "_pyi")               # PyInstaller 中间文件
EXEDIR = os.path.join(BUILD, NAME)              # 具体程序目录
SPEC = os.path.join(DIST, NAME + ".spec")
