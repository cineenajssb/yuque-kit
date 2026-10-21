# -*- coding: utf-8 -*-
"""
语雀便携接入工具（Yuque Portable Kit）
====================================
单一入口，所有路径都相对本文件所在目录，可整个文件夹拷到任意 Windows / macOS / Linux。

用法：python yuque.py <命令>
   doctor            环境体检（不安装任何东西）
   setup             准备运行环境（建虚拟环境 + 装依赖，只需一次）
   start             启动或复用浏览器，并检查登录状态
   state             看当前打开的页面
   books             列出所有知识库
   toc   <知识库>     列出某个库的文档目录
   pull  [知识库]     把正文导出成 Markdown 到 out/md/
   push  <文件.md>    把本地 Markdown 新建或更新到语雀
   stop              关闭这个工具开的浏览器

设计原则：
1. 不写死任何绝对路径，全部基于 __file__；
2. 不污染系统 Python，依赖装在自带的 .venv 里；
3. 浏览器只用「独立配置目录」，不碰你日常用的 Edge/Chrome；
4. 只读为主，写操作（push）默认按标题幂等更新，不产生副本。
"""

import os
import re
import sys
import json
import glob
import time
import shutil
import argparse
import subprocess
import urllib.parse

KIT = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(KIT, "config.json")
VENV = os.path.join(KIT, ".venv")
IS_WIN = os.name == "nt"
VPY = os.path.join(VENV, "Scripts", "python.exe") if IS_WIN else os.path.join(VENV, "bin", "python")
PROFILE = os.path.join(KIT, "edge-profile")
OUT = os.path.join(KIT, "out")
MD_DIR = os.path.join(OUT, "md")
RAW_DIR = os.path.join(OUT, "raw")

DEFAULT_PORT = 9222
NEED = ["playwright", "bs4", "markdownify"]
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------------------
# 基础小工具
# --------------------------------------------------------------------------
def say(msg=""):
    print(msg, flush=True)


def die(msg, code=1):
    print("\n× " + msg, flush=True)
    sys.exit(code)


def clean_env():
    """清掉代理变量。CDP 端口和站内 fetch 走代理会 502，这是踩过的坑。"""
    e = dict(os.environ)
    if e.get("YQ_KEEP_PROXY") in ("1", "true", "yes"):
        pass
    else:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            e.pop(k, None)
    e["NO_PROXY"] = "*"
    e["no_proxy"] = "*"
    e.setdefault("PYTHONIOENCODING", "utf-8")
    return e


for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"


def load_cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def profile_procs():
    """数一下还有多少浏览器进程在占用本工具的配置目录。
    同一个 user-data-dir 只能有一个实例，旧实例不退，新端口永远起不来。"""
    try:
        if IS_WIN:
            ps = ("$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like %s -and $_.Name -match 'msedge|chrome' }; "
                  "$p | Measure-Object | ForEach-Object { $_.Count }") % _ps_str("*" + PROFILE + "*")
            out = (_ps(ps).stdout or "").strip().splitlines()
            return int(out[-1]) if out and out[-1].strip().isdigit() else 0
        r = subprocess.run(["pgrep", "-f", PROFILE], capture_output=True, timeout=10, env=clean_env())
        return len([x for x in (r.stdout or b"").decode("utf-8", "replace").split() if x.strip()])
    except Exception:
        return 0


def default_port():
    """按工具目录算一个专属调试端口。
    不同副本互不抢占（避免拷了两份互相顶掉），同一份副本每次都一样。"""
    import zlib
    return DEFAULT_PORT + (zlib.crc32(os.path.abspath(KIT).lower().encode("utf-8")) % 100)


def port_info(port=None, timeout=2):
    port = port or default_port()
    """CDP 端口活着就返回版本信息，否则 None。"""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%d/json/version" % port, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def human(n):
    n = n or 0
    if n >= 10000:
        return "%.1f 万" % (n / 10000.0)
    return str(n)


def dwidth(s):
    """显示宽度：中文/全角按 2 算，用于对齐。"""
    import unicodedata
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s, width):
    s = str(s)
    return s + " " * max(0, width - dwidth(s))


# --------------------------------------------------------------------------
# Python / 依赖 / 虚拟环境
# --------------------------------------------------------------------------
def _ver_of(argv):
    try:
        r = subprocess.run(argv + ["-c", "import sys;print('%d%d' % sys.version_info[:2])"],
                           capture_output=True, text=True, timeout=25, env=clean_env())
        if r.returncode != 0:
            return 0
        return int((r.stdout or "0").strip() or 0)
    except Exception:
        return 0


def find_python():
    """返回 (启动命令列表, 版本号)。全部是「可执行 + 参数」形式，兼容 py 启动器。"""
    cands = []
    if IS_WIN:
        cands.append(["py", "-3"])
        cands.append(["py"])
        lp = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        up = os.environ.get("USERPROFILE", "")
        pats = []
        if lp:
            pats.append(os.path.join(lp, "Programs", "Python", "Python3*", "python.exe"))
        pats.append(r"C:\Python3*\python.exe")
        pats.append(os.path.join(pf, "Python3*", "python.exe"))
        pats.append(os.path.join(pf86, "Python3*", "python.exe"))
        if up:
            pats.append(os.path.join(up, ".workbuddy", "binaries", "python", "versions", "*", "python.exe"))
            pats.append(os.path.join(up, "miniconda3", "python.exe"))
            pats.append(os.path.join(up, "anaconda3", "python.exe"))
        pats.append(os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Miniconda3", "python.exe"))
        for pat in pats:
            for p in sorted(glob.glob(pat), reverse=True):
                cands.append([p])
        cands.append(["python"])
        cands.append(["python3"])
    else:
        cands = [["python3"], ["python"],
                 ["/usr/bin/python3"], ["/usr/local/bin/python3"], ["/opt/homebrew/bin/python3"]]

    for c in cands:
        exe = c[0]
        if exe != "py" and not exe.startswith("python") and ("/" in exe or "\\" in exe) and not os.path.exists(exe):
            continue
        v = _ver_of(c)
        if v >= 38:
            return c, v
    return None, 0


def deps_ok(py):
    try:
        r = subprocess.run(py + ["-c", "import " + ", ".join(NEED)],
                           capture_output=True, timeout=90, env=clean_env())
        return r.returncode == 0
    except Exception:
        return False


def pip_install(py):
    pkgs = ["playwright", "beautifulsoup4", "markdownify"]
    idx_list = [None, os.environ.get("YQ_PIP_INDEX") or "https://pypi.tuna.tsinghua.edu.cn/simple"]
    for idx in idx_list:
        cmd = py + ["-m", "pip", "install", "--disable-pip-version-check", "-q"] + pkgs
        if idx:
            cmd += ["-i", idx]
            say("  依赖源换成镜像重试…")
        r = subprocess.run(cmd, env=clean_env())
        if r.returncode == 0 and deps_ok(py):
            return True
    return False


def ensure_ready():
    if os.path.exists(VPY) and deps_ok([VPY]):
        return VPY
    say("首次运行，正在准备运行环境（1-2 分钟，只需一次）…")
    base, ver = find_python()
    if not base:
        say("")
        die("这台电脑没找到 Python 3.8 以上版本。\n"
            "  装法一：https://www.python.org/downloads/ 下载安装，安装时勾选 Add python.exe to PATH\n"
            "  装法二：命令行执行  winget install -e --id Python.Python.3.12\n"
            "  装完重新运行本工具即可。", 2)
    say("  Python: %s  (3.%s)" % (" ".join(base), ver - 300))

    if not os.path.exists(VPY):
        say("  创建独立虚拟环境 .venv …")
        r = subprocess.run(base + ["-m", "venv", VENV], env=clean_env())
        if r.returncode != 0 or not os.path.exists(VPY):
            die("创建虚拟环境失败。请确认安装的是完整版 Python（含 venv 模块），而不是精简版。")
    if not deps_ok([VPY]):
        say("  安装依赖 playwright / beautifulsoup4 / markdownify …")
        if not pip_install([VPY]):
            die("依赖安装失败。如果这台机器上网必须走代理，先设置环境变量 YQ_KEEP_PROXY=1 再重试；\n"
                "  也可以指定镜像：set YQ_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple")
    say("  环境就绪")
    return VPY


# --------------------------------------------------------------------------
# 浏览器探测 / 启动
# --------------------------------------------------------------------------
def browser_candidates():
    cands = []
    if IS_WIN:
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        lp = os.environ.get("LOCALAPPDATA", "")
        for p in (os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
                  os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
                  os.path.join(lp, "Microsoft", "Edge", "Application", "msedge.exe")):
            cands.append(("edge", p))
        for p in (os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
                  os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
                  os.path.join(lp, "Google", "Chrome", "Application", "chrome.exe")):
            cands.append(("chrome", p))
        try:
            import winreg
            for exe, kind in (("msedge.exe", "edge"), ("chrome.exe", "chrome")):
                for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                    try:
                        key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe)
                        v = winreg.QueryValue(key, None)
                        if v:
                            cands.append((kind, v.strip('"')))
                    except OSError:
                        pass
        except Exception:
            pass
    else:
        cands += [("chrome", shutil.which("google-chrome") or ""),
                  ("chrome", shutil.which("chromium") or ""),
                  ("edge", shutil.which("microsoft-edge") or ""),
                  ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]

    out, seen = [], set()
    for kind, p in cands:
        if p and os.path.exists(p):
            k = os.path.normcase(os.path.abspath(p))
            if k not in seen:
                seen.add(k)
                out.append((kind, os.path.abspath(p)))
    return out


def pick_browser(explicit=None):
    cands = browser_candidates()
    if explicit and explicit not in ("auto", ""):
        if os.path.exists(explicit):
            return "custom", os.path.abspath(explicit)
        for kind, p in cands:
            if kind == explicit:
                return kind, p
        die("没有找到 %s。可用：%s" % (explicit, "、".join(sorted({k for k, _ in cands})) or "无"))
    cfg = load_cfg()
    old = cfg.get("browser_path")
    if old and os.path.exists(old):
        return cfg.get("browser_kind", "edge"), old
    if not cands:
        die("没找到 Edge 或 Chrome，请先安装其中任意一个浏览器。")
    for kind, p in cands:
        if kind == "edge":
            return kind, p
    return cands[0]


def _decode(raw):
    """Windows 控制台输出可能是 GBK，按顺序试几种编码，别让解码把命令搞崩。"""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "gbk", "mbcs", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def _ps(ps_script, timeout=90):
    """用 -EncodedCommand 跑 PowerShell，彻底绕开中文路径和引号转义问题。"""
    import base64
    enc = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                       capture_output=True, timeout=timeout, env=clean_env())
    r.stdout = _decode(r.stdout) if r.stdout else ""
    r.stderr = _decode(r.stderr) if r.stderr else ""
    return r


def _ps_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def _quote_arg(a):
    return '"%s"' % a if (" " in a or "\t" in a) else a


def _launch_wmi(argv):
    """经 WMI 拉起进程，父进程是 WmiPrvSE，可脱离调用方的 Job 对象。
    某些终端/自动化环境会在命令结束时连带杀掉子进程，用这条路可以躲开。"""
    ps = "$cl = %s; Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$cl} | Out-Null" % \
         _ps_str(" ".join(_quote_arg(a) for a in argv))
    r = _ps(ps)
    return r.returncode == 0


def launch_browser(port, url, explicit=None):
    kind, exe = pick_browser(explicit)
    os.makedirs(PROFILE, exist_ok=True)
    argv = [exe,
            "--remote-debugging-port=%d" % port,
            "--remote-allow-origins=*",
            "--user-data-dir=%s" % PROFILE,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=msEdgeSidebarV2",
            url]
    cfg = load_cfg()
    cfg.update({"browser_kind": kind, "browser_path": exe, "port": port, "profile": PROFILE})
    save_cfg(cfg)
    say("  浏览器：%s" % exe)

    if not IS_WIN:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, env=clean_env())
        return kind, exe, argv

    # 优先用 WMI 拉起：父进程是系统服务，不会跟着终端/脚本进程一起被杀，
    # 这样「启动」和「干活」可以分成两条命令执行。失败再退回普通方式。
    if not _launch_wmi(argv):
        say("  系统服务方式不可用，改为直接启动…")
        flags = 0x00000008 | 0x00000200 | 0x01000000  # DETACHED | NEW_GROUP | BREAKAWAY
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags, env=clean_env())
        except OSError:
            die("启动浏览器失败。请确认 Edge / Chrome 能正常打开。")
    return kind, exe, argv


def wait_cdp(port, secs=30):
    t0 = time.time()
    while time.time() - t0 < secs:
        if port_info(port, timeout=1.5):
            return True
        time.sleep(0.6)
    return False


def pick_page(b):
    ctx = b.contexts[0] if b.contexts else None
    if ctx is None:
        die("浏览器没有可用上下文，请先运行 stop 关掉浏览器，再重新 start。")
    pages = [p for p in ctx.pages if p.url.startswith("http")]
    yq = [p for p in pages if "yuque.com" in p.url]
    if yq:
        return yq[-1]
    if pages:
        return pages[-1]
    return ctx.new_page()


class Session:
    """with Session(port) as s: s.pg / s.login"""

    def __init__(self, port=None, start_url="https://www.yuque.com/dashboard", browser=None):
        self.port = port or default_port()
        self.start_url = start_url
        self.browser = browser
        self.pw = None
        self.b = None
        self.pg = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        if not port_info(self.port):
            # 旧实例还占着配置目录时，新实例会直接退出并让端口起不来，先清掉
            if profile_procs() > 0:
                say("  发现同一目录还有旧浏览器实例，先关掉…")
                cmd_stop(self.port)
            say("启动浏览器（独立配置目录，不影响你日常用的浏览器）…")
            kind, exe, argv = launch_browser(self.port, self.start_url, self.browser)
            if not wait_cdp(self.port, 30):
                say("  调试端口没响应，改用系统服务方式再拉起一次…")
                _launch_wmi(argv)
                if not wait_cdp(self.port, 40):
                    die("浏览器起来了但调试端口 %d 没响应。请先运行 stop，再重新 start。" % self.port)
        self.pw = sync_playwright().start()
        self.b = self.pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % self.port)
        self.pg = pick_page(self.b)
        return self

    def __exit__(self, *exc):
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        return False


# --------------------------------------------------------------------------
# 语雀站内 API
# --------------------------------------------------------------------------
JS_API = """async ([u, method, payload]) => {
  const opt = {method: method || 'GET', credentials: 'include',
               headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}};
  if (payload) opt.body = JSON.stringify(payload);
  const r = await fetch(u, opt);
  return {s: r.status, b: await r.text()};
}"""

JS_BATCH = """async (items) => {
  const out = [];
  for (const it of items) {
    try {
      const r = await fetch(`/api/docs/${it.slug}?book_id=${it.book_id}`, {credentials: 'include'});
      const j = await r.json();
      const d = j.data || {};
      out.push({slug: it.slug, ok: r.status === 200, title: d.title, content: d.content || '',
                word_count: d.word_count, updated_at: d.updated_at});
    } catch (e) {
      out.push({slug: it.slug, ok: false, err: String(e)});
    }
    await new Promise(r => setTimeout(r, 60));
  }
  return out;
}"""


def on_yuque(pg):
    if "yuque.com" not in pg.url:
        pg.goto("https://www.yuque.com/dashboard", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(1500)


def api(pg, path, method="GET", payload=None):
    on_yuque(pg)
    r = pg.evaluate(JS_API, [path, method, payload])
    body = r.get("b") or ""
    try:
        js = json.loads(body)
    except Exception:
        js = None
    return r.get("s"), js, body


def whoami(pg):
    """登录判断。语雀的 /api/misc/me 已经 404，改用 /api/mine/books：
    200 且能拿到数据 = 已登录；401/403 = 没登录。"""
    s, js, _ = api(pg, "/api/mine/books")
    if s == 200 and isinstance(js, dict):
        books = js.get("data") or []
        if books:
            user = books[0].get("user") or {}
            return user.get("login") or "已登录"
        return "已登录"
    return ""


def fetch_books(pg):
    s, js, body = api(pg, "/api/mine/books")
    if s != 200 or not isinstance(js, dict):
        die("读取知识库失败（HTTP %s）。多半是登录态失效，请重新登录后重试。\n  %s" % (s, body[:160]))
    out = []
    for bk in js.get("data") or []:
        user = bk.get("user") or {}
        out.append({"id": bk.get("id"), "name": bk.get("name"), "slug": bk.get("slug"),
                    "items": bk.get("items_count"), "login": user.get("login") or "",
                    "public": bk.get("public")})
    return out


def resolve_book(pg, key):
    books = fetch_books(pg)
    if not key:
        if len(books) == 1:
            return books[0]
        die("有多个知识库，请用 --book 指定一个：" + "、".join(b["name"] for b in books))
    k = str(key).strip()
    for b in books:
        if k == str(b["id"]) or k == b["name"] or k == b["slug"]:
            return b
    hit = [b for b in books if k in (b["name"] or "")]
    if len(hit) == 1:
        return hit[0]
    if len(hit) > 1:
        die("「%s」匹配到多个库：%s，请写完整名字。" % (k, "、".join(b["name"] for b in hit)))
    die("找不到知识库「%s」。现有：%s" % (k, "、".join(b["name"] for b in books)))


def fetch_toc(pg, book_id):
    s, js, body = api(pg, "/api/docs?book_id=%d&limit=300" % int(book_id))
    if s != 200 or not isinstance(js, dict):
        die("读取目录失败（HTTP %s）：%s" % (s, body[:160]))
    return js.get("data") or []


# --------------------------------------------------------------------------
# HTML(lake) -> Markdown
# --------------------------------------------------------------------------
def _html_to_md(html):
    if not html:
        return ""
    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    s = re.sub(r"^<!doctype lake>", "", html.strip(), flags=re.I)
    soup = BeautifulSoup(s, "html.parser")
    for m in soup.find_all("meta"):
        m.decompose()
    for c in soup.find_all("card"):
        name = (c.get("name") or "").lower()
        val = c.get("value") or ""
        data = None
        if val.startswith("data:"):
            try:
                data = json.loads(urllib.parse.unquote(val[5:]))
            except Exception:
                data = None
        if name == "image" and isinstance(data, dict) and data.get("src"):
            c.replace_with("\n![图片](%s)\n" % data["src"])
        elif name == "codeblock" and isinstance(data, dict) and data.get("code") is not None:
            c.replace_with("\n```%s\n%s\n```\n" % (data.get("mode") or "", data["code"]))
        elif isinstance(data, dict) and data.get("src"):
            c.replace_with("\n[附件/嵌入](%s)\n" % data["src"])
        else:
            c.replace_with("\n")
    try:
        out = md(str(soup), heading_style="ATX", bullets="-", strip=["span"],
                 escape_asterisks=False, escape_underscores=False)
    except Exception:
        out = soup.get_text("\n")
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def safe_name(name, maxlen=60):
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name or "无标题").strip()
    return (re.sub(r"\s+", " ", name)[:maxlen] or "无标题")


# --------------------------------------------------------------------------
# 命令实现
# --------------------------------------------------------------------------
def cmd_doctor():
    say("== 语雀便携工具包 · 环境体检 ==")
    say("工具目录：%s" % KIT)
    say("系统：%s %s" % (os.name, sys.platform))
    say("当前解释器：%s" % sys.executable)
    say("")

    ok_vpy = os.path.exists(VPY)
    say("虚拟环境：%s" % ("已就绪  %s" % VPY if ok_vpy else "未创建（首次运行 start/setup 时自动创建）"))
    if ok_vpy:
        say("依赖：%s" % ("完整" if deps_ok([VPY]) else "缺失（重跑 setup 会补装）"))
    if not ok_vpy:
        base, ver = find_python()
        say("可用 Python：%s" % ((" ".join(base) + "  (3.%d)" % (ver - 300)) if base else "没找到 3.8+"))

    cands = browser_candidates()
    say("")
    say("浏览器：")
    if cands:
        for kind, p in cands:
            say("  [%s] %s" % (kind, p))
    else:
        say("  没找到 Edge / Chrome")

    cfg = load_cfg()
    port = int(cfg.get("port") or default_port())
    say("")
    say("调试端口 %d：%s" % (port, "运行中" if port_info(port) else "未启动"))
    say("登录配置目录：%s（%s）" % (PROFILE, "已存在" if os.path.isdir(PROFILE) else "首次登录时创建"))
    say("配置文件：%s（%s）" % (CFG_PATH, "已生成" if os.path.exists(CFG_PATH) else "首次运行时生成"))
    say("")

    if port_info(port):
        try:
            with Session(port) as s:
                who = whoami(s.pg)
                say("登录状态：%s" % ("已登录 · " + who if who else "未登录"))
        except SystemExit:
            pass
        except Exception as e:
            say("登录状态：读取失败 %s" % e)
    else:
        say("登录状态：浏览器未启动，运行 start 后才知道")
    say("")
    say("体检结束。要开始用，执行：python yuque.py start")


def cmd_start(port, wait, browser, url):
    with Session(port, start_url=url or "https://www.yuque.com/dashboard", browser=browser) as s:
        who = whoami(s.pg)
        if who:
            say("✓ 已登录：%s" % who)
            say("  当前页面：%s" % s.pg.url)
            cfg = load_cfg()
            cfg.update({"last_login": who, "port": port})
            save_cfg(cfg)
            return 0

        say("× 这个浏览器配置还没登录语雀。")
        say("")
        say("请在弹出的浏览器窗口里登录（三种方式任选）：")
        say("  · 微信扫码（最快）")
        say("  · 点底部「密码登录」用账号密码")
        say("  · 手机号 + 短信验证码（要拖滑块，只能你手动）")
        try:
            s.pg.goto("https://www.yuque.com/login", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        if wait <= 0:
            say("")
            say("登录完成后，再运行：python yuque.py start")
            return 1

        say("")
        say("我会一直等你，登录成功会自动继续（最多 %d 秒，Ctrl+C 可中断）…" % wait)
        t0 = time.time()
        dot = 0
        while time.time() - t0 < wait:
            time.sleep(3)
            try:
                who = whoami(s.pg)
            except Exception:
                who = ""
            if who:
                say("")
                say("✓ 登录成功：%s（这台电脑以后不用再扫码）" % who)
                cfg = load_cfg()
                cfg.update({"last_login": who, "port": port})
                save_cfg(cfg)
                return 0
            dot += 1
            if dot % 5 == 0:
                say("  …还在等（%d 秒）" % int(time.time() - t0))
        say("")
        say("× 等超时了。登录好之后再跑一次 start 就行，登录态会存下来。")
        return 1


def cmd_state(port):
    with Session(port) as s:
        who = whoami(s.pg) or "未登录"
        say(json.dumps({"url": s.pg.url, "title": s.pg.title(), "login": who}, ensure_ascii=False))


def cmd_books(port, as_json):
    with Session(port) as s:
        books = fetch_books(s.pg)
        if as_json:
            say(json.dumps(books, ensure_ascii=False, indent=1))
            return 0
        say("共 %d 个知识库：" % len(books))
        say("")
        for b in books:
            privacy = "私密" if str(b.get("public")) in ("0", "False", "false") else "公开"
            say("  %s  %s  %s 篇  (id=%s)" % (
                pad(privacy, 6), pad(b["name"], 24), pad(b.get("items") or 0, 5), b["id"]))
        say("")
        say("导出某个库：python yuque.py pull --book 库名")
        return 0


def cmd_toc(port, book_key, as_json):
    with Session(port) as s:
        bk = resolve_book(s.pg, book_key)
        docs = fetch_toc(s.pg, bk["id"])
        if as_json:
            say(json.dumps({"book": bk, "docs": docs}, ensure_ascii=False, indent=1))
            return 0
        say("《%s》 共 %d 篇：" % (bk["name"], len(docs)))
        for i, d in enumerate(docs, 1):
            say("  %3d  %6s 字  %s  %s" % (i, d.get("word_count") or 0,
                                          str(d.get("updated_at") or "")[:10], d.get("title")))
        return 0


def cmd_pull(port, book_key):
    with Session(port) as s:
        pg = s.pg
        books = [resolve_book(pg, book_key)] if book_key else fetch_books(pg)
        os.makedirs(RAW_DIR, exist_ok=True)
        os.makedirs(MD_DIR, exist_ok=True)

        index = {}
        for bk in books:
            docs = fetch_toc(pg, bk["id"])
            say("《%s》%d 篇，开始抓取…" % (bk["name"], len(docs)))
            tasks = [{"book": bk["name"], "book_id": bk["id"], "idx": i,
                      "slug": d.get("slug"), "title": d.get("title"), "wc": d.get("word_count")}
                     for i, d in enumerate(docs, 1)]
            got = {}
            CH = 15
            for st in range(0, len(tasks), CH):
                chunk = tasks[st:st + CH]
                res = pg.evaluate(JS_BATCH, [{"slug": t["slug"], "book_id": t["book_id"]} for t in chunk])
                for g in res:
                    got[g["slug"]] = g
                    with open(os.path.join(RAW_DIR, "%s.json" % g["slug"]), "w", encoding="utf-8") as f:
                        json.dump(g, f, ensure_ascii=False)
                say("  进度 %d/%d" % (min(st + CH, len(tasks)), len(tasks)))
                time.sleep(0.2)

            folder = os.path.join(MD_DIR, safe_name(bk["name"]))
            os.makedirs(folder, exist_ok=True)
            for t in tasks:
                g = got.get(t["slug"], {})
                body = _html_to_md(g.get("content", ""))
                fn = "%03d-%s.md" % (t["idx"], safe_name(t["title"]))
                head = "# %s\n\n> 知识库：%s　|　字数：%s　|　更新：%s\n\n" % (
                    t["title"], bk["name"], g.get("word_count"), str(g.get("updated_at") or "")[:10])
                with open(os.path.join(folder, fn), "w", encoding="utf-8") as f:
                    f.write(head + body + "\n")
                index.setdefault(bk["name"], []).append({
                    "idx": t["idx"], "title": t["title"],
                    "file": "out/md/%s/%s" % (safe_name(bk["name"]), fn),
                    "word_count": g.get("word_count"),
                    "updated_at": str(g.get("updated_at") or "")[:10],
                    "chars": len(body), "ok": bool(g.get("content")),
                })

        with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)

        total = sum(len(v) for v in index.values())
        okc = sum(1 for v in index.values() for x in v if x["ok"])
        empty = total - okc
        chars = sum(x["chars"] for v in index.values() for x in v)

        lines = ["# 语雀笔记索引", "",
                 "导出时间：%s" % time.strftime("%Y-%m-%d %H:%M"), "",
                 "| 知识库 | 篇数 | 有正文 | 空文档 | 正文字符 | 文件位置 |",
                 "|---|---|---|---|---|---|"]
        for name, rows in index.items():
            n_ok = sum(1 for r in rows if r["ok"])
            lines.append("| %s | %d | %d | %d | %s | `out/md/%s/` |" % (
                name, len(rows), n_ok, len(rows) - n_ok,
                human(sum(r["chars"] for r in rows)), safe_name(name)))
        lines += ["", "合计 **%d** 篇，正文 %s 字符，空文档 %d 篇。" % (total, human(chars), empty), ""]
        for name, rows in index.items():
            lines += ["## %s（%d 篇）" % (name, len(rows)), ""]
            for r in rows:
                lines.append("%d. [%s](%s) — %s 字，%s" % (
                    r["idx"], r["title"], r["file"].replace("out/md/", "").replace(" ", "%20"),
                    r["word_count"] or 0, r["updated_at"]))
            lines.append("")
        with open(os.path.join(OUT, "INDEX.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        say("")
        say("✓ 完成：%d 篇（有正文 %d 篇，空文档 %d 篇），正文合计 %s 字符" % (total, okc, empty, human(chars)))
        say("  Markdown：%s" % MD_DIR)
        say("  索引：%s" % os.path.join(OUT, "INDEX.md"))
        return 0


def cmd_push(port, path, book_key, title, slug, force_new):
    if not os.path.exists(path):
        die("文件不存在：%s" % path)
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    lines = raw.split("\n")
    if not title:
        for ln in lines:
            m = re.match(r"^#\s+(.+)$", ln.strip())
            if m:
                title = m.group(1).strip()
                break
        if not title:
            title = os.path.splitext(os.path.basename(path))[0]
    while lines and (not lines[0].strip() or lines[0].startswith("# ")):
        lines.pop(0)
    body = "\n".join(lines).lstrip("\n")

    with Session(port) as s:
        pg = s.pg
        bk = resolve_book(pg, book_key)
        docs = fetch_toc(pg, bk["id"])
        hit = [d for d in docs if (d.get("title") or "").strip() == title.strip()]

        payload = {"title": title, "body": body, "body_draft": body, "format": "markdown", "public": 0}
        if slug:
            payload["slug"] = slug

        if hit and not force_new:
            doc_id = hit[0]["id"]
            st, js, txt = api(pg, "/api/docs/%s?book_id=%d" % (doc_id, bk["id"]), "PUT", payload)
            if st != 200:
                say("× 更新失败（HTTP %s）：%s" % (st, txt[:200]))
                say("  为避免产生同名副本，本次没有新建。想强行新建一篇，加 --force-new。")
                return 1
            data = (js or {}).get("data") or {}
            say("✓ 已更新：《%s》%s 字" % (data.get("title") or title, data.get("word_count")))
        else:
            st, js, txt = api(pg, "/api/docs?book_id=%d" % bk["id"], "POST", payload)
            if st not in (200, 201):
                say("× 新建失败（HTTP %s）：%s" % (st, txt[:300]))
                return 1
            data = (js or {}).get("data") or {}
            say("✓ 已新建：《%s》%s 字" % (data.get("title") or title, data.get("word_count")))

        login = (bk.get("login") or "").strip()
        if data.get("slug") and login and bk.get("slug"):
            say("  链接：https://www.yuque.com/%s/%s/%s" % (login, bk["slug"], data["slug"]))
        return 0


def cmd_stop(port):
    info = port_info(port)
    prof = PROFILE
    if IS_WIN:
        ps = ("$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like %s -and $_.Name -match 'msedge|chrome' }; "
              "foreach ($x in $p) { try { Stop-Process -Id $x.ProcessId -Force -ErrorAction Stop } catch {} }; "
              "$p | Measure-Object | ForEach-Object { 'killed: ' + $_.Count }") % _ps_str("*" + prof + "*")
        try:
            r = _ps(ps)
            out = (r.stdout or "").strip()
            if out:
                say("  " + out.splitlines()[-1])
        except Exception as e:
            say("  结束进程失败：%s" % e)
    else:
        subprocess.run(["pkill", "-f", prof], env=clean_env())

    time.sleep(2)
    if port_info(port):
        say("× 调试端口 %d 还在，可能有别的浏览器占用了它。" % port)
        return 1
    say("✓ 浏览器已关闭（登录态保存在 edge-profile，下次不用重新登录）" if info else "✓ 端口 %d 上没有浏览器在跑" % port)
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="yuque.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="语雀便携接入工具 —— 把语雀笔记读成 Markdown，或把本地 Markdown 写回语雀",
        epilog="示例：\n"
               "  python yuque.py start\n"
               "  python yuque.py books\n"
               "  python yuque.py pull --book ai\n"
               "  python yuque.py push 报告.md --book \"ai\"\n")
    sub = p.add_subparsers(dest="cmd")

    def add_port(sp):
        sp.add_argument("--port", type=int, default=None, help="调试端口，默认 9222")

    sp = sub.add_parser("doctor", help="环境体检（不安装）")
    add_port(sp)

    sp = sub.add_parser("setup", help="准备运行环境")
    add_port(sp)

    sp = sub.add_parser("start", help="启动/复用浏览器并检查登录")
    add_port(sp)
    sp.add_argument("--wait", type=int, default=0, help="未登录时最长等待秒数，双击启动默认给 300")
    sp.add_argument("--browser", default=None, help="edge / chrome / 浏览器 exe 路径")
    sp.add_argument("--url", default=None, help="启动时打开的页面")

    sp = sub.add_parser("state", help="当前页面信息")
    add_port(sp)

    sp = sub.add_parser("books", help="列出知识库")
    add_port(sp)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("toc", help="列出某库目录")
    add_port(sp)
    sp.add_argument("book", nargs="?", help="库名/id/slug，只有一个库时可省略")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("pull", help="导出 Markdown")
    add_port(sp)
    sp.add_argument("book", nargs="?", help="库名/id/slug，不写则导出全部")
    sp.add_argument("--book", dest="book_opt", default=None, help="同位置参数")

    sp = sub.add_parser("push", help="把本地 Markdown 新建/更新到语雀")
    add_port(sp)
    sp.add_argument("file", help="本地 .md 路径")
    sp.add_argument("--book", required=True, help="目标知识库")
    sp.add_argument("--title", default=None, help="文档标题，默认取文件首个 H1")
    sp.add_argument("--slug", default=None, help="自定义 slug（一般不用填）")
    sp.add_argument("--force-new", action="store_true", help="同名文档存在时也强制新建一篇")

    sp = sub.add_parser("stop", help="关闭这个工具开的浏览器")
    add_port(sp)

    return p


def main():
    if not sys.argv[1:]:
        print(__doc__)
        return 0

    argv = sys.argv[1:]
    if argv[0] in ("-h", "--help", "help"):
        build_parser().print_help()
        return 0

    # doctor 不建环境，先跑
    if argv[0] == "doctor":
        cmd_doctor()
        return 0

    vpy = ensure_ready()

    # 用 venv 里的解释器重新执行自己
    if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(os.path.abspath(vpy)):
        r = subprocess.run([vpy, os.path.abspath(__file__)] + argv, env=clean_env())
        return r.returncode

    args = build_parser().parse_args(argv)
    cfg = load_cfg()
    port = args.port or int(cfg.get("port") or default_port())

    if args.cmd == "setup":
        say("✓ 环境就绪")
        say("  虚拟环境：%s" % VPY)
        say("  工具目录：%s" % KIT)
        say("  下一步：python yuque.py start")
        return 0
    if args.cmd == "start":
        return cmd_start(port, args.wait, args.browser, args.url)
    if args.cmd == "state":
        cmd_state(port)
        return 0
    if args.cmd == "books":
        return cmd_books(port, args.json)
    if args.cmd == "toc":
        return cmd_toc(port, args.book, args.json)
    if args.cmd == "pull":
        return cmd_pull(port, args.book or args.book_opt)
    if args.cmd == "push":
        return cmd_push(port, args.file, args.book, args.title, args.slug, args.force_new)
    if args.cmd == "stop":
        return cmd_stop(port)

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
