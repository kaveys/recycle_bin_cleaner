# 垃圾站自清 (Recycle Bin Cleaner)

Windows 回收站自动清理工具。自动清理指定天数之前的回收站文件，直接解析 `$Recycle.Bin` 下的 `$I` 文件获取删除时间，超过阈值则删除对应的 `$R` / `$I` 文件。

## 功能特性
- 自动清理 N 天前的回收站文件（默认 30 天）
- 定时后台检查（默认每 6 小时）
- 开机自启动（注册表 Run 项）
- 系统托盘常驻 + GUI 主界面
- 实时统计回收站占用空间与可清理项

## 运行
```bash
pip install -r requirements.txt
python recycle_bin_cleaner.py
```

## 打包 (PyInstaller)
```bash
pyinstaller 垃圾站自清.spec
```

## 配置文件
首次运行后自动生成于 `~/.recycle_bin_cleaner.json`。
