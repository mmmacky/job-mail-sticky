# -*- coding: utf-8 -*-
import os
import sys
import time
import shutil
import copy
import base64
import ctypes
import email
import hashlib
import html
import imaplib
import json
import queue
import re
import ssl
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from email import policy
from pathlib import Path
from tkinter import ttk, messagebox, filedialog
from schedule_parser import update_item
from mail_content import readable_html, links_in, safe_url

from runtime_support import APP_DIR, data_dir, select_data_directory, start_tray
from mail_sync import sync_mail, merge_items, test_connection, error_message
BASE = APP_DIR
DATA = data_dir()
KEYWORDS = re.compile(r'笔试|测评|在线测试|能力测试|性格测试|assessment|online test|coding test', re.I)


class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(value, decrypt=False):
    buf = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    out = Blob()
    dll = ctypes.windll.crypt32
    if decrypt:
        ok = dll.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(out))
    else:
        ok = dll.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        ctypes.windll.kernel32.LocalFree(out.data)


def read(name, default):
    path = DATA / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def save(name, value):
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / name
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def mail_text(msg):
    parts = []
    for part in msg.walk():
        if part.get_content_disposition() == 'attachment':
            continue
        if part.get_content_type() not in ('text/plain', 'text/html'):
            continue
        payload = part.get_payload(decode=True) or b''
        try:
            text = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
        except LookupError:
            text = payload.decode('utf-8', errors='replace')
        if part.get_content_type() == 'text/html':
            text = readable_html(text)
        parts.append(text)
    return '\n'.join(parts)


def evidence(text):
    # Keep source text rather than guessing dates, years, timezones or exam windows.
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()]
    hits = [line for line in lines if re.search(r'截止|最晚|有效期|完成时间|考试时间|测评时间|deadline|due|within', line, re.I)]
    return '\n'.join(line[:450] for line in hits[:6]) or '未识别出截止说明，请打开原邮件核对。'


def fetch(config, cursor=None):
    password = protect(base64.b64decode(config['secret']), True).decode()
    return sync_mail(config['address'], password, cursor, DATA, mail_text, evidence)


class App:
    def __init__(self, root):
        self.root = root
        self.bg, self.ink, self.muted = '#F3F5F2', '#243B35', '#64736D'
        self.accent = '#286653'
        root.title('求职便利贴 · 笔试 / 测评')
        root.configure(bg=self.bg)
        root.option_add('*Font', ('Microsoft YaHei UI', 10))
        root.option_add('*Toplevel.background', self.bg)
        root.minsize(900, 650)
        self.ui = read('ui.json', {})
        root.geometry(self.ui.get('geometry', '980x780+70+70'))
        self.pinned = tk.BooleanVar(value=False)
        root.attributes('-topmost', self.pinned.get())
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.setup_style()
        self.config = read('config.json', {})
        self.state = read('tasks.json', {'items': [], 'last_sync': ''})
        self.items = self.state['items']
        changed = [update_item(item) for item in self.items]
        if any(changed):
            self.persist()
        self.events = queue.Queue()
        self.busy = False
        self.generation = 0
        self.failures = 0
        self.next_sync = 0
        self.last_tick = time.time()
        self.tray = None
        self.closed_windows = []
        self.connection_test = False

        shell = ttk.Frame(root, padding=(26, 22, 26, 18))
        shell.pack(fill='both', expand=True)
        header = ttk.Frame(shell); header.pack(fill='x')
        left = ttk.Frame(header); left.pack(side='left')
        ttk.Label(left, text='求职便利贴', style='Title.TLabel').pack(anchor='w')
        ttk.Label(left, text='把时间留给准备，把提醒交给这里。', style='Muted.TLabel').pack(anchor='w', pady=(5, 0))
        right = ttk.Frame(header); right.pack(side='right')
        ttk.Label(right, text=datetime.now().strftime('%m 月 %d 日') + '  ·  ' + '周' + '一二三四五六日'[datetime.now().weekday()], style='Date.TLabel').pack(anchor='e')
        ttk.Label(right, text='窗口可自由切换', style='Small.TLabel').pack(anchor='e', pady=(7, 0))

        cards = ttk.Frame(shell); cards.pack(fill='x', pady=(22, 18))
        self.stats = []
        for index, (label, color) in enumerate([('未完成事项', self.accent), ('24 小时内', '#AE6045'), ('时间待补充', '#8A733E')]):
            card = tk.Frame(cards, bg='#FFFFFF', highlightbackground='#E1E7E0', highlightthickness=1, padx=18, pady=12)
            card.pack(side='left', fill='both', expand=True, padx=(0 if index == 0 else 12, 0))
            number = tk.StringVar(value='0'); self.stats.append(number)
            tk.Label(card, textvariable=number, bg='#FFFFFF', fg=color, font=('Segoe UI', 23, 'bold')).pack(side='left')
            tk.Label(card, text=label, bg='#FFFFFF', fg=self.muted, font=('Microsoft YaHei UI', 10)).pack(side='left', padx=(14, 0))

        toolbar = ttk.Frame(shell); toolbar.pack(fill='x', pady=(0, 12))
        self.sync_button = ttk.Button(toolbar, text='立即更新', command=self.sync, style='Accent.TButton')
        self.sync_button.pack(side='left')
        for title, action in [('添加事项', self.add), ('邮箱设置', self.settings), ('打开邮箱', lambda: webbrowser.open('https://mail.qq.com/'))]:
            ttk.Button(toolbar, text=title, command=action).pack(side='left', padx=(8, 0))
        self.show_done = tk.BooleanVar()
        self.show_removed = tk.BooleanVar()
        ttk.Checkbutton(toolbar, text='含已移除', variable=self.show_removed, command=self.render).pack(side='right', padx=(12, 0))
        ttk.Checkbutton(toolbar, text='含已完成', variable=self.show_done, command=self.render).pack(side='right')

        table = tk.Frame(shell, bg='#FFFFFF', highlightbackground='#DEE5DE', highlightthickness=1)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('title', 'due', 'status'), show='headings', selectmode='browse')
        for key, title, width in [('title', '公司 / 邮件主题', 400), ('due', '开考 / 截止时间', 265), ('status', '状态', 120)]:
            self.tree.heading(key, text=title, anchor='w')
            self.tree.column(key, width=width, minwidth=100, stretch=key == 'title', anchor='w')
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill='both', expand=True)
        self.tree.tag_configure('even', background='#FFFFFF')
        self.tree.tag_configure('odd', background='#F7F9F6')
        self.tree.tag_configure('urgent', foreground='#9B4936', background='#FCF0EA')
        self.tree.tag_configure('unknown', foreground='#877036', background='#FAF7EB')
        self.tree.tag_configure('past', foreground='#7D8781')
        self.tree.tag_configure('inactive', foreground='#919A94')
        self.tree.bind('<Double-1>', lambda e: self.details())
        self.tree.bind('<Return>', lambda e: self.details())

        actions = ttk.Frame(shell); actions.pack(fill='x', pady=(12, 14))
        ttk.Button(actions, text='查看详情', command=self.details).pack(side='left')
        ttk.Button(actions, text='完成 / 恢复待办', command=self.toggle).pack(side='left', padx=8)
        ttk.Button(actions, text='移除 / 恢复显示', command=self.remove, style='Quiet.TButton').pack(side='left')
        ttk.Label(actions, text='双击事项，查看原文与时间依据', style='Muted.TLabel').pack(side='right')

        ttk.Separator(shell).pack(fill='x')
        self.status = tk.StringVar(value='请先设置邮箱，连接后将自动整理待办。' if not self.config else '上次更新 ' + (self.state.get('last_sync') or '尚未同步') + ' · 正在准备更新')
        ttk.Label(shell, textvariable=self.status, style='Muted.TLabel', wraplength=860, justify='left').pack(anchor='w', pady=(12, 5))
        foot = ttk.Frame(shell); foot.pack(fill='x')
        ttk.Label(foot, text='每 30 分钟更新 · 关闭收起到托盘', style='Small.TLabel').pack(side='left')
        ttk.Button(foot, text='数据与隐私', style='Link.TButton', command=self.privacy).pack(side='right', padx=8)
        ttk.Button(foot, text='退出', style='Link.TButton', command=self.quit).pack(side='right', padx=8)
        ttk.Button(foot, text='使用说明', style='Link.TButton', command=self.help).pack(side='right')
        self.render()
        root.after(200, self.poll)
        root.after(1200, self.sync)
        root.after(15000, self.periodic)

    def setup_style(self):
        from tkinter import font as tkfont
        style = ttk.Style(self.root)
        style.theme_use('clam')
        font = ('Microsoft YaHei UI', 10)
        row = tkfont.Font(self.root, font=font).metrics('linespace') + 20
        style.configure('.', background=self.bg, foreground=self.ink, font=font)
        style.configure('TFrame', background=self.bg)
        style.configure('TLabel', background=self.bg, foreground=self.ink)
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 22, 'bold'), foreground=self.ink)
        style.configure('Muted.TLabel', foreground=self.muted)
        style.configure('Small.TLabel', foreground='#75837C', font=('Microsoft YaHei UI', 9))
        style.configure('Date.TLabel', font=('Microsoft YaHei UI', 11), foreground=self.muted)
        style.configure('TButton', padding=(14, 9), background='#FFFFFF', bordercolor='#DCE3DC', lightcolor='#FFFFFF', darkcolor='#FFFFFF', relief='flat')
        style.map('TButton', background=[('active', '#E8EEE8'), ('pressed', '#DCE6DC')], bordercolor=[('focus', '#86A293')])
        style.configure('Accent.TButton', background=self.accent, foreground='#FFFFFF', bordercolor=self.accent, lightcolor=self.accent, darkcolor=self.accent)
        style.map('Accent.TButton', background=[('disabled', '#90A99C'), ('active', '#1E5343'), ('pressed', '#184437')], foreground=[('disabled', '#FFFFFF')])
        style.configure('Quiet.TButton', background=self.bg, foreground=self.muted)
        style.configure('Link.TButton', padding=(4, 2), background=self.bg, borderwidth=0, foreground=self.accent)
        style.configure('TCheckbutton', background=self.bg, foreground=self.muted, padding=(2, 4))
        style.map('TCheckbutton', background=[('active', self.bg)])
        style.configure('Treeview', background='#FFFFFF', fieldbackground='#FFFFFF', foreground=self.ink, rowheight=row, borderwidth=0, font=font)
        style.configure('Treeview.Heading', background='#EAF0E9', foreground='#50645A', padding=(12, 12), font=('Microsoft YaHei UI', 10, 'bold'), relief='flat')
        style.map('Treeview.Heading', background=[('active', '#E2EBE0')])
        style.map('Treeview', background=[('selected', '#D9EADF')], foreground=[('selected', '#173E2C')])
        style.configure('TEntry', padding=8, fieldbackground='#FFFFFF', bordercolor='#D6DFD5')
        style.configure('TCombobox', padding=7, fieldbackground='#FFFFFF')
        style.configure('Vertical.TScrollbar', background='#CBD6CC', troughcolor='#F5F7F3', borderwidth=0, arrowsize=12)

    def pin(self):
        # Compatibility for older saved preferences: no window is forced on top.
        self.pinned.set(False)
        self.root.attributes('-topmost', False)

    def dialog(self):
        win = tk.Toplevel(self.root)
        # No transient owner or grab: whichever window is clicked may come forward.
        win.attributes('-topmost', False)
        win.resizable(True, True)
        def activate():
            if win.winfo_exists():
                win.deiconify(); win.lift(); win.focus_force()
        win.after_idle(activate)
        return win

    def show(self):
        self.root.deiconify(); self.root.lift(); self.root.focus_force()
        for child in self.closed_windows:
            if child.winfo_exists(): child.deiconify()
        self.closed_windows = []

    def close(self):
        self.ui.update(geometry=self.root.geometry(), pinned=False)
        save('ui.json', self.ui)
        if self.tray is None:
            try:
                self.tray = start_tray(self.events)
            except Exception:
                self.root.iconify()
                self.status.set('托盘不可用，已最小化到任务栏，仍会继续更新。')
                return
        if not self.ui.get('tray_explained'):
            messagebox.showinfo('已收起到托盘', '程序会在右下角托盘继续运行。点击托盘图标可重新打开，右键菜单可退出。', parent=self.root)
            self.ui['tray_explained'] = True
            save('ui.json', self.ui)
        self.closed_windows = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel) and w.state() != 'withdrawn']
        for child in self.closed_windows: child.withdraw()
        self.root.withdraw()

    def quit(self):
        self.generation += 1
        self.ui.update(geometry=self.root.geometry(), pinned=False)
        save('ui.json', self.ui)
        if self.tray: self.tray.stop()
        self.root.destroy()

    def privacy(self):
        win = self.dialog(); win.title('数据与隐私'); win.geometry('680x500')
        box = ttk.Frame(win, padding=20); box.pack(fill='both', expand=True)
        ttk.Label(box, text='数据只存本机；收信只读，不删除邮箱邮件。', wraplength=620).pack(anchor='w', pady=8)
        ttk.Label(box, text='邮箱授权码由 Windows 加密保存。正文和原始邮件含私人信息，以明文保存在以下目录：', wraplength=620).pack(anchor='w', pady=8)
        path_label = ttk.Label(box, text=str(DATA), wraplength=620); path_label.pack(anchor='w', pady=8)
        ttk.Label(box, text='退出账号只移除本机授权码；清除缓存会删除邮件、手动事项和处理状态。移动数据会保留原目录作为备份，请自行保管。', wraplength=620).pack(anchor='w', pady=8)
        def idle():
            if self.busy or self.connection_test:
                messagebox.showinfo('请稍候', '请等待当前连接或同步结束再操作。', parent=win); return False
            return True
        def move():
            global DATA
            if not idle(): return
            folder = filedialog.askdirectory(title='选择空的数据文件夹', parent=win)
            if not folder: return
            try:
                DATA = select_data_directory(folder, DATA)
                path_label.configure(text=str(DATA))
            except (OSError, ValueError):
                messagebox.showerror('移动失败', '请选择可写入的独立空文件夹；原数据保留。', parent=win)
        def logout():
            if not idle(): return
            if messagebox.askyesno('退出账号', '移除本机邮箱授权信息，保留已有事项？', parent=win):
                self.generation += 1
                (DATA / 'config.json').unlink(missing_ok=True)
                self.config = {}
                self.status.set('已退出邮箱账号，已有事项保留。')
        def clear():
            if not idle(): return
            if not messagebox.askyesno('清除本地缓存', '删除本机所有事项、完成/移除状态和原始邮件？不会删除邮箱邮件。', parent=win): return
            self.items = []; self.state = {'items': [], 'last_sync': ''}
            self.persist()
            originals = DATA / 'originals'
            if originals.exists(): shutil.rmtree(originals)
            self.render()
            self.status.set('本地缓存已清空。下次更新将重新扫描收件箱。')
        def diagnostic():
            dest = filedialog.asksaveasfilename(parent=win, defaultextension='.json', initialfile='diagnostic.json')
            if dest:
                report = dict(version='0.2.0', python=sys.version.split()[0], platform=sys.platform,
                    item_count=len(self.items), configured=bool(self.config), sync_failures=self.failures,
                    last_success=self.state.get('last_sync', ''), tray_running=self.tray is not None)
                Path(dest).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        for label, action in [('打开数据目录', lambda: (DATA.mkdir(parents=True, exist_ok=True), os.startfile(DATA))),
                              ('更换数据目录', move), ('退出邮箱账号', logout), ('清除本地缓存', clear),
                              ('导出脱敏诊断（无正文、邮箱、链接、路径）', diagnostic)]:
            ttk.Button(box, text=label, command=action).pack(fill='x', pady=4)

    def help(self):
        messagebox.showinfo('使用说明', '开机登录 Windows 后，便利贴会自动打开并联网更新。\n运行中每 30 分钟更新一次；也可以点击“立即更新”。\n\n平时建议最小化，仍会继续同步。关闭窗口收起到托盘，点击底部“退出”才会结束程序。\n双击文件夹中的“求职便利贴”快捷方式即可重新打开。\n\n双击事项查看时间依据；移除只隐藏便利贴事项，不删除邮箱邮件。\n勾选“含已移除”可以恢复。\n\n扫描最近 90 天收件箱，不包含附件、垃圾箱或其他文件夹。', parent=self.root)

    def persist(self):
        self.state['items'] = self.items
        save('tasks.json', self.state)

    def render(self):
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        now = datetime.now()
        pending = [i for i in self.items if not i.get('done') and not i.get('removed')]
        soon = sum(bool(i.get('due')) and 0 <= (datetime.strptime(i['due'], '%Y-%m-%d %H:%M') - now).total_seconds() <= 86400 for i in pending)
        for variable, value in zip(self.stats, (len(pending), soon, sum(not i.get('due') for i in pending))):
            variable.set(str(value))
        def order(item):
            due = item.get('due', '')
            past = bool(due and due < now.strftime('%Y-%m-%d %H:%M'))
            return (item.get('removed', False), item['done'], 2 if past else (1 if not due else 0), due)
        for item in sorted(self.items, key=order):
            if item.get('removed') and not self.show_removed.get(): continue
            if item['done'] and not self.show_done.get() and not (item.get('removed') and self.show_removed.get()): continue
            kind = {'start':'开考', 'entry':'最晚进入', 'end':'结束'}.get(item.get('time_kind'), '截止')
            label, tag, status = '未识别到时间', 'unknown', '需补充'
            if item['due']:
                date_only = item.get('time_precision') == 'date'
                label = kind + ' ' + (item['due'][:10] + '（时刻未注明）' if date_only else item['due'])
                left = (datetime.strptime(item['due'], '%Y-%m-%d %H:%M') - now).total_seconds()
                tag = 'urgent' if left < 86400 else ''
                if left < 0:
                    status, tag = ('开考时间已过' if kind == '开考' else '已过期'), 'past'
                    if kind == '开考' and item.get('end') and item['end'] > now.strftime('%Y-%m-%d %H:%M'):
                        status, tag = '考试时段内', 'urgent'
                elif date_only: status = '日期临近' if left < 259200 else '待完成'
                elif left < 7200: status = '不足2小时'
                elif left < 86400: status = '不足1天'
                elif left < 259200: status = '3天内'
                else: status = '待参加' if kind == '开考' else '待完成'
            if item.get('needs_review') and not item.get('manual_time'): status += ' · 待核对'
            if item['done']: status, tag = '已完成', 'inactive'
            if item.get('removed'): status, tag = '已移除', 'inactive'
            stripe = 'even' if len(self.tree.get_children()) % 2 == 0 else 'odd'
            self.tree.insert('', 'end', iid=item['id'], values=(item['title'], label, status), tags=(stripe, tag))
        if selection and self.tree.exists(selection[0]):
            self.tree.selection_set(selection[0])

    def selected(self):
        sel = self.tree.selection()
        return next((i for i in self.items if sel and i['id'] == sel[0]), None)

    def toggle(self):
        item = self.selected()
        if item:
            item['done'] = not item['done']; self.persist(); self.render()

    def remove(self, item=None, window=None):
        item = item or self.selected()
        if item:
            item['removed'] = not item.get('removed', False)
            self.persist(); self.render()
            self.status.set('已从便利贴移除；不会删除邮箱邮件，后续同步不会重新添加。' if item['removed'] else '已恢复显示。')
            if window:
                window.destroy()

    def add(self):
        return self.manual_form()

    def manual_form(self, item=None):
        from urllib.parse import urlsplit
        new = item is None
        win = self.dialog()
        win.title('添加事项' if new else '编辑事项')
        win.configure(bg=self.bg)
        win.geometry('620x560')
        win.minsize(500, 380)
        value = item or {}
        # Reserve the button row first; only the form content scrolls.
        bottom = ttk.Frame(win, padding=(20, 12))
        bottom.pack(side='bottom', fill='x')
        area = ttk.Frame(win); area.pack(fill='both', expand=True)
        canvas = tk.Canvas(area, bg=self.bg, highlightthickness=0)
        scroll = ttk.Scrollbar(area, orient='vertical', command=canvas.yview)
        scroll.pack(side='right', fill='y'); canvas.pack(fill='both', expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        form = ttk.Frame(canvas, padding=20)
        inner = canvas.create_window(0, 0, window=form, anchor='nw')
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(inner, width=e.width))
        form.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        form.columnconfigure(0, weight=1)
        ttk.Label(form, text='补充微信、短信或其他渠道的安排', style='Muted.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 16))
        ttk.Label(form, text='事项名称 *').grid(row=1, column=0, sticky='w')
        title = ttk.Entry(form, name='title'); title.insert(0, value.get('title', ''))
        title.grid(row=2, column=0, sticky='ew', pady=(5, 14))
        ttk.Label(form, text='时间 *（本机时间）').grid(row=3, column=0, sticky='w')
        row = ttk.Frame(form); row.grid(row=4, column=0, sticky='ew', pady=(5, 4)); row.columnconfigure(1, weight=1)
        kind = ttk.Combobox(row, name='kind', values=('开考', '截止'), state='readonly', width=6)
        kind.set('开考' if value.get('time_kind') == 'start' else '截止'); kind.grid(row=0, column=0)
        due = ttk.Entry(row, name='due'); due.insert(0, value.get('due', '')); due.grid(row=0, column=1, sticky='ew', padx=(10, 0))
        ttk.Label(form, text='例如：2026-09-08 19:00', style='Small.TLabel').grid(row=5, column=0, sticky='w', pady=(0, 14))
        ttk.Label(form, text='链接（可选）').grid(row=6, column=0, sticky='w')
        link = ttk.Entry(form, name='link'); link.insert(0, value.get('link', '')); link.grid(row=7, column=0, sticky='ew', pady=(5, 14))
        ttk.Label(form, text='备注（可选）').grid(row=8, column=0, sticky='w')
        notes = tk.Text(form, name='notes', height=4, wrap='word', font=('Microsoft YaHei UI', 10), relief='flat', bg='#FFFFFF', fg=self.ink, padx=8, pady=8)
        notes.insert('1.0', value.get('notes', '')); notes.grid(row=9, column=0, sticky='ew', pady=(5, 0))
        def parsed_link():
            address = link.get().strip()
            parsed = urlsplit(address)
            if address and (parsed.scheme not in ('http', 'https') or not parsed.hostname or any(c.isspace() for c in address)):
                raise ValueError('链接请使用完整的 http:// 或 https:// 地址。')
            return address
        def commit():
            if not title.get().strip():
                messagebox.showerror('请填写名称', '请输入事项名称。', parent=win); title.focus_set(); return
            try:
                when = datetime.strptime(due.get().strip(), '%Y-%m-%d %H:%M').strftime('%Y-%m-%d %H:%M')
            except ValueError:
                messagebox.showerror('请填写时间', '请输入有效时间，例如 2026-09-08 19:00。', parent=win); due.focus_set(); return
            try:
                address = parsed_link()
            except ValueError as error:
                messagebox.showerror('链接格式', str(error), parent=win); return
            record = item if not new else dict(id=hashlib.sha256(str(datetime.now()).encode()).hexdigest(), sender='', received='', evidence='', text='', done=False)
            record.update(source='manual', title=title.get().strip(), due=when, time_kind='start' if kind.get() == '开考' else 'deadline', time_precision='minute', manual_time=True, link=address, notes=notes.get('1.0', 'end-1c').strip())
            if new: self.items.append(record)
            self.persist(); self.render(); win.destroy()
        def open_link():
            try:
                address = parsed_link()
                if address: webbrowser.open(address)
            except ValueError as error:
                messagebox.showerror('链接格式', str(error), parent=win)
        ttk.Button(bottom, name='save', text='保存事项', command=commit, style='Accent.TButton').pack(side='right')
        ttk.Button(bottom, text='取消', command=win.destroy).pack(side='right', padx=8)
        ttk.Button(bottom, text='打开链接', command=open_link).pack(side='left')
        title.focus_set()
        return win

    def details(self, item=None, new=False):
        if new: return self.manual_form()
        item = item or self.selected()
        if not item: return
        manual = item.get('source') == 'manual' or (not item.get('received') and not item.get('sender'))
        win = self.dialog()
        win.title('事项原文' if manual else '邮件正文')
        win.configure(bg=self.bg); win.geometry('900x740'); win.minsize(620, 450)
        bottom = ttk.Frame(win, padding=(16, 12)); bottom.pack(side='bottom', fill='x')
        def edit():
            win.destroy()
            return self.edit_details(item)
        ttk.Button(bottom, text='编辑事项' if manual else '修改标题 / 时间', command=edit).pack(side='left')
        ttk.Button(bottom, text='恢复显示' if item.get('removed') else '从便利贴移除', command=lambda: self.remove(item, win)).pack(side='left', padx=8)
        ttk.Button(bottom, text='关闭', command=win.destroy).pack(side='right')
        top = ttk.Frame(win, padding=(18, 14)); top.pack(fill='x')
        heading = ttk.Label(top, text=item['title'], font=('Microsoft YaHei UI', 13, 'bold'), wraplength=820)
        heading.pack(anchor='w')
        when = item.get('due') or '尚未填写时间'
        if item.get('time_precision') == 'date': when = when[:10] + '（具体时刻未注明）'
        ttk.Label(top, text=({'start':'开考：','entry':'最晚进入：','end':'结束：'}.get(item.get('time_kind'), '截止：')) + when, style='Muted.TLabel').pack(anchor='w', pady=(6, 0))
        if not manual:
            def export_original():
                name = Path(item.get('original', '')).name
                source = DATA / 'originals' / name
                if not name or not source.is_file():
                    messagebox.showinfo('原邮件尚未缓存', '点击主页面“立即更新”后再试；当前仍可查看已保存正文。', parent=win); return
                destination = filedialog.asksaveasfilename(parent=win, defaultextension='.eml', initialfile='original.eml', filetypes=[('原始邮件', '*.eml')])
                if destination: shutil.copyfile(source, destination)
            ttk.Button(bottom, text='导出原始邮件', command=export_original).pack(side='left', padx=8)
        content = ((item.get('notes') or '暂无备注。') + ('\n\n' + item['link'] if item.get('link') else '')) if manual else item.get('text', '')
        addresses = links_in(content)
        if addresses:
            ttk.Label(top, text='正文中的链接入口（请选择对应的笔试或测评链接）', style='Muted.TLabel').pack(anchor='w', pady=(12, 4))
            row = ttk.Frame(top); row.pack(fill='x')
            choice = ttk.Combobox(row, name='links', values=addresses, state='readonly')
            choice.current(0); choice.pack(side='left', fill='x', expand=True)
            ttk.Button(row, name='openlink', text='打开所选链接', style='Accent.TButton', command=lambda: webbrowser.open(choice.get()) if safe_url(choice.get()) else None).pack(side='right', padx=(8, 0))
        else:
            ttk.Label(top, text='此事项正文中没有可直接打开的网页链接。', style='Muted.TLabel').pack(anchor='w', pady=(10, 0))
        container = ttk.Frame(win, padding=(18, 0, 18, 0)); container.pack(fill='both', expand=True)
        text = tk.Text(container, name='body', wrap='word', font=('Microsoft YaHei UI', 11), bg='#FFFFFF', fg=self.ink, relief='flat', padx=16, pady=16, spacing3=6)
        scroll = ttk.Scrollbar(container, orient='vertical', command=text.yview)
        scroll.pack(side='right', fill='y'); text.pack(fill='both', expand=True); text.configure(yscrollcommand=scroll.set)
        if manual:
            prefix = '【手动添加 · 备注原文】\n\n'
        else:
            prefix = '发件人：' + item.get('sender', '') + '\n发送时间：' + item.get('received', '') + '\n\n【邮件正文 · 文字版】\n\n'
        full = prefix + content
        suffix = '' if manual else '\n\n【时间识别依据】\n' + (item.get('time_evidence') or item.get('evidence', '')) + '\n' + item.get('time_note', '')
        text.insert('1.0', full + suffix)
        for index, address in enumerate(addresses):
            tag = 'url_' + str(index)
            pos = 0
            while True:
                pos = full.find(address, pos)
                if pos < 0: break
                text.tag_add(tag, f'1.0+{pos}c', f'1.0+{pos+len(address)}c')
                pos += len(address)
            text.tag_configure(tag, foreground='#236B59', underline=True)
            text.tag_bind(tag, '<Button-1>', lambda e, url=address: webbrowser.open(url))
            text.tag_bind(tag, '<Enter>', lambda e: text.configure(cursor='hand2'))
            text.tag_bind(tag, '<Leave>', lambda e: text.configure(cursor='xterm'))
        text.configure(state='disabled')
        win.bind('<Configure>', lambda e: heading.configure(wraplength=max(300, win.winfo_width()-50)) if e.widget == win else None)
        return win

    def edit_details(self, item=None, new=False):
        item = item or self.selected()
        if not item: return
        if new or item.get('source') == 'manual' or (not item.get('received') and not item.get('sender')):
            return self.manual_form(None if new else item)
        win = self.dialog(); win.title('笔试 / 测评详情'); win.geometry('900x760'); win.configure(bg=self.bg); win.minsize(620, 480)
        bottom = ttk.Frame(win, padding=(14, 12))
        bottom.pack(side='bottom', fill='x')
        title = tk.StringVar(value=item['title']); due = tk.StringVar(value=item['due'])
        ttk.Label(win, text='公司 / 岗位 / 邮件主题').pack(anchor='w', padx=14, pady=(14, 3))
        ttk.Entry(win, textvariable=title).pack(fill='x', padx=14)
        def display_time(key):
            value = item.get(key, '')
            return (value[:10] + '（未注明具体时刻）') if item.get(key+'_precision') == 'date' else value
        summary = []
        if item.get('start'): summary.append('开考：' + display_time('start'))
        if item.get('deadline'): summary.append('截止 / 结束：' + display_time('deadline'))
        if item.get('manual_time'): summary.append('手动提醒时间：' + item['due'])
        ttk.Label(win, text='\n'.join(summary) or '正文暂未识别到时间', wraplength=740).pack(anchor='w', padx=14, pady=(12, 3))
        ttk.Label(win, text=item.get('time_note', '') or '已从邮件正文自动提取，无需手动填写。', wraplength=740).pack(anchor='w', padx=14, pady=3)
        editing = tk.BooleanVar(value=new)
        editrow = ttk.Frame(win)
        kind_var = tk.StringVar(value='开考' if item.get('time_kind') == 'start' else '截止')
        ttk.Combobox(editrow, textvariable=kind_var, values=('开考', '截止'), state='readonly', width=6).pack(side='left')
        ttk.Entry(editrow, name='due', textvariable=due).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Label(editrow, text='YYYY-MM-DD HH:MM').pack(side='left')
        def show_edit():
            if editing.get(): editrow.pack(fill='x', padx=14, after=edit_toggle)
            else: editrow.pack_forget()
        edit_toggle = ttk.Checkbutton(win, name='manual_time', text='手动修正（可选）', variable=editing, command=show_edit)
        edit_toggle.pack(anchor='w', padx=14, pady=5)
        show_edit()
        body = ttk.Frame(win)
        body.pack(fill='both', expand=True, padx=14, pady=12)
        scroll = ttk.Scrollbar(body, orient='vertical')
        scroll.pack(side='right', fill='y')
        text = tk.Text(body, height=4, yscrollcommand=scroll.set, wrap='word', font=('Microsoft YaHei UI', 11), bg='#FFFFFF', fg=self.ink, relief='flat', padx=18, pady=16, spacing1=3, spacing3=7, selectbackground='#D9EADF', highlightthickness=1, highlightbackground='#DEE5DE')
        text.pack(fill='both', expand=True)
        scroll.configure(command=text.yview)
        text.insert('1.0', '【时间识别依据】\n' + (item.get('time_evidence') or item['evidence']) + '\n\n【发件人】' + item['sender'] + '\n【邮件发送时间】' + item['received'] + '\n\n' + item['text'])
        text.configure(state='disabled')
        def commit():
            value = due.get().strip() if editing.get() else item['due']
            if editing.get() and value:
                try: value = datetime.strptime(value, '%Y-%m-%d %H:%M').strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    messagebox.showerror('时间格式', '请输入例如 2026-09-08 18:00', parent=win); return
            item.update(title=title.get().strip() or '未命名待办', due=value)
            if editing.get():
                item.update(manual_time=True, time_kind='start' if kind_var.get() == '开考' else 'deadline', time_precision='minute')
            if new: self.items.append(item)
            self.persist(); self.render(); win.destroy()
        ttk.Button(bottom, name='save', style='Accent.TButton', text='保存并关闭', command=commit).pack(side='left', padx=8)
        ttk.Button(bottom, text='取消', command=win.destroy).pack(side='right', padx=8)
        win.bind('<Control-Return>', lambda event: commit())
        if not new:
            ttk.Button(bottom, text='恢复显示' if item.get('removed') else '从便利贴移除', command=lambda: self.remove(item, win)).pack(side='left', padx=8)

        return win

    def settings(self):
        if self.busy or self.connection_test:
            messagebox.showinfo('请稍候', '正在同步邮件或测试连接，暂时不能修改邮箱配置。\n请等主页面恢复“立即更新”后再打开邮箱设置。\n期间仍可查看邮件、修改事项时间。', parent=self.root); return
        win = self.dialog(); win.title('连接 QQ 邮箱'); win.geometry('620x480')
        box = ttk.Frame(win, padding=20); box.pack(fill='both', expand=True)
        ttk.Label(box, text='首次连接：', font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(box, text='1. 打开 QQ 邮箱网页，在设置中开启 IMAP。\n2. 生成第三方客户端授权码（不是 QQ 密码）。\n3. 填入下方，先测试连接，成功后才保存。', wraplength=560).pack(anchor='w', pady=12)
        ttk.Label(box, text='只读取收件箱。授权码加密保存在本机；没有云端识别服务。', wraplength=560).pack(anchor='w', pady=8)
        address = tk.StringVar(value=self.config.get('address', '')); secret = tk.StringVar()
        ttk.Label(box, text='QQ / Foxmail 邮箱地址').pack(anchor='w')
        ttk.Entry(box, textvariable=address).pack(fill='x', pady=5)
        ttk.Label(box, text='IMAP 授权码').pack(anchor='w')
        ttk.Entry(box, textvariable=secret, show='•').pack(fill='x', pady=5)
        info = tk.StringVar(value='目前支持 QQ / Foxmail，其他邮箱尚未接入。')
        ttk.Label(box, textvariable=info, wraplength=560).pack(anchor='w', pady=8)
        def connect():
            addr, password = address.get().strip().lower(), secret.get().strip()
            if not re.fullmatch(r'[^\s@]+@(qq\.com|foxmail\.com)', addr, re.I) or not password:
                info.set('请输入正确的邮箱地址和授权码。'); return
            if self.busy or self.connection_test: return
            button.configure(state='disabled'); self.connection_test = True; info.set('正在测试安全连接…')
            def work():
                try:
                    test_connection(addr, password)
                    config = dict(address=addr, secret=base64.b64encode(protect(password.encode())).decode())
                    self.events.put(('connection', (win, button, info, config, None)))
                except Exception as error:
                    self.events.put(('connection', (win, button, info, None, error_message(error))))
            threading.Thread(target=work, daemon=True).start()
            secret.set('')
        button = ttk.Button(box, text='测试连接并保存', command=connect, style='Accent.TButton'); button.pack(pady=10)

    def sync(self):
        if not self.config or self.busy or self.connection_test: return
        self.busy = True
        self.sync_button.configure(text='正在更新…', state='disabled')
        self.status.set('正在检查新邮件… 最近成功：' + (self.state.get('last_sync') or '尚未成功'))
        config, cursor, generation = dict(self.config), dict(self.state.get('cursor', {})), self.generation
        def worker():
            try: self.events.put(('ok', (generation, fetch(config, cursor))))
            except Exception as error: self.events.put(('error', (generation, error_message(error))))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            kind, payload = self.events.get_nowait()
            if kind == 'show': self.show()
            elif kind == 'sync': self.sync()
            elif kind == 'quit':
                self.quit(); return
            elif kind == 'connection':
                self.connection_test = False
                win, button, info, config, error = payload
                if win.winfo_exists():
                    button.configure(state='normal')
                    if error: info.set(error)
                    else:
                        try:
                            save('config.json', config)
                            self.config = config; self.generation += 1
                            win.destroy(); self.sync()
                        except OSError:
                            info.set('连接成功，但本地配置保存失败；请检查数据目录权限。')
            else:
                generation, value = payload
                if generation == self.generation:
                    self.busy = False
                    self.sync_button.configure(text='立即更新', state='normal')
                    if kind == 'ok':
                        incoming, count, cursor = value
                        previous = copy.deepcopy(self.state)
                        try:
                            fresh = merge_items(self.items, incoming, update_item)
                            self.state['cursor'] = cursor
                            self.state['last_sync'] = datetime.now().strftime('%Y-%m-%d %H:%M')
                            self.persist()
                        except Exception:
                            self.state = previous; self.items = self.state['items']
                            self.failures += 1; self.next_sync = time.time() + 60
                            self.status.set('本地保存失败，进度未推进；稍后重试。')
                        else:
                            self.failures = 0; self.next_sync = time.time() + 1800
                            self.render()
                            self.status.set(f'更新于 {self.state["last_sync"]} · 本次读取 {count} 封 · 新增 {fresh} 条')
                    else:
                        self.failures += 1
                        delay = min(60 * 2**min(self.failures-1, 5), 1800)
                        self.next_sync = time.time() + delay
                        self.status.set(value + f' {delay//60} 分钟后重试；最近成功：' + (self.state.get('last_sync') or '尚未成功'))
        except queue.Empty: pass
        self.root.after(250, self.poll)

    def periodic(self):
        now = time.time()
        woke = now - self.last_tick > 90
        self.last_tick = now
        self.render()
        if woke or now >= self.next_sync: self.sync()
        self.root.after(15000, self.periodic)


if __name__ == '__main__':
    # Find only a real window on the current desktop. A process running on a
    # different desktop must not prevent the user from opening the application.
    import traceback
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        existing = user32.FindWindowW(None, '求职便利贴 · 笔试 / 测评')
        if existing:
            user32.ShowWindow(existing, 9)
            user32.SetForegroundWindow(existing)
        else:
            root = tk.Tk()
            app = App(root)
            root.update_idletasks()
            root.deiconify()
            root.lift()
            root.attributes('-topmost', False)
            if not app.config:
                root.after(1000, app.settings)
            root.mainloop()
    except Exception:
        report = DATA / 'startup-error.log'
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            report.write_text('启动失败：' + sys.exc_info()[0].__name__, encoding='utf-8')
        except OSError:
            pass
        ctypes.windll.user32.MessageBoxW(None, '便利贴启动失败。请查看程序目录中的 startup-error.log，或双击 启动便利贴.cmd 查看错误。', '求职便利贴', 16)
        raise
