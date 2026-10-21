# PCL Companion

PCL Companion 是一款独立、免费、开源的 Windows 下载与实例部署工具。它负责浏览和下载 Minecraft 社区资源，并将完整实例部署到用户指定的 `.minecraft`；PCL 仍然只负责账号、启动与版本管理。

![PCL Companion 图标](assets/app_icon.png)

> 本项目不是 PCL 插件，不属于 PCL 官方，也未获得 PCL 官方背书。“PCL”仅用于说明兼容目标。

## 主要功能

- 自动发现 PCL 旁的 `.minecraft`，也可选择任意自定义部署目录；
- 浏览 Modrinth 的模组、整合包、数据包、资源包和光影包；
- 按 Minecraft 版本、加载器与分类筛选，支持分页、图片缓存和完整项目介绍；
- 下载项目原始文件并进行 SHA-512 校验；
- 浏览 Mojang 官方原版版本清单，安装客户端 JAR 与版本 JSON；
- 解析 `.mrpack`，部署 Fabric 与 Forge 整合包；
- 扫描原版、Fabric、Forge、NeoForge、Quilt 本地实例；
- 保存任务和设置，显示安装阶段与 PCL 手动刷新指引；
- 完全本地运行，无账号、广告、遥测、付费功能或自建后端。

## 与 PCL 的边界

PCL Companion 是独立进程和独立代码库，不复制 PCL 源码、素材或内部下载实现，也不会：

- 修改、注入、替换或重新分发 PCL；
- 读取 PCL/Minecraft 登录账号、令牌或密码；
- 自动操作 PCL 的私有界面；
- 负责登录或启动 Minecraft；
- 写入用户未选择的游戏目录。

实例部署完成后，在 PCL 中执行 `版本选择 → 右键“当前文件夹” → 刷新` 即可重新扫描。PCL 是否立即显示实例取决于其版本扫描与缓存状态。

更详细的设计边界见 [架构说明](docs/ARCHITECTURE.md)。

## 当前支持情况

| 类型 | 当前状态 |
| --- | --- |
| Modrinth 资源浏览与下载 | 可用 |
| Fabric `.mrpack` 部署 | 可用 |
| Forge 1.20.1 `.mrpack` 部署 | 已实机验证 |
| 原版客户端 JAR/JSON | 可用 |
| 原版 Libraries/Assets 完整预下载 | 尚未完成 |
| NeoForge/Quilt 部署 | 实验性，尚未广泛验证 |
| CurseForge 在线目录 | 未接入（不内置私密 API Key） |
| 断点续传与镜像切换 | 尚未完成 |
| 独立 EXE 安装包 | 尚未提供，目前需要 Python |

## 运行

需要 Windows 与 Python 3.11+：

```powershell
python -m pip install -r requirements.txt
python app.py
```

也可双击 `run.cmd`。仓库不会自动创建桌面快捷方式；本机开发版本的快捷方式只是便利配置，并非安装器的一部分。

## 测试

```powershell
python -m compileall -q app.py pcl_companion
python -m unittest discover -s tests -v
```

## 数据与网络

- 设置、任务记录与图片缓存位于 `%LOCALAPPDATA%\PCLCompanion`；
- Modrinth、Mojang、Fabric 和 Forge 数据由客户端直接访问其公开服务；
- 下载文件只写入临时目录及用户选择的 `.minecraft`；
- 项目没有自建服务器、用户数据库、遥测或分析服务。

## 参与和安全

- 开发说明：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全问题：[SECURITY.md](SECURITY.md)
- 给 PCL 作者的项目说明草稿：[docs/LETTER_TO_PCL_AUTHOR.md](docs/LETTER_TO_PCL_AUTHOR.md)

## 许可

本项目以 [MIT License](LICENSE) 开源。官方版本不收费，也没有付费计划；MIT 许可本身允许在满足许可条件的前提下复制、修改和再分发。
