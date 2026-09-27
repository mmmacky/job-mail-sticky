# -*- coding: utf-8 -*-
import json
import os
import sys
import shutil
from pathlib import Path

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent

def data_dir():
    if os.environ.get('JOB_STICKY_TEST_DATA'):
        return Path(os.environ['JOB_STICKY_TEST_DATA'])
    pointer = APP_DIR / 'data-location.json'
    if pointer.exists():
        value = Path(json.loads(pointer.read_text(encoding='utf-8-sig'))['path'])
        if not value.is_absolute(): raise ValueError('Data directory must be absolute')
        return value
    if (APP_DIR / 'data').exists(): return APP_DIR / 'data'
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'JobMailSticky'

def select_data_directory(destination, old):
    target, old = Path(destination).resolve(), Path(old).resolve()
    if target == old: return target
    if target in old.parents or old in target.parents:
        raise ValueError('请选择与现有数据文件夹独立的位置。')
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()): raise ValueError('请选择空文件夹，避免覆盖已有数据。')
    if old.exists():
        for item in old.iterdir():
            if item.is_dir(): shutil.copytree(item, target / item.name)
            else: shutil.copy2(item, target / item.name)
    pointer = APP_DIR / 'data-location.json'
    temp = pointer.with_suffix('.tmp')
    temp.write_text(json.dumps({'path': str(target)}, ensure_ascii=False), encoding='utf-8')
    temp.replace(pointer)
    return target

def start_tray(events):
    import pystray
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (64, 64), '#286653')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((14, 10, 50, 54), radius=5, fill='#F3F5F2')
    for y in (24, 33, 42): draw.line((22, y, 42, y), fill='#286653', width=3)
    icon = pystray.Icon('JobMailSticky', image, '求职便利贴', menu=pystray.Menu(
        pystray.MenuItem('打开便利贴', lambda: events.put(('show', None)), default=True),
        pystray.MenuItem('立即更新', lambda: events.put(('sync', None))),
        pystray.MenuItem('退出程序', lambda: events.put(('quit', None)))))
    icon.run_detached()
    return icon

