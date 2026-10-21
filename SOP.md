# 语雀接入工具包手册（口令：连语雀）

> 这份手册是 `yuque-kit` 工具包的操作说明，跟着工具包一起走，换电脑也有效。
> 对本工具说一句「**连语雀**」，就是按这份手册跑一遍：启动浏览器 → 确认登录 → 按需读取或写入。

---

## 0. 这套东西解决什么问题

语雀没有对外的免费 API Token（那是超级会员功能），也没有现成的连接器。可行路线只有一条：

> **用真实浏览器登录 → 通过调试端口接管这个浏览器 → 调它自己的站内接口拿数据。**

所以工具包本质上做三件事：找到一个浏览器、用独立配置目录启动它、把你的操作翻译成站内请求。

**已经确认走不通的两条路**（别再浪费时间去试）：

| 方案 | 结果 |
|---|---|
| 找连接器 | 连接器市场里没有语雀，只有 Notion / 幕布 / flomo / 飞书 |
| 官方 Token | Personal Access Token 挂「超级会员」标签，免费账号生成不了 |

---

## 1. 怎么用

### 第一次（每台电脑各一次）

1. 把 `yuque-kit` 文件夹拷到这台电脑
2. 双击 **`start.bat`**（mac/Linux 用 `./yuque.sh`）
3. 会自动：找 Python → 建 `.venv` → 装依赖 → 启动独立浏览器 → 等你登录
4. 在弹出窗口登录语雀（微信扫码最快，其次「密码登录」），脚本检测到登录会自动继续

### 之后

```bash
python yuque.py start                 # 启动 + 确认登录
python yuque.py books                 # 有哪几个库、各多少篇
python yuque.py toc ai                # 看 ai 库的目录
python yuque.py pull --book ai        # 导出 ai 库 -> out/md/ai/
python yuque.py pull                  # 导出全部
python yuque.py push 报告.md --book ai  # 本地 md 写入语雀（同名则更新）
python yuque.py state                 # 当前页面
python yuque.py stop                  # 关闭浏览器
python yuque.py doctor                # 环境体检
```

库名支持「名字 / id / slug / 部分匹配」，比如 `--book 云原生` 也能命中。

---

## 2. 工作原理

```
yuque.py
  ├── 找到 Python（py -3 / python / 常见安装目录 / conda）
  ├── 在工具目录建 .venv，装 playwright + beautifulsoup4 + markdownify
  ├── 找到 Edge 或 Chrome（固定路径 + 注册表 App Paths）
  ├── 用 --remote-debugging-port=<按目录路径派生> --user-data-dir=./edge-profile 启动
  ├── Playwright connect_over_cdp 接管
  └── 在页面里 fetch 站内接口（自动带上登录 cookie）
```

三个关键设计：

1. **全部路径相对 `yuque.py`**。没有一处绝对路径，放中文目录、放 U 盘都行。
2. **依赖装在自己的 `.venv`**。不动系统 Python，删掉会自动重建。
3. **浏览器用独立配置目录**。不碰你日常用的 Edge，退出也不影响你原来的窗口。

另外两点是为了「多份副本 / 多台电脑」不打架：

- **调试端口不是固定的 9222**，而是按工具目录路径算出来的（9222 + 0~99）。这样你同时放两份工具包，它们各用各的端口，不会互相顶掉。实际端口看 `config.json` 或 `doctor` 输出。
- **同一个配置目录只能有一个浏览器实例**。旧实例没退时再启动，Edge 会把请求交给旧实例然后自己退出，新端口永远起不来。所以 `start` 会先检查并关掉旧实例。

---

## 3. 接口清单（2026-10 实测）

所有请求都在 `https://www.yuque.com` 页面上下文里发，靠浏览器 cookie 认证。

| 用途 | 方法与路径 | 说明 |
|---|---|---|
| 列知识库 | `GET /api/mine/books` | 200 有数据 = 已登录；401 = 未登录。**登录判断就用它** |
| 列目录 | `GET /api/docs?book_id=<id>&limit=300` | 返回 title / slug / word_count / updated_at |
| 读正文 | `GET /api/docs/<slug>?book_id=<id>` | `data.content` 是 lake HTML，不是纯文本 |
| 新建文档 | `POST /api/docs?book_id=<id>` | body: `{title, body, body_draft, format:"markdown", public:0}` |
| 更新文档 | `PUT /api/docs/<数字id>?book_id=<id>` | **必须用数字 id，用 slug 会报「要求是数字」** |
| 删除文档 | `DELETE /api/docs/<数字id>?book_id=<id>` | 真删，无回收站。工具**不提供**这个命令 |

**踩过的坑：**

- `/api/misc/me` 曾经能查当前用户，现在返回 **404**。别再用它判断登录。
- `/api/v2/user` 需要官方 Token，浏览器 cookie 调它一律 401。
- 建文档时 `body` 传 Markdown 即可，`format:"markdown"` 会自己渲染成表格和代码块；不需要自己转 HTML。
- 更新文档后 slug 不变，所以「同名更新」不会产生副本。

---

## 4. 正文转换（lake HTML → Markdown）

语雀正文是自定义的 `lake` 格式，直接取文字会丢掉代码块和图片。处理顺序：

1. 去掉开头的 `<!doctype lake>` 和 `<meta>`
2. 处理 `<card>` 标签：
   - `name="image"` → `value` 是 `data:<urlencode(json)>`，解出 `src` 写成 `![图片](src)`
   - `name="codeblock"` → 解出 `code` 和 `mode`，写成围栏代码块
   - 其它带 `src` 的 → 写成链接
3. 用 `markdownify` 转（`heading_style="ATX"`, `bullets="-"`, 不转义 `*` 和 `_`）
4. 合并连续空行

图片保留语雀 CDN 链接，联网可见，不下载到本地。原始 JSON 留在 `out/raw/`，转换坏了可以用它重转，不用重新抓。

---

## 5. 换电脑 / 换目录要注意

| 东西 | 能不能拷 | 原因 |
|---|---|---|
| `yuque.py` `start.bat` `README.md` `SOP.md` | ✅ 必须拷 | 工具本体 |
| `edge-profile/` | ❌ 拷了也没用 | cookie 用 Windows 账户 DPAPI 加密，换机器解不开。每台机器扫码一次 |
| `.venv/` | ❌ 别拷 | 是本机 Python 的副本，拷过去用不了还占几百兆 |
| `out/` | 可选 | 已导出的笔记，想带走就拷 |

**其它电脑上的旧版本要升级**，直接覆盖 `yuque.py` 和 `SOP.md` 就行，`edge-profile` 不用动。

---

## 6. 故障排查

**A. 连 9222 端口报 502 / 连接被拒**
代理变量在作怪。工具启动时已经清空 `HTTP_PROXY` 等变量，如果你是手动敲命令，记得加 `NO_PROXY=*`。反过来，如果这台机器**必须**走代理才能上网，设 `YQ_KEEP_PROXY=1` 再运行。

**B. 浏览器起来了，但命令结束后就没了**
某些终端/自动化环境会在命令结束时连带杀掉子进程。工具改用 WMI（`Win32_Process.Create`）拉起浏览器，父进程是系统服务，不受影响。判断方法：`curl --noproxy '*' http://127.0.0.1:<端口>/json/version` 有返回就是活着（端口见 `config.json` 或 `doctor`）。

**B2. `start` 报「调试端口没响应」**
99% 是同一个配置目录已经有浏览器在跑：Edge 会把新启动请求转交给旧实例，然后自己退出，新端口自然起不来。工具会自动检测并先关掉旧实例；如果还不行，手动跑一次 `python yuque.py stop` 再 `start`。

**C. 说「未登录」，但窗口里明明已经登录了**
先看登录判断接口。旧版用 `/api/misc/me`（现在 404）会误判为未登录，必须用 `/api/mine/books`。

**D. `pip install` 失败（公司网络/代理）**
工具会自动重试清华镜像。还不行就手动指定：`set YQ_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`。

**E. 复制 `edge-profile` 报 `Device or resource busy`**
浏览器还占着 `Cookies` 文件，这时复制会**漏掉登录态**。必须先确认 `tasklist | findstr /i msedge` 为空、端口 000，再整目录复制，最后校验 `Default/Network/Cookies` 存在（约 20KB）。

**F. 截图命令超时 `Page.screenshot: Timeout 30000ms exceeded`**
浏览器窗口不在前台时，Chromium 等不到可见帧。抓取**不依赖截图**，别被这个报错带偏；需要截图先 `bring_to_front()` 并 try/except 兜底。

**G. PowerShell 输出解码报 `UnicodeDecodeError`**
Windows 控制台是 GBK，PowerShell 中文输出按 UTF-8 解码会崩。工具里统一按 `utf-8 → gbk → mbcs` 顺序尝试解码。另：传复杂脚本一律用 `-EncodedCommand`（base64 UTF-16LE），彻底避开引号和中文路径的转义问题。

**H. PDF 附件读不出来**
`Read` 工具对 PDF 有 256KB 上限；pip 装 pymupdf 又可能被代理拦。可行办法是让浏览器渲染 `file:///...pdf` 再截图（http 直连 PDF 会触发下载，只有 `file://` 能渲染）。

**I. 更新文档报「要求是数字」**
`PUT /api/docs/<id>` 里的 `<id>` 必须是数字主键，不是 slug。先列目录拿到 `id` 再更新。

---

## 7. 安全边界

- 账号密码不经过本工具，登录完全在浏览器里完成
- 只写自己的语雀，`push` 只按你给的文件和标题写，不动其它文档
- 不写系统目录、不改注册表、不装全局包
- **不提供删除命令**：语雀的删除是真删、没有回收站，故意不给这个口子防止误删
- 只读模式下不会点编辑、不会改任何笔记

---

## 8. 一句话回顾

> 命令是 `python yuque.py <start|books|toc|pull|push|stop>`，
> 路径全自动，环境自动装，登录一次长期有效。
> 换电脑就三件事：拷工具、双击 `start.bat`、扫码。
