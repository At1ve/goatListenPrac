# -*- coding: utf-8 -*-
"""
清空指定牌组的所有卡片。

用法:
    python clear_deck.py                    # 清空默认牌组
    python clear_deck.py "牌组名"
    python clear_deck.py --list             # 列出所有牌组

需要 Anki 处于关闭状态。
"""
import sys
import io
import os
import re
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("deck", nargs="?", default=core.DECK_NAME)
    ap.add_argument("--list", action="store_true", help="列出所有牌组")
    ap.add_argument("--keep-media", action="store_true", help="保留媒体文件")
    args = ap.parse_args()

    path = core.get_collection_path()
    if not os.path.exists(path):
        print("找不到 Anki 数据库：", path)
        print("可用环境变量 ANKI_COLLECTION 指定完整路径。")
        return 1

    from anki.collection import Collection
    col = Collection(path)

    if args.list:
        print("现有牌组：")
        for d in col.decks.all_names_and_ids():
            n = len(col.find_notes(f'deck:"{d.name}"'))
            print(f"  {d.name}  ({n} 张)")
        col.close()
        return 0

    did = col.decks.id_for_name(args.deck)
    if did is None:
        print("找不到牌组:", args.deck)
        col.close()
        return 1

    nids = col.find_notes(f'deck:"{args.deck}"')
    print(f"牌组 [{args.deck}] 现有 {len(nids)} 张笔记")
    if not nids:
        col.close()
        return 0

    media_names = set()
    if not args.keep_media:
        for nid in nids:
            note = col.get_note(nid)
            for f in note.fields:
                media_names.update(re.findall(r"\[sound:([^\]]+)\]", f))

    col.remove_notes(nids)
    print(f"已删除 {len(nids)} 张笔记")

    if media_names:
        still = set()
        for nid in col.find_notes(""):
            note = col.get_note(nid)
            for f in note.fields:
                still.update(re.findall(r"\[sound:([^\]]+)\]", f))
        mdir = core.get_anki_media_dir()
        removed = 0
        for name in media_names:
            if name not in still:
                p = os.path.join(mdir, name)
                if os.path.exists(p):
                    os.remove(p)
                    removed += 1
        print(f"清理媒体文件 {removed} 个")

    col.close()
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
