# -*- coding: utf-8 -*-
"""
Windows 回收站自动清理工具
自动清理指定天数之前的回收站文件
直接解析 $Recycle.Bin 下的 $I 文件获取删除时间，超过阈值的删除对应的 $R / $I 文件
"""
import os
import sys
import ctypes
import struct
import json
import time
import string
import threading
import shutil
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime, timedelta
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem, Menu, Icon

# ====================== 全局配置 ======================
APP_NAME = "垃圾站自清"
APP_VERSION = "1.0.0"
CONFIG_FILE = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")),
                           ".recycle_bin_cleaner.json")

DEFAULT_CONFIG = {
    "days_threshold": 30,            # 清理多少天前的文件
    "auto_clean": True,              # 是否启用自动清理
    "check_interval_hours": 6,       # 自动检查间隔（小时）
    "run_at_startup": False,         # 开机自启动
    "last_clean_time": "",           # 上次清理时间
    "last_clean_count": 0,           # 上次清理数量
    "last_clean_size": 0,            # 上次清理释放空间
}

# FILETIME（1601-01-01 起的 100ns）到 Unix 时间戳的偏移量
_FILETIME_EPOCH_DIFF = 116444736000000000


def fmt_size(size):
    """字节大小格式化"""
    s = float(size) if size else 0.0
    for unit in ['B', 'KB', 'MB', 'GB']:
        if s < 1024:
            return f"{s:.1f} {unit}"
        s /= 1024
    return f"{s:.1f} TB"


# ====================== 清理核心 ======================
class RecycleBinCleaner:
    def __init__(self):
        self.config = self._load_config()
        self._lock = threading.Lock()

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                merged = DEFAULT_CONFIG.copy()
                merged.update(cfg)
                return merged
            except Exception:
                pass
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        try:
            with self._lock:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- 回收站枚举 ----------
    @staticmethod
    def get_recycle_bin_paths():
        """枚举所有盘符下的 $Recycle.Bin 路径"""
        paths = []
        for letter in string.ascii_uppercase:
            drive = letter + ':'
            if not os.path.exists(drive + os.sep):
                continue
            bin_path = os.path.join(drive + os.sep, '$Recycle.Bin')
            if os.path.isdir(bin_path):
                paths.append(bin_path)
        return paths

    @staticmethod
    def _parse_i_file(path):
        """解析 $I 文件，返回 {delete_time, original_path, file_size}"""
        try:
            with open(path, 'rb') as f:
                data = f.read()
            if len(data) < 24:
                return None
            version = struct.unpack_from('<Q', data, 0)[0]
            if version == 2:
                # v2: version(8) + size(8) + time(8) + pathlen(8) + path(UTF-16LE)
                if len(data) < 32:
                    return None
                file_size = struct.unpack_from('<Q', data, 8)[0]
                delete_ft = struct.unpack_from('<Q', data, 16)[0]
                path_str = data[32:].decode('utf-16-le', errors='ignore').rstrip('\x00')
            elif version == 1:
                # v1: version(8) + size(4) + time(8) + pathlen(4) + path(UTF-16LE)
                file_size = struct.unpack_from('<I', data, 8)[0]
                delete_ft = struct.unpack_from('<Q', data, 12)[0]
                path_str = data[24:].decode('utf-16-le', errors='ignore').rstrip('\x00')
            else:
                return None
            try:
                unix_time = (delete_ft - _FILETIME_EPOCH_DIFF) / 1e7
                dt = datetime.fromtimestamp(unix_time)
            except Exception:
                return None
            return {
                'delete_time': dt,
                'original_path': path_str,
                'file_size': file_size,
            }
        except Exception:
            return None

    def get_recycle_bin_items(self):
        """枚举回收站中所有项目"""
        items = []
        for bin_path in self.get_recycle_bin_paths():
            try:
                for entry in os.scandir(bin_path):
                    if not entry.is_dir():
                        continue
                    user_bin = entry.path
                    try:
                        for f in os.scandir(user_bin):
                            name = f.name
                            if name.startswith('$I') and len(name) > 2:
                                I_path = f.path
                                R_path = os.path.join(user_bin, '$R' + name[2:])
                                info = self._parse_i_file(I_path)
                                if info:
                                    items.append({
                                        'R_path': R_path,
                                        'I_path': I_path,
                                        'delete_time': info['delete_time'],
                                        'original_path': info['original_path'],
                                        'file_size': info['file_size'],
                                    })
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue
        return items

    @staticmethod
    def _delete_item(item):
        """删除一个回收站项目（包括 $R 与 $I）"""
        for path in [item['R_path'], item['I_path']]:
            try:
                if not os.path.exists(path):
                    continue
                try:
                    os.chmod(path, 0o777)
                except Exception:
                    pass
                if os.path.isfile(path) or os.path.islink(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass

    def clean_old_items(self, days_threshold=None, log_callback=None):
        """清理超过指定天数的回收站项目，返回 (count, total_size)"""
        if days_threshold is None:
            days_threshold = self.config['days_threshold']
        threshold = datetime.now() - timedelta(days=days_threshold)
        items = self.get_recycle_bin_items()
        cleaned = 0
        total_size = 0
        for item in items:
            if item['delete_time'] < threshold:
                self._delete_item(item)
                cleaned += 1
                total_size += item['file_size']
                if log_callback:
                    log_callback(f"  删除: {item['original_path']}  ({fmt_size(item['file_size'])})")
        self.config['last_clean_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.config['last_clean_count'] = cleaned
        self.config['last_clean_size'] = total_size
        self.save_config()
        return cleaned, total_size

    def get_stats(self):
        """统计回收站状态"""
        items = self.get_recycle_bin_items()
        threshold = datetime.now() - timedelta(days=self.config['days_threshold'])
        total = len(items)
        old = sum(1 for i in items if i['delete_time'] < threshold)
        total_size = sum(i['file_size'] for i in items)
        old_size = sum(i['file_size'] for i in items if i['delete_time'] < threshold)
        return {
            'total': total,
            'old': old,
            'total_size': total_size,
            'old_size': old_size,
        }


# ====================== 开机自启 ======================
def set_startup(enable):
    """写入 / 删除 Run 注册表项以实现开机自启"""
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        exe_path = sys.executable if getattr(sys, 'frozen', False) else \
                   f'"{sys.executable}" "{os.path.abspath(__file__)}"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


# ====================== 应用主控 ======================
class CleanerApp:
    def __init__(self):
        self.cleaner = RecycleBinCleaner()
        self.gui = None
        self.tray_icon = None
        self.running = True
        self._lock = threading.Lock()
        self._last_check_ts = 0
        self._create_tray_icon()

    # ---------- 托盘 ----------
    def _create_tray_icon(self):
        self.tray_icon = Icon(
            APP_NAME,
            self._create_icon_image(),
            APP_NAME,
            menu=Menu(
                MenuItem("打开主界面", self._tray_show, default=True),
                MenuItem("立即清理", self._tray_clean_now),
                MenuItem(self._auto_menu_label, self._tray_toggle_auto),
                Menu.SEPARATOR,
                MenuItem("退出", self._tray_quit),
            )
        )

    @staticmethod
    def _create_icon_image():
        size = 64
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(64, 156, 255, 255))
        d.rounded_rectangle([20, 24, 44, 50], radius=2, fill=(255, 255, 255, 255))
        d.rounded_rectangle([16, 18, 48, 23], radius=2, fill=(255, 255, 255, 255))
        d.rounded_rectangle([28, 14, 36, 18], radius=1, fill=(255, 255, 255, 255))
        for x in [26, 32, 38]:
            d.line([(x, 28), (x, 46)], fill=(64, 156, 255, 255), width=2)
        return img

    def _auto_menu_label(self, item=None):
        return "暂停自动清理" if self.cleaner.config['auto_clean'] else "启用自动清理"

    def _tray_show(self, icon=None, item=None):
        if self.gui:
            self.gui.show()

    def _tray_clean_now(self, icon=None, item=None):
        self.clean_now_async()

    def _tray_toggle_auto(self, icon=None, item=None):
        with self._lock:
            self.cleaner.config['auto_clean'] = not self.cleaner.config['auto_clean']
            self.cleaner.save_config()
        self._last_check_ts = 0  # 立刻触发一次
        if self.gui:
            self.gui.safe_call(self.gui.update_auto_state)
        if self.tray_icon:
            self.tray_icon.update_menu()

    def _tray_quit(self, icon=None, item=None):
        self.running = False
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        if self.gui:
            self.gui.safe_call(self.gui.destroy)

    # ---------- 清理动作 ----------
    def clean_now_async(self):
        threading.Thread(target=self._do_clean_now, daemon=True).start()

    def _do_clean_now(self):
        if self.gui:
            self.gui.safe_log("开始立即清理...")
        cleaned, size = self.cleaner.clean_old_items(
            log_callback=(self.gui.safe_log if self.gui else None)
        )
        if self.gui:
            self.gui.safe_log(f"清理完成: {cleaned} 个项目, 释放 {fmt_size(size)}")
            self.gui.safe_call(self.gui.refresh_stats)

    # ---------- 后台循环 ----------
    def _background_loop(self):
        while self.running:
            try:
                if self.cleaner.config['auto_clean']:
                    interval = max(0.5, float(self.cleaner.config['check_interval_hours'])) * 3600
                    if time.time() - self._last_check_ts >= interval:
                        self._last_check_ts = time.time()
                        cleaned, size = self.cleaner.clean_old_items()
                        if cleaned > 0 and self.gui:
                            self.gui.safe_log(
                                f"自动清理: {cleaned} 个项目, 释放 {fmt_size(size)}"
                            )
            except Exception as e:
                if self.gui:
                    self.gui.safe_log(f"自动清理出错: {e}")
            time.sleep(30)

    # ---------- 启动 ----------
    def start(self):
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()
        bg_thread = threading.Thread(target=self._background_loop, daemon=True)
        bg_thread.start()
        self.gui = AppGUI(self)
        self.gui.run()


# ====================== GUI ======================
class AppGUI:
    def __init__(self, app):
        self.app = app
        self.cleaner = app.cleaner
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME}  v{APP_VERSION}")
        self.root.geometry("620x680")
        self.root.minsize(540, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._busy = False
        self._setup_style()
        self._build_ui()
        self.refresh_stats()
        self.update_auto_state()
        self._maybe_run_startup_check()

    # ---------- 样式 ----------
    def _setup_style(self):
        self.bg = '#f0f2f5'
        self.card_bg = '#ffffff'
        self.accent = '#1a73e8'
        self.danger = '#ea4335'
        self.text_main = '#202124'
        self.text_sub = '#5f6368'
        self.root.configure(bg=self.bg)

        style = ttk.Style(self.root)
        try:
            style.theme_use('vista')
        except Exception:
            pass

        style.configure('TFrame', background=self.bg)
        style.configure('Card.TFrame', background=self.card_bg)
        style.configure('Header.TLabel', font=('Microsoft YaHei UI', 18, 'bold'),
                        foreground=self.accent, background=self.bg)
        style.configure('Subhead.TLabel', font=('Microsoft YaHei UI', 10),
                        foreground=self.text_sub, background=self.bg)
        style.configure('CardTitle.TLabel', font=('Microsoft YaHei UI', 11, 'bold'),
                        foreground=self.text_main, background=self.card_bg)
        style.configure('CardKey.TLabel', font=('Microsoft YaHei UI', 10),
                        foreground=self.text_sub, background=self.card_bg)
        style.configure('StatValue.TLabel', font=('Microsoft YaHei UI', 16, 'bold'),
                        foreground=self.accent, background=self.card_bg)
        style.configure('StatValueRed.TLabel', font=('Microsoft YaHei UI', 16, 'bold'),
                        foreground=self.danger, background=self.card_bg)
        style.configure('StatHint.TLabel', font=('Microsoft YaHei UI', 9),
                        foreground=self.text_sub, background=self.card_bg)
        style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=6)
        style.configure('Primary.TButton', font=('Microsoft YaHei UI', 10, 'bold'),
                        foreground='white', background=self.accent)
        style.configure('Danger.TButton', font=('Microsoft YaHei UI', 10, 'bold'),
                        foreground='white', background=self.danger)
        style.configure('TCheckbutton', font=('Microsoft YaHei UI', 10),
                        background=self.card_bg)

    # ---------- 卡片 ----------
    def _card(self, parent, padx=14, pady=12):
        card = ttk.Frame(parent, style='Card.TFrame')
        # 内层填充
        inner = tk.Frame(card, bg=self.card_bg)
        inner.pack(fill='both', expand=True, padx=padx, pady=pady)
        return card, inner

    # ---------- UI 构建 ----------
    def _build_ui(self):
        root = self.root
        # 顶部标题区
        header = tk.Frame(root, bg=self.bg)
        header.pack(fill='x', padx=20, pady=(16, 6))
        tk.Label(header, text=APP_NAME, font=('Microsoft YaHei UI', 18, 'bold'),
                 fg=self.accent, bg=self.bg).pack(side='left')
        tk.Label(header, text=f"v{APP_VERSION}  ·  自动清理回收站旧文件",
                 font=('Microsoft YaHei UI', 9), fg=self.text_sub,
                 bg=self.bg).pack(side='left', padx=10, pady=(8, 0))

        # 卡片1：状态
        c1, c1_inner = self._card(root, padx=16, pady=14)
        c1.pack(fill='x', padx=16, pady=(10, 8))
        tk.Label(c1_inner, text="回收站状态",
                 font=('Microsoft YaHei UI', 11, 'bold'),
                 fg=self.text_main, bg=self.card_bg).pack(anchor='w')
        # 三栏统计
        stat_row = tk.Frame(c1_inner, bg=self.card_bg)
        stat_row.pack(fill='x', pady=(10, 4))
        self.lbl_total = self._stat_block(stat_row, "项目总数", "0")
        self.lbl_total_size = self._stat_block(stat_row, "占用空间", "0 B")
        self.lbl_old = self._stat_block(stat_row, "可清理项目", "0", danger=True)
        self.lbl_old_size = self._stat_block(stat_row, "可释放空间", "0 B", danger=True)

        # 上次清理信息
        last_row = tk.Frame(c1_inner, bg=self.card_bg)
        last_row.pack(fill='x', pady=(8, 0))
        self.lbl_last = tk.Label(last_row, text="上次清理：尚未运行",
                                font=('Microsoft YaHei UI', 9),
                                fg=self.text_sub, bg=self.card_bg, anchor='w')
        self.lbl_last.pack(side='left')
        refresh_btn = tk.Button(last_row, text="刷新", command=self.refresh_stats,
                                font=('Microsoft YaHei UI', 9), bd=0,
                                fg=self.accent, bg=self.card_bg,
                                activebackground=self.card_bg,
                                activeforeground=self.accent, cursor='hand2')
        refresh_btn.pack(side='right')

        # 卡片2：清理设置
        c2, c2_inner = self._card(root, padx=16, pady=14)
        c2.pack(fill='x', padx=16, pady=8)
        tk.Label(c2_inner, text="清理设置",
                 font=('Microsoft YaHei UI', 11, 'bold'),
                 fg=self.text_main, bg=self.card_bg).pack(anchor='w')

        set_row1 = tk.Frame(c2_inner, bg=self.card_bg)
        set_row1.pack(fill='x', pady=(10, 6))
        tk.Label(set_row1, text="清理超过", font=('Microsoft YaHei UI', 10),
                 fg=self.text_sub, bg=self.card_bg).pack(side='left')
        self.spin_days = tk.Spinbox(set_row1, from_=1, to=365, width=5,
                                   font=('Microsoft YaHei UI', 10),
                                   justify='center')
        self.spin_days.pack(side='left', padx=8)
        tk.Label(set_row1, text="天的项目", font=('Microsoft YaHei UI', 10),
                 fg=self.text_sub, bg=self.card_bg).pack(side='left')

        set_row2 = tk.Frame(c2_inner, bg=self.card_bg)
        set_row2.pack(fill='x', pady=(0, 6))
        tk.Label(set_row2, text="检查间隔", font=('Microsoft YaHei UI', 10),
                 fg=self.text_sub, bg=self.card_bg).pack(side='left')
        self.spin_interval = tk.Spinbox(set_row2, from_=0.5, to=24, increment=0.5,
                                       width=5, font=('Microsoft YaHei UI', 10),
                                       justify='center')
        self.spin_interval.pack(side='left', padx=8)
        tk.Label(set_row2, text="小时", font=('Microsoft YaHei UI', 10),
                 fg=self.text_sub, bg=self.card_bg).pack(side='left')

        # 复选框：自动清理 / 开机启动
        chk_row = tk.Frame(c2_inner, bg=self.card_bg)
        chk_row.pack(fill='x', pady=(6, 0))
        self.var_auto = tk.BooleanVar(value=self.cleaner.config['auto_clean'])
        cb_auto = tk.Checkbutton(chk_row, text="启用自动清理", variable=self.var_auto,
                                 command=self.on_toggle_auto,
                                 font=('Microsoft YaHei UI', 10),
                                 bg=self.card_bg, activebackground=self.card_bg,
                                 fg=self.text_main, selectcolor=self.card_bg)
        cb_auto.pack(side='left')
        self.var_startup = tk.BooleanVar(value=self.cleaner.config['run_at_startup'])
        cb_startup = tk.Checkbutton(chk_row, text="开机自启动",
                                    variable=self.var_startup,
                                    command=self.on_toggle_startup,
                                    font=('Microsoft YaHei UI', 10),
                                    bg=self.card_bg, activebackground=self.card_bg,
                                    fg=self.text_main, selectcolor=self.card_bg)
        cb_startup.pack(side='left', padx=24)

        # 保存配置按钮
        save_row = tk.Frame(c2_inner, bg=self.card_bg)
        save_row.pack(fill='x', pady=(10, 0))
        tk.Button(save_row, text="保存设置", command=self.on_save_config,
                  font=('Microsoft YaHei UI', 10), bd=0, height=1,
                  fg=self.accent, bg='#e8f0fe', activebackground='#d2e3fc',
                  cursor='hand2', padx=14).pack(side='left')

        # 卡片3：操作按钮
        c3, c3_inner = self._card(root, padx=16, pady=12)
        c3.pack(fill='x', padx=16, pady=8)
        btn_row = tk.Frame(c3_inner, bg=self.card_bg)
        btn_row.pack(fill='x')
        tk.Button(btn_row, text="立即清理", command=self.on_clean_now,
                  font=('Microsoft YaHei UI', 11, 'bold'), height=1,
                  fg='white', bg=self.accent, activebackground='#1765cc',
                  bd=0, cursor='hand2', padx=18).pack(side='left')
        tk.Button(btn_row, text="隐藏到托盘", command=self.on_close,
                  font=('Microsoft YaHei UI', 10), height=1,
                  fg=self.text_main, bg='#e8eaed', activebackground='#dadce0',
                  bd=0, cursor='hand2', padx=14).pack(side='left', padx=10)

        # 卡片4：日志
        c4, c4_inner = self._card(root, padx=12, pady=10)
        c4.pack(fill='both', expand=True, padx=16, pady=(8, 16))
        tk.Label(c4_inner, text="运行日志",
                 font=('Microsoft YaHei UI', 11, 'bold'),
                 fg=self.text_main, bg=self.card_bg).pack(anchor='w', pady=(2, 4))
        self.txt_log = scrolledtext.ScrolledText(
            c4_inner, height=8, font=('Consolas', 9), bd=0,
            bg='#f8f9fa', fg=self.text_main, wrap='word', state='disabled'
        )
        self.txt_log.pack(fill='both', expand=True)

        # 初始值
        self.spin_days.delete(0, 'end')
        self.spin_days.insert(0, str(self.cleaner.config['days_threshold']))
        self.spin_interval.delete(0, 'end')
        self.spin_interval.insert(0, str(self.cleaner.config['check_interval_hours']))

        self.log(f"{APP_NAME} 已启动。配置文件: {CONFIG_FILE}")

    def _stat_block(self, parent, label, value, danger=False):
        block = tk.Frame(parent, bg=self.card_bg)
        block.pack(side='left', expand=True, fill='x')
        tk.Label(block, text=label, font=('Microsoft YaHei UI', 9),
                 fg=self.text_sub, bg=self.card_bg).pack(anchor='w')
        style_name = 'StatValueRed.TLabel' if danger else 'StatValue.TLabel'
        lbl = ttk.Label(block, text=value, style=style_name)
        lbl.pack(anchor='w', pady=(2, 0))
        return lbl

    # ---------- 业务 ----------
    def _maybe_run_startup_check(self):
        # 启动后稍延迟刷新一次状态（异步）
        self.root.after(800, self.refresh_stats)

    def refresh_stats(self):
        """刷新统计（异步线程 + 主线程更新）"""
        def work():
            try:
                stats = self.cleaner.get_stats()
                self.safe_call(lambda: self._apply_stats(stats))
            except Exception as e:
                self.safe_log(f"统计出错: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _apply_stats(self, stats):
        try:
            self.lbl_total.config(text=str(stats['total']))
            self.lbl_total_size.config(text=fmt_size(stats['total_size']))
            self.lbl_old.config(text=str(stats['old']))
            self.lbl_old_size.config(text=fmt_size(stats['old_size']))
        except Exception:
            pass
        # 上次清理信息
        last = self.cleaner.config.get('last_clean_time', '')
        cnt = self.cleaner.config.get('last_clean_count', 0)
        sz = self.cleaner.config.get('last_clean_size', 0)
        if last:
            self.lbl_last.config(
                text=f"上次清理：{last}  ·  {cnt} 项  ·  释放 {fmt_size(sz)}"
            )

    def update_auto_state(self):
        try:
            self.var_auto.set(self.cleaner.config['auto_clean'])
        except Exception:
            pass

    # ---------- 回调 ----------
    def on_save_config(self):
        try:
            days = int(self.spin_days.get())
            if days < 1:
                days = 1
            elif days > 365:
                days = 365
        except Exception:
            self.log("天数输入无效")
            return
        try:
            interval = float(self.spin_interval.get())
            if interval < 0.5:
                interval = 0.5
        except Exception:
            self.log("间隔输入无效")
            return
        self.cleaner.config['days_threshold'] = days
        self.cleaner.config['check_interval_hours'] = interval
        self.cleaner.save_config()
        self.log(f"设置已保存: 清理 {days} 天前 / 每 {interval} 小时检查")
        self.refresh_stats()

    def on_clean_now(self):
        if self._busy:
            self.log("正在清理中，请稍候...")
            return
        self._busy = True
        self.log("开始立即清理...")
        self.app.clean_now_async()
        # 解锁
        self.root.after(3000, lambda: setattr(self, '_busy', False))

    def on_toggle_auto(self):
        self.cleaner.config['auto_clean'] = bool(self.var_auto.get())
        self.cleaner.save_config()
        self.app._last_check_ts = 0
        self.log(f"自动清理: {'已启用' if self.cleaner.config['auto_clean'] else '已暂停'}")

    def on_toggle_startup(self):
        enable = bool(self.var_startup.get())
        ok = set_startup(enable)
        if ok:
            self.cleaner.config['run_at_startup'] = enable
            self.cleaner.save_config()
            self.log(f"开机自启: {'已开启' if enable else '已关闭'}")
        else:
            self.var_startup.set(not enable)
            self.log("设置开机自启失败（可能需要权限）")

    def on_close(self):
        """关闭窗口 -> 隐藏到托盘"""
        try:
            self.root.withdraw()
        except Exception:
            pass

    def show(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------- 线程安全的日志/调用 ----------
    def safe_call(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def safe_log(self, msg):
        self.safe_call(lambda: self.log(msg))

    def log(self, msg):
        ts = datetime.now().strftime('%H:%M:%S')
        try:
            self.txt_log.config(state='normal')
            self.txt_log.insert('end', f"[{ts}] {msg}\n")
            self.txt_log.see('end')
            self.txt_log.config(state='disabled')
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


# ====================== 高 DPI 适配 ======================
def enable_high_dpi():
    """启用 DPI 感知，避免在高分辨率/缩放屏上界面发虚模糊"""
    if sys.platform != 'win32':
        return
    try:
        # system-aware：Tk 8.6 会按系统 DPI 缩放字体，并在原生分辨率渲染，清晰且尺寸正确
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        # 兜底：老 API，仅标记 DPI 感知
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ====================== 入口 ======================
def main():
    enable_high_dpi()
    app = CleanerApp()
    app.start()


if __name__ == '__main__':
    main()
