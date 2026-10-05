# 垃圾站自清 (Recycle Bin Cleaner)

> Windows 回收站自动清理工具 —— 自动清理指定天数之前的回收站文件

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 📖 项目简介

垃圾站自清是一款轻量级的 Windows 桌面工具，能够在后台自动扫描并清理回收站中超过指定天数的文件。工具直接解析 `$Recycle.Bin` 下的 `$I` 元数据文件获取删除时间，精准定位过期文件并删除对应的 `$R`（数据）与 `$I`（索引）文件。

## ✨ 功能特性

- **定时自动清理**：按设定间隔（默认 6 小时）后台扫描，清理超过阈值（默认 30 天）的回收站文件
- **系统托盘常驻**：最小化到托盘，不占用任务栏，右键菜单快速操作
- **图形化界面**：基于 Tkinter 的现代化卡片式 UI，实时显示回收站状态
- **开机自启动**：通过注册表 Run 项实现开机自动运行
- **多盘符支持**：自动枚举所有盘符下的 `$Recycle.Bin` 目录
- **配置持久化**：所有设置保存到用户目录下的 JSON 配置文件
- **高 DPI 适配**：启用 DPI 感知，在高分辨率屏幕上界面清晰不模糊

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.8+ |
| GUI | Tkinter / ttk |
| 系统托盘 | pystray |
| 图像处理 | Pillow |
| 打包 | PyInstaller |

## 📦 安装与运行

### 环境要求

- Windows 7 / 10 / 11
- Python 3.8 及以上

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行

```bash
python recycle_bin_cleaner.py
```

## 🚀 打包为 EXE

使用项目自带的 PyInstaller 配置文件进行打包：

```bash
pip install pyinstaller
pyinstaller 垃圾站自清.spec
```

打包完成后，可执行文件位于 `dist/垃圾站自清.exe`。

## ⚙️ 配置说明

首次运行后，配置文件自动生成于：

```
%USERPROFILE%\.recycle_bin_cleaner.json
```

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `days_threshold` | int | 30 | 清理超过多少天的文件 |
| `auto_clean` | bool | true | 是否启用自动清理 |
| `check_interval_hours` | float | 6 | 自动检查间隔（小时） |
| `run_at_startup` | bool | false | 开机自启动 |
| `last_clean_time` | string | "" | 上次清理时间 |
| `last_clean_count` | int | 0 | 上次清理文件数量 |
| `last_clean_size` | int | 0 | 上次清理释放空间（字节） |

## 📁 项目结构

```
recycle_bin_cleaner/
├── recycle_bin_cleaner.py   # 主程序（清理核心 + GUI + 托盘）
├── requirements.txt          # Python 依赖
├── 垃圾站自清.spec           # PyInstaller 打包配置
├── app.ico                   # 应用图标
├── .gitignore                # Git 忽略规则
└── README.md                 # 项目说明
```

## 🧩 核心原理

Windows 回收站在每个盘符的 `$Recycle.Bin` 目录下，按用户 SID 划分子目录。每个被删除的文件对应一对文件：

- **`$I<随机名>`**：元数据文件，包含原始路径、文件大小、删除时间（FILETIME 格式）
- **`$R<随机名>`**：实际文件数据

本工具通过解析 `$I` 文件的二进制结构（支持 v1 / v2 两种格式），读取删除时间并与阈值比较，对过期文件同时删除 `$I` 和 `$R`，实现精准清理。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源协议。

## 🙏 致谢

- [pystray](https://github.com/moses-palmer/pystray) — Python 系统托盘库
- [Pillow](https://python-pillow.org/) — Python 图像处理库
- [PyInstaller](https://www.pyinstaller.org/) — Python 打包工具
