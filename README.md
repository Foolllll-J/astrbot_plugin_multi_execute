<div align="center">

# ⚡ 指令模拟器

<i>🧩 举一隅可以三隅反</i>

![License](https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white)
![AstrBot](https://img.shields.io/badge/framework-AstrBot-ff6b6b?style=flat-square)

</div>

## ✨ 简介

一款为 [**AstrBot**](https://github.com/AstrBotDevs/AstrBot) 设计的指令模拟器。支持模拟其他用户执行指令、自动连续执行指令、免唤醒触发指令和伪装指令回复。

---

## 🌟 功能特性

- 🎭 用户模拟：管理员可模拟任意用户身份执行指令。
- 🔄 连续执行：支持 nx 语法，一键自动化执行重复任务。
- ⚡ 免唤醒触发：支持特定指令或全量指令免唤醒，无需唤醒即可触发。
- 👻 指令伪装：支持拦截指令回复，替换为自定义文本或实现完全静默执行。

---

## 🚀 使用方法

### 🎭 模拟用户指令

使用 `/模拟 @用户 指令` 格式，允许管理员模拟其他用户执行指令。

- **示例**：`/模拟 @用户A /查询状态`
- **效果**：Bot 将以用户A的身份执行 `/查询状态` 指令。

### 🔄 连续执行指令

使用 `nx 指令` 格式，其中 `n` 为执行次数，`nx`前无需指令前缀。

- **示例**：`3x /签到` (前缀模式开启时) 或 `3x 签到` (前缀模式关闭时)
- **效果**：Bot 将连续执行 3 次 `/签到` 指令。

### ⚡ 免唤醒指令

用户可以直接发送指令来执行，无需输入唤醒 Bot。

- **示例**：添加指令 `签到`
- **使用**：直接发送 `签到` 或 `签到 参数`
- **效果**：Bot 会自动添加唤醒前缀，转换为 `/签到` 或 `/签到 参数`

> [!TIP]
> 开启 `所有指令免唤醒` 模式后，所有已注册的指令都无需前缀即可触发。可通过 `需唤醒指令列表` 设置黑名单。

### 👻 伪装指令回复

使用自定义的消息替换原本的指令执行反馈，或实现静默触发。

- **示例**：添加指令 `reset`，回复内容 [ "清空啦", "已经全部忘掉了" ]
- **使用**：发送 `/reset`
- **效果**：Bot 执行 `reset` 指令的回复将从回复列表中随机选取。若回复内容为空，则保持静默。

---

## ⚙️ 配置说明

首次加载后，请在 AstrBot 后台 -> 插件 页面找到本插件进行设置。所有配置项都有详细的说明和介绍。

---

## 🔄 版本历史

详见 [CHANGELOG.md](./CHANGELOG.md)

---

## ❤️ 支持

* [AstrBot 帮助文档](https://astrbot.app)
* 如果您在使用中遇到问题，欢迎在本仓库提交 [Issue](https://github.com/Foolllll-J/astrbot_plugin_multi_execute/issues)。

---

<div align="center">

**如果本插件对你有帮助，欢迎点个 ⭐ Star 支持一下！**

</div>