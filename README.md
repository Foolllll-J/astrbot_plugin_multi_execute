# ⚡ 指令倍增器

![License](https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white)
![AstrBot](https://img.shields.io/badge/framework-AstrBot-ff6b6b?style=flat-square)

一款为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 设计的指令倍增器。用户可通过发送 `nx 指令` ，模拟连续执行 n 次目标指令。

## 🌟 功能特性

- **连续执行**：支持将一个指令连续执行多次。
- **安全限制**：可配置单次最大执行次数，防止滥用。
- **权限管理**：支持白名单模式，仅允许特定用户使用，Bot 管理员默认拥有权限。
- **灵活配置**：支持设置指令执行的间隔时间。

## 🚀 使用方法

### 指令格式

使用 `nx 指令` 格式，其中 `n` 为执行次数，`nx`前无需指令前缀。

- **示例**：`3x /签到`
- **效果**：Bot 将连续执行 3 次 `/签到` 指令。


## ⚙️ 配置说明

在 AstrBot 管理面板中，你可以找到以下配置项：

| 配置项 | 说明 | 默认值 |
| :--- | :--- | :--- |
| **使用者白名单** | 填入用户 ID 列表，不填表示所有人可用。Bot 管理员默认可用。 | `[]` |
| **执行间隔时间 (秒)** | 连续执行指令之间的间隔时间。 | `1` |
| **最大执行次数限制** | 单次指令连续执行的最大次数限制。 | `10` |
| **指令监控超时时间 (秒)** | 单次指令执行的监控超时时间。 | `60` |



## ❤️ 支持

* [帮助文档](https://astrbot.app)
* 如果您在使用中遇到问题，欢迎提交 [Issue](https://github.com/Foolllll-J/astrbot_plugin_multi_execute/issues)。

