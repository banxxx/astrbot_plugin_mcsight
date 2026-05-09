# mcwatcher

**Minecraft 多服务器状态监控插件** for [AstrBot](https://github.com/AstrBotDevs/AstrBot)

> 查询多个 MC 服务器的在线玩家信息，并以精美卡片图片呈现。

---

## ✨ 功能特性

- 🖼️ **可视化信息卡片**：用 Pillow 将服务器在线玩家绘制风格图片，头像圆角、分区卡片清晰。
- 📡 **多服务器支持**：可添加、删除、修改、批量管理你的 Minecraft 服务器列表。
- ⚡ **高性能查询**：异步并行查询所有服务器，头像下载自动缓存。
- 🧩 **功能隔离**：项目结构模块化，方便后续扩展更多 MC 监控能力。
- 🎨 **灵活别名**：支持 `/在线` 等快捷命令，快速生成状态图片。

---

## 📸 效果预览

发送 `/mc status` 或 `/在线`，机器人会回复一张类似下图的卡片：

![预览效果](https://raw.githubusercontent.com/banxxx/astrbot_plugin_mcwatcher/main/preview.png)


---

## 📦 安装

1. 将本插件文件夹放入 AstrBot 的插件目录：

   ```
   AstrBot/data/plugins/astrbot_plugin_mcwatcher/
   ```

2. 安装依赖：
   - 方式一：重启 AstrBot 后，在 WebUI “插件管理”中点击“安装依赖”按钮。
   - 方式二：在 AstrBot 的 Python 环境中手动执行：

     ```bash
     pip install aiohttp mcstatus Pillow
     ```

3. 自定义中文字体

   将任意支持中文的 `.ttf` 字体文件放入以下路径：

   ```
   resources/fonts/
   ```

   并修改字体名称为`msyh.ttf`（默认查找 `msyh.ttf`）。

4. 在 AstrBot WebUI 中启用/重载插件。

---

## 🚀 使用

### 基本命令

所有命令都以 `/mc` 开头（可搭配别名），具体如下：

| 命令 | 说明 |
|------|------|
| `/mc add 生存服 play.example.com` | 添加一个服务器 |
| `/mc remove 生存服` | 删除指定服务器 |
| `/mc edit 生存服 new.ip.com` | 修改服务器 IP |
| `/mc batchadd 生存服:ip1,生电服:ip2` | 批量添加（逗号分隔） |
| `/mc batchremove 生存服,生电服` | 批量删除（逗号分隔） |
| `/mc list` | 列出所有已添加的服务器 |
| `/mc status` | 查询所有服务器在线状态并生成图片 |
| `/mc help` | 显示帮助信息 |

### 快捷别名

- **`/在线`** （及别名 `online`）：直接查询所有服务器并返回状态图片。

你可以根据需要添加更多别名，或修改 `main.py` 中的装饰器参数。

---

## ⚙️ 配置

服务器列表存储在 `servers.json`。你可以直接编辑这个文件来批量导入，格式如下：

```json
{
  "servers": [
    {"name": "生存服", "host": "play.survival.com"},
    {"name": "生电服", "host": "tech.mc.com"}
  ]
}
```


---

## 🏗️ 项目结构

```
astrbot_plugin_mcwatcher/
├── main.py                  # 插件入口，命令注册
├── metadata.yaml            # 插件元数据
├── requirements.txt         # 依赖
├── commands/                # 命令分发
│   └── mc_handler.py
├── config/                  # 服务器配置管理
│   └── server_config.py
├── features/                # 功能板块
│   └── player_status/       # 在线玩家功能
│       ├── controller.py    # 板块入口
│       ├── checker.py       # MC 服务器查询
│       └── image_generator.py  # 图片绘制
├── utils/                   # 通用工具
│   └── avatar_cache.py      # 头像下载缓存
├── resources/               # 静态资源
│   └── fonts/               # 字体文件
└── avatar_cache/            # 头像缓存目录（运行时自动生成）
```


---

## 🔮 后续计划

- [ ] 显示服务器 MOTD、延迟、版本
- [ ] 支持 Bedrock 版服务器
- [ ] 玩家上下线通知
- [ ] 定时自动查询并发送状态
- [ ] WebUI 配置面板

欢迎提交 Issue 或 PR！

---

## 🤝 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件框架
- [mcstatus](https://github.com/py-mine/mcstatus) Minecraft 查询库
- [Minotar](https://minotar.net/) 头像 API