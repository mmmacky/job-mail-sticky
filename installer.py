# -*- coding: utf-8 -*-
"""Per-user installer; no administrator privileges or Python installation required."""
import os
import sys
import json
import zipfile
import shutil
import threading
import queue
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

def install_payload(payload, folder, data):
    folder, data = Path(folder).resolve(), Path(data).resolve()
    if folder == data or folder in data.parents or data in folder.parents:
        raise ValueError('程序和数据目录必须是两个独立文件夹。')
    # Never replace a running executable or unrelated directory.
    if folder.exists() and any(folder.iterdir()) and not (folder / 'JobMailSticky.exe').exists():
        raise ValueError('安装目录非空且不是本程序，请另选文件夹。')
    pointer = folder / 'data-location.json'
    if pointer.exists():
        recorded = Path(json.loads(pointer.read_text(encoding='utf-8-sig'))['path']).resolve()
        if recorded != data:
            raise ValueError('升级请保持原数据目录；更换数据目录请在程序内操作。')
    folder.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(payload) as archive:
        for name in archive.namelist():
            dest = (folder / name).resolve()
            if folder not in dest.parents:
                raise ValueError('安装包文件路径无效。')
        archive.extractall(folder)
    temp = folder / 'data-location.tmp'
    temp.write_text(json.dumps({'path': str(data)}, ensure_ascii=False), encoding='utf-8')
    temp.replace(pointer)

def shortcuts(folder, desktop, startup):
    env = os.environ.copy()
    env['JMS_INSTALL_DIR'] = str(folder)
    env['JMS_DESKTOP'] = '1' if desktop else '0'
    env['JMS_STARTUP'] = '1' if startup else '0'
    # Paths are passed as environment values, never inserted into shell code.
    script = """
$ws = New-Object -ComObject WScript.Shell
$destinations = @()
if ($env:JMS_DESKTOP -eq '1') { $destinations += [Environment]::GetFolderPath('Desktop') }
if ($env:JMS_STARTUP -eq '1') { $destinations += [Environment]::GetFolderPath('Startup') }
foreach ($d in $destinations) {
    if ([string]::IsNullOrWhiteSpace($d)) { throw 'Desktop path unavailable' }
    $link = $ws.CreateShortcut((Join-Path $d '求职便利贴.lnk'))
    $link.TargetPath = Join-Path $env:JMS_INSTALL_DIR 'JobMailSticky.exe'
    $link.WorkingDirectory = $env:JMS_INSTALL_DIR
    $link.Save()
}
"""
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env=env, check=True, capture_output=True, creationflags=0x08000000)

def main():
    root=tk.Tk(); root.title('安装求职便利贴 0.2'); root.geometry('680x530'); root.minsize(620,500)
    root.option_add('*Font', ('Microsoft YaHei UI',10))
    panel=ttk.Frame(root,padding=24); panel.pack(fill='both',expand=True)
    ttk.Label(panel,text='选择安装位置与数据位置',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w',pady=(0,15))
    local=Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
    program=tk.StringVar(value=str(local/'Programs'/'JobMailSticky'))
    data=tk.StringVar(value=str(local/'JobMailSticky'))
    for title,var in [('程序目录（可选择 E 盘）',program),('数据目录（邮箱与邮件保存在这里）',data)]:
        ttk.Label(panel,text=title).pack(anchor='w',pady=(8,3))
        row=ttk.Frame(panel);row.pack(fill='x')
        ttk.Entry(row,textvariable=var).pack(side='left',fill='x',expand=True)
        def choose(v=var):
            result=filedialog.askdirectory(parent=root,title='选择目录')
            if result:v.set(str(Path(result)/('JobMailSticky' if v is program else 'JobMailStickyData')))
        ttk.Button(row,text='选择…',command=choose).pack(side='right',padx=(8,0))
    ttk.Label(panel,text='升级会保留原数据。程序目录与数据目录请分开。\n旧版用户可把“数据目录”直接选为原来的 data 文件夹。',wraplength=600).pack(anchor='w',pady=12)
    desktop=tk.BooleanVar(value=True); startup=tk.BooleanVar(value=True)
    ttk.Checkbutton(panel,text='创建桌面快捷方式',variable=desktop).pack(anchor='w')
    ttk.Checkbutton(panel,text='登录 Windows 后自动打开并更新',variable=startup).pack(anchor='w')
    ttk.Label(panel,text='无云端识别、无主动弹窗通知。原始邮件和链接只存本机。\n首次运行需自行连接 QQ 邮箱。安装包未包含任何个人邮件。',wraplength=600).pack(anchor='w',pady=12)
    status=tk.StringVar();ttk.Label(panel,textvariable=status,wraplength=600).pack(anchor='w')
    events=queue.Queue()
    def install():
        folder=program.get().strip(); storage=data.get().strip()
        if not folder or not storage:return
        desk,start=desktop.get(),startup.get()
        button.configure(state='disabled');status.set('正在安装，请稍候…')
        def work():
            try:
                base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
                install_payload(base/'payload.zip',folder,storage)
                try:shortcuts(folder,desk,start)
                except Exception: events.put(('partial',folder));return
                events.put(('ok',folder))
            except Exception as error:
                events.put(('error',str(error) if isinstance(error,ValueError) else '安装失败，请关闭旧程序并确认目标文件夹可写。'))
        threading.Thread(target=work,daemon=True).start()
    button=ttk.Button(panel,text='安装',command=install);button.pack(anchor='e',pady=12)
    def poll():
        try:
            kind,value=events.get_nowait()
            button.configure(state='normal')
            if kind=='error':status.set(value)
            else:
                status.set('安装完成。' if kind=='ok' else '程序已安装，快捷方式创建失败；请从程序目录打开 JobMailSticky.exe。')
                if messagebox.askyesno('安装完成','现在打开便利贴？',parent=root):
                    subprocess.Popen([str(Path(value)/'JobMailSticky.exe')],cwd=value,creationflags=0x08000000)
                root.destroy();return
        except queue.Empty:pass
        root.after(200,poll)
    root.after(200,poll);root.mainloop()

if __name__=='__main__':main()

