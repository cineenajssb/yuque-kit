# 语雀便携工具包（Yuque Portable Kit）

一个可以整个文件夹拷走、双击即用的语雀命令行工具。扫码登录即可读你语雀的笔记、把知识库导出成 Markdown，也能把本地 Markdown 写回语雀。

**零配置 · 双向读写 · 便携免装 · 不碰你日常浏览器。**

---

## 为什么选它

语雀对免费用户不提供 API Token（Personal Access Token 是超级会员功能），也没有官方连接器。现有同类工具大多要你手动找 Token、复制 Cookie，甚至把账号密码写进配置文件。这个工具换了一条更省心的路：**用真实浏览器扫码登录，再通过调试端口接管浏览器，调语雀自己的站内接口。**

| 工具 | 认证方式 | 双向读写 | 便携免装 |
|---|---|---|---|
| **yuque-kit（本项目）** | 扫码登录，密码不过工具 | ✅ pull + push | ✅ 双击即用 |
| gxr404/yuque-dl | Token / 手动 Cookie | 只读 | 需 Node |
| john1688/yuque-tools | 明文账号密码 | 只读 | 需 npm |
| 各类 yuque-exporter | Token / 明文密码 | 只读 | 需装依赖 |

三个关键设计：

1. **全部路径相对脚本自身** —— 放中文目录、放 U 盘、放任何盘符都行
2. **依赖装在自带的 `.venv`** —— 不污染系统 Python，删了会自动重建
3. **浏览器用独立配置目录** —— 不碰你日常用的 Edge/Chrome，退出也不影响你原来的窗口

---

## 一分钟上手

1. 把整个 `yuque-kit` 文件夹拷到本地硬盘
2. 双击 **`start.bat`**（macOS / Linux 运行 `./yuque.sh`）
3. 第一次会自动：找 Python → 建 `.venv` → 装依赖 → 弹出独立浏览器窗口
4. 在窗口里登录语雀（微信扫码最快），脚本检测到登录后自动继续
5. 之后就能用命令干活了

> 如果这台电脑没装过 Python，入口会明确告诉你两种装法，装完再双击一次即可。

---

## 常用命令

在文件夹里打开命令行：

```bash
python yuque.py start                  启动浏览器 + 检查登录
python yuque.py books                  列出所有知识库
python yuque.py toc ai                 看某个库的目录（库名支持部分匹配）
python yuque.py pull --book ai         导出某个库 -> out/md/
python yuque.py pull                   导出全部知识库
python yuque.py push 报告.md --book ai  本地 Markdown 写入语雀（同名则更新）
python yuque.py state                  看当前浏览器停在哪一页
python yuque.py doctor                 环境体检（不装任何东西）
python yuque.py stop                   关闭这个工具开的浏览器
```

- 库名可以写「名字 / id / slug」，支持部分匹配
- `push` 按标题判重：标题一样就更新，不会产生重复文档
- `pull` 是只读；删文档的接口是真删、无回收站，本工具**故意不提供删除命令**

---

## 工作原理

```
yuque.py
  ├── 找到 Python（py -3 / python / 常见目录 / conda）
  ├── 在工具目录建 .venv，装 playwright + beautifulsoup4 + markdownify
  ├── 找到 Edge 或 Chrome，用独立配置目录 + 调试端口启动
  ├── Playwright connect_over_cdp 接管浏览器
  └── 在页面里 fetch 站内接口（自动带上登录 cookie）
```

正文是语雀自定义的 `lake` 格式，工具会转成标准 Markdown：图片保留 CDN 链接，代码块还原成围栏代码块，原始 JSON 留在 `out/raw/` 供出错时重转。

---

## 目录说明

| 路径 | 是什么 |
|---|---|
| `yuque.py` | 唯一的主程序，所有命令都在这 |
| `start.bat` / `yuque.sh` | Windows / macOS、Linux 双击入口 |
| `config.json` | 首次运行自动生成，记录浏览器路径和端口 |
| `.venv/` | 本文件夹专用的 Python 环境，删了会自动重建 |
| `edge-profile/` | 本机的语雀登录态，删了就重新扫码 |
| `out/md/` | 导出的 Markdown，按知识库分文件夹 |
| `out/raw/` | 原始 JSON，转 Markdown 出错时可以拿它重转 |
| `SOP.md` | 完整手册：接口细节、正文转换、8 类故障排查 |

> 以上 `.venv/`、`edge-profile/`、`out/`、`config.json` 均为运行时产物，已列入 `.gitignore`，**切勿提交**（`edge-profile` 是你的登录态，泄露等于把账号交给别人）。

---

## 安全说明

- 账号密码不经过本工具，登录完全在浏览器里完成
- 只写你自己的语雀，`push` 只按你给的文件和标题写，不动其它文档
- 不写系统目录、不改注册表、不装全局包
- 浏览器用独立配置目录，和你日常用的 Edge/Chrome 完全隔离
- 不提供删除命令，防止误删

---

## 换电脑

三件事：**拷工具 → 双击 `start.bat` → 扫码**。

- `edge-profile` 里的登录态跟「这台电脑的 Windows 账户」加密绑定，拷不过去，每台新电脑扫码一次即可（一般几周有效）
- `.venv` 别跟着拷，到新机器第一次运行会自动重建
- 路径里有中文、空格都没关系

---

## 出问题了

先跑 `python yuque.py doctor`，它会列出 Python、依赖、浏览器、端口、登录状态。细节排查看 `SOP.md`。

## License

[MIT](LICENSE)
