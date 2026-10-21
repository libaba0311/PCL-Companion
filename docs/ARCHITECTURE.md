# 架构说明

## 设计目标

PCL Companion 只承担“发现资源、下载、校验、构建实例、写入指定 `.minecraft`”这一段流程。PCL 继续承担账号、启动、版本展示与管理；两者之间没有插件协议、进程注入或内部 API 依赖。

## 组件

```text
Tkinter UI
 ├─ 概览与部署目标
 ├─ 社区资源目录
 ├─ 原版版本目录
 ├─ 本地实例与安装任务
 └─ 设置
      ↓
Application services
 ├─ PclService          发现 PCL、扫描本地实例
 ├─ ModrinthService     搜索、筛选、版本与文件元数据
 ├─ VanillaService      Mojang 原版版本清单与基础文件
 ├─ MrpackInstaller     解析、校验并规划整合包安装
 ├─ InstallEngine       临时构建、加载器安装与最终提交
 └─ TaskStore           持久化任务和设置
      ↓
File system / public upstreams
 ├─ 用户选择的 .minecraft/versions
 ├─ %LOCALAPPDATA%/PCLCompanion
 ├─ Modrinth / Mojang / Fabric / Forge
 └─ 本地 Java 运行时
```

## 安装事务

```text
解析项目与版本
  → 下载至本地临时/缓存目录
  → 校验哈希与 mrpack 相对路径
  → 构建完整实例
  → 执行所需加载器安装步骤
  → 校验版本 JSON 和关键文件
  → 提交至 .minecraft/versions/<实例名>
  → 提示用户在 PCL 中手动刷新
```

所有 `.mrpack` 目标路径都必须保持在实例目录内，以阻止 `../` 等路径穿越。下载先进入临时位置，验证通过后再提交，避免把半成品直接暴露为可启动实例。Forge 使用其官方 installer，并由本机 Java 执行；PCL Companion 不复制 Forge 安装器逻辑。

## 本地数据

`%LOCALAPPDATA%\PCLCompanion` 用于保存：

- `settings.json`：部署目标等用户设置；
- `tasks.json`：下载和安装任务状态；
- 图片缓存：仅缓存当前浏览过的项目图标。

自定义部署目标在设置页选择。应用会创建目标下缺少的 `versions` 目录，但不会把该目录自动添加为 PCL 的游戏文件夹。

## 零后端策略

- 客户端直接查询 Modrinth、Mojang、Fabric 与 Forge 的公开服务；
- 不托管资源索引，不代理安装包，不建立用户数据库；
- 不保存登录信息，不采集遥测；
- CurseForge 需要受其条款约束的 API 凭据，因此当前不在客户端中内置；
- 如果上游在中国大陆连接不稳定，当前版本只报告错误，不承诺代理或镜像服务。

## 明确限制

- 原版安装尚未完整预取 Libraries 和 Assets；
- Forge 1.20.1 已验证，其他 Forge、NeoForge 与 Quilt 组合仍需扩大测试矩阵；
- 没有断点续传、镜像切换和独立 EXE 安装包；
- PCL 的版本列表刷新由 PCL 自身决定，本项目只能写入规范实例并提示手动刷新。
