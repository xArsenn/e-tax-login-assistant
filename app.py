import base64
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tempfile
import urllib.request
import winreg
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from urllib.parse import urlparse

APP_NAME = "电子税务局企业登录助手"
APP_VERSION = "1.1.0"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/xArsenn/e-tax-login-assistant/main/latest.json"
REGIONS = {
    "深圳": {
        "portal": "https://etax.shenzhen.chinatax.gov.cn:8443/",
        "login_host": "tpass.shenzhen.chinatax.gov.cn",
    },
    "浙江": {
        "portal": "https://etax.zhejiang.chinatax.gov.cn:8443/",
        "login_host": "tpass.zhejiang.chinatax.gov.cn",
    },
    "四川": {
        "portal": "https://etax.sichuan.chinatax.gov.cn/",
        "login_host": "tpass.sichuan.chinatax.gov.cn",
    },
    "湖北": {
        "portal": "https://etax.hubei.chinatax.gov.cn:8443/",
        "login_host": "tpass.hubei.chinatax.gov.cn",
    },
}
TAX_REGIONS = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "大连", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "宁波", "安徽", "福建", "厦门", "江西", "山东", "青岛",
    "河南", "湖北", "湖南", "广东", "深圳", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]
REGIONS = {
    name: REGIONS.get(name, {"portal": "", "login_host": ""})
    for name in TAX_REGIONS
}
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
CONFIG_FILE = DATA_DIR / "config.json"
SECRETS_FILE = DATA_DIR / "secrets.json"

COMPANIES = ["请先新增公司"]


def default_region(company):
    if "杭州" in company:
        return "浙江"
    if "成都" in company:
        return "四川"
    if any(word in company for word in ("湖北", "浠水", "红安", "黄冈", "麻城")):
        return "湖北"
    return "深圳"


def ensure_package(import_name, package_name=None):
    try:
        return __import__(import_name)
    except ImportError:
        if getattr(sys, "frozen", False):
            raise RuntimeError(f"程序组件 {import_name} 缺失，请重新下载完整安装包")
        installer = Path(sys.executable)
        if installer.name.lower() == "pythonw.exe":
            console_python = installer.with_name("python.exe")
            if console_python.exists():
                installer = console_python
        subprocess.check_call([str(installer), "-m", "pip", "install", package_name or import_name])
        return __import__(import_name)


def version_tuple(value):
    return tuple(int(part) for part in re.findall(r"\d+", value)[:4])


def download_update(manifest, root, status):
    try:
        url = manifest["url"]
        expected = manifest["sha256"].lower()
        destination = Path(tempfile.gettempdir()) / f"etax-assistant-{manifest['version']}.exe.new"
        request = urllib.request.Request(url, headers={"User-Agent": "ETaxLoginAssistant-Updater"})
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        actual = hashlib.sha256(destination.read_bytes()).hexdigest().lower()
        if actual != expected:
            destination.unlink(missing_ok=True)
            raise RuntimeError("更新文件校验失败")
        current_exe = Path(sys.executable).resolve()
        quote = lambda path: str(path).replace("'", "''")
        command = (
            f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
            "Start-Sleep -Milliseconds 500; "
            f"Move-Item -LiteralPath '{quote(destination)}' -Destination '{quote(current_exe)}' -Force; "
            f"Start-Process -FilePath '{quote(current_exe)}'"
        )
        subprocess.Popen([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command
        ], creationflags=0x08000000)
        root.after(0, root.destroy)
    except Exception as exc:
        status(f"自动更新失败：{exc}")


def check_for_updates(root, status):
    if not getattr(sys, "frozen", False):
        return
    try:
        request = urllib.request.Request(
            UPDATE_MANIFEST_URL,
            headers={"User-Agent": "ETaxLoginAssistant-Updater", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            manifest = json.loads(response.read().decode("utf-8"))
        if version_tuple(manifest.get("version", "0")) <= version_tuple(APP_VERSION):
            return

        def ask_update():
            if messagebox.askyesno(
                "发现新版本",
                f"发现版本 {manifest['version']}，是否立即更新？\n\n更新不会删除本机公司资料。",
                parent=root,
            ):
                status("正在下载并校验新版本……")
                threading.Thread(
                    target=download_update, args=(manifest, root, status), daemon=True
                ).start()

        root.after(0, ask_update)
    except Exception:
        # 网络不可用时静默跳过，不影响正常登录。
        return


def resource_path(relative):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def company_list_from(config):
    saved = config.get("company_list")
    if isinstance(saved, list):
        return [name for name in saved if isinstance(name, str) and name.strip()]
    return list(COMPANIES)


def write_config(config):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def get_region_settings(region):
    settings = dict(REGIONS.get(region, {"portal": "", "login_host": ""}))
    custom_url = load_config().get("region_urls", {}).get(region, "").strip()
    if custom_url:
        settings["portal"] = custom_url
        host = urlparse(custom_url).hostname or ""
        if host.startswith("tpass."):
            settings["login_host"] = host
    return settings


def save_region_url(region, url):
    config = load_config()
    urls = config.setdefault("region_urls", {})
    if url.strip():
        urls[region] = url.strip()
    else:
        urls.pop(region, None)
    write_config(config)


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi_encrypt(text):
    raw = text.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), APP_NAME, None, None, None, 1, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(result.pbData, result.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def dpapi_decrypt(encoded):
    raw = base64.b64decode(encoded)
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def load_secrets():
    try:
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_secrets(secrets):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SECRETS_FILE.write_text(json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8")


def save_password(company, account, password):
    secrets = load_secrets()
    secrets[company] = {
        "account": dpapi_encrypt(account),
        "password": dpapi_encrypt(password),
    }
    write_secrets(secrets)


def load_account(company):
    encoded = load_secrets().get(company, {}).get("account", "")
    if not encoded:
        return ""
    try:
        return dpapi_decrypt(encoded)
    except Exception:
        return ""


def load_password(company, account):
    item = load_secrets().get(company, {})
    if load_account(company) != account or not item.get("password"):
        return ""
    try:
        return dpapi_decrypt(item["password"])
    except Exception:
        return ""


def delete_password(company):
    secrets = load_secrets()
    if company in secrets:
        secrets.pop(company)
        write_secrets(secrets)


def portable_bundle_path():
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "迁移资料.enc"


def import_portable_bundle_if_needed():
    """新电脑首次启动时，将口令加密的迁移资料重新绑定到本机 DPAPI。"""
    bundle = portable_bundle_path()
    if CONFIG_FILE.exists() or not bundle.exists():
        return
    ensure_package("cryptography")
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    package = json.loads(bundle.read_text(encoding="utf-8"))
    salt = base64.b64decode(package["salt"])
    prompt_root = tk.Tk()
    prompt_root.withdraw()
    try:
        for _attempt in range(3):
            password = simpledialog.askstring(
                "首次部署",
                "请输入迁移密码以导入公司账号资料：",
                show="●",
                parent=prompt_root,
            )
            if password is None:
                return
            key = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            ).derive(password.encode("utf-8"))
            try:
                raw = Fernet(base64.urlsafe_b64encode(key)).decrypt(package["token"].encode("ascii"))
                payload = json.loads(raw.decode("utf-8"))
            except (InvalidToken, ValueError, KeyError, json.JSONDecodeError):
                messagebox.showerror("迁移密码错误", "迁移密码不正确，请重新输入。", parent=prompt_root)
                continue
            write_config(payload.get("config", {}))
            for company, item in payload.get("credentials", {}).items():
                account = item.get("account", "")
                password_value = item.get("password", "")
                if account and password_value:
                    save_password(company, account, password_value)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / "portable_imported.ok").write_text("ok", encoding="ascii")
            messagebox.showinfo("导入完成", "公司资料已安全导入并绑定到这台电脑。", parent=prompt_root)
            return
        messagebox.showerror("导入失败", "迁移密码连续输入错误，资料尚未导入。", parent=prompt_root)
    finally:
        prompt_root.destroy()


def save_config(company, region, tax_id, _user_id=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    profiles = config.setdefault("companies", {})
    # 办税人员账号与密码均只写入 Windows DPAPI 加密的 secrets.json。
    profiles[company] = {"region": region, "tax_id": tax_id}
    config["selected_company"] = company
    if company not in company_list_from(config):
        config["company_list"] = company_list_from(config) + [company]
    write_config(config)


def migrate_sensitive_storage():
    """将旧版本配置里的明文办税人员账号迁移到 DPAPI 加密存储。"""
    config = load_config()
    secrets = load_secrets()
    config_changed = False
    secrets_changed = False
    for company, profile in config.get("companies", {}).items():
        old_account = profile.pop("user_id", "")
        if old_account:
            config_changed = True
        item = secrets.get(company, {})
        stored = item.get("account", "")
        account = ""
        encrypted = False
        if stored:
            try:
                account = dpapi_decrypt(stored)
                encrypted = True
            except Exception:
                account = stored  # 兼容旧版本中曾以明文保存的账号。
        account = account or old_account
        if account and not encrypted:
            item["account"] = dpapi_encrypt(account)
            secrets[company] = item
            secrets_changed = True
    if config_changed:
        write_config(config)
    if secrets_changed:
        write_secrets(secrets)


def selectors_for(page, patterns):
    candidates = []
    for pattern in patterns:
        candidates.extend([
            page.get_by_placeholder(re.compile(pattern, re.I)),
            page.get_by_label(re.compile(pattern, re.I)),
        ])
    return candidates


def fill_first(page, patterns, value):
    # 优先使用页面截图中稳定的 placeholder；再回退到可访问性标签。
    for locator in selectors_for(page, patterns):
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.fill(value)
                return True
        except Exception:
            pass
    return False


def fill_login_form(page, tax_id, user_id, password, timeout=25):
    """等待单页应用渲染完成，并反复尝试填写三个企业业务输入框。"""
    deadline = time.monotonic() + timeout
    result = (False, False, False)
    while time.monotonic() < deadline:
        click_enterprise(page)
        ok1 = fill_first(page, [r"统一社会信用代码/纳税人识别号", r"统一社会信用代码", r"纳税人识别号"], tax_id)
        ok2 = fill_first(page, [r"居民身份证号码/手机号码/用户名", r"居民身份证号码", r"身份证号码", r"手机号码", r"用户名"], user_id)
        ok3 = fill_first(page, [r"个人用户密码", r"用户密码"], password)
        result = (ok1, ok2, ok3)
        if all(result):
            return result
        page.wait_for_timeout(500)
    return result


def click_login_once(page):
    """三项信息填写成功后，只提交一次，避免触发登录风控。"""
    candidates = (
        page.get_by_role("button", name=re.compile(r"^登录$")),
        page.locator("button").filter(has_text=re.compile(r"^\s*登录\s*$")),
        page.get_by_text("登录", exact=True),
    )
    for locator in candidates:
        try:
            if locator.count() and locator.first.is_visible() and locator.first.is_enabled():
                locator.first.click()
                return True
        except Exception:
            pass
    return False


def wait_and_request_sms(page, status, timeout=300):
    """等待用户手动完成图片验证，然后只点击一次获取短信验证码。"""
    status("请手动完成图片验证；通过后程序会自动获取短信验证码。")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = (
            page.get_by_role("button", name=re.compile(r"^获取验证码$")),
            page.get_by_text("获取验证码", exact=True),
        )
        for locator in candidates:
            try:
                if locator.count() and locator.first.is_visible() and locator.first.is_enabled():
                    locator.first.click()
                    page.wait_for_timeout(800)
                    return True
            except Exception:
                pass
        page.wait_for_timeout(500)
    return False


def click_enterprise(page):
    for name in ("企业业务", "企业登录"):
        try:
            item = page.get_by_text(name, exact=True)
            if item.count() and item.first.is_visible():
                item.first.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            pass


def open_login_page(page, region):
    """从电子税务局首页进入带有一次性 state 参数的官方登录页。"""
    settings = get_region_settings(region)
    if not settings["portal"]:
        raise RuntimeError(f"{region}电子税务局网址尚未配置")
    page.goto(settings["portal"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    # 如果首页尚未跳转到统一登录页，点击右上角登录入口。
    is_login_page = (settings["login_host"] and settings["login_host"] in page.url) or "tpass." in page.url
    if not is_login_page:
        for locator in (
            page.get_by_role("button", name=re.compile(r"^登录$")),
            page.get_by_role("link", name=re.compile(r"^登录$")),
            page.get_by_text("登录", exact=True),
        ):
            try:
                if locator.count() and locator.first.is_visible():
                    locator.first.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass
    page.wait_for_timeout(1000)


def find_installed_browser():
    """只查找 Windows 正式安装的浏览器，不使用自动化工具自带的浏览器。"""
    registry_paths = {}
    for executable_name in ("chrome.exe", "msedge.exe", "firefox.exe"):
        for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}"
                with winreg.OpenKey(root_key, key_path) as key:
                    registry_paths[executable_name] = winreg.QueryValue(key, None)
                    break
            except OSError:
                pass
    candidates = [
        ("Google Chrome", "chromium", registry_paths.get("chrome.exe")),
        ("Google Chrome", "chromium", Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe"),
        ("Google Chrome", "chromium", Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe"),
        ("Google Chrome", "chromium", Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
        ("Microsoft Edge", "chromium", registry_paths.get("msedge.exe")),
        ("Microsoft Edge", "chromium", Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe"),
        ("Microsoft Edge", "chromium", Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe"),
        ("Microsoft Edge", "chromium", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe"),
        ("Mozilla Firefox", "firefox", registry_paths.get("firefox.exe")),
        ("Mozilla Firefox", "firefox", Path(os.environ.get("PROGRAMFILES", "")) / "Mozilla Firefox/firefox.exe"),
        ("Mozilla Firefox", "firefox", Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Mozilla Firefox/firefox.exe"),
    ]
    for name, engine, candidate in candidates:
        if candidate and Path(candidate).is_file():
            return name, engine, str(candidate)
    raise RuntimeError("未找到系统安装的 Google Chrome、Microsoft Edge 或 Mozilla Firefox")


def cdp_ready(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def open_real_chromium(executable, profile_name, legacy_port=None):
    if legacy_port and cdp_ready(legacy_port):
        return legacy_port
    profile = DATA_DIR / profile_name
    profile.mkdir(parents=True, exist_ok=True)
    port_file = profile / "DevToolsActivePort"
    try:
        saved_port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        if cdp_ready(saved_port):
            return saved_port
    except Exception:
        pass
    subprocess.Popen([
        executable,
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        "--start-maximized",
        "--no-first-run",
        "about:blank",
    ])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
            if cdp_ready(port):
                return port
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("浏览器已启动，但自动填写连接超时")


def open_preferred_browser(playwright):
    name, engine, executable = find_installed_browser()
    if engine == "chromium":
        profile_name = "chrome-profile" if name == "Google Chrome" else "edge-profile-v2"
        legacy_port = 9222 if name == "Microsoft Edge" else None
        port = open_real_chromium(executable, profile_name, legacy_port)
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        return browser, browser.contexts[0], name
    context = playwright.firefox.launch_persistent_context(
        str(DATA_DIR / "firefox-profile"), executable_path=executable,
        headless=False, no_viewport=True
    )
    return None, context, name


def unique_download_path(folder, filename):
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", filename).strip(" .")
    candidate = folder / safe_name
    stem, suffix = candidate.stem, candidate.suffix
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def install_download_handler(context, status):
    downloads_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"

    def handle_download(download):
        try:
            temp_path = Path(download.path())
            suggested = download.suggested_filename or ""
            with temp_path.open("rb") as downloaded_file:
                is_pdf = downloaded_file.read(5) == b"%PDF-"
            if not is_pdf and not suggested.lower().endswith(".pdf"):
                status(f"文件已下载：{suggested or temp_path.name}")
                return
            if suggested.lower().endswith(".pdf"):
                filename = suggested
            else:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                filename = f"税务报表_{stamp}.pdf"
            destination = unique_download_path(downloads_dir, filename)
            download.save_as(str(destination))
            status(f"PDF 已保存：{destination}")
        except Exception as exc:
            status(f"PDF 保存失败：{exc}")

    def attach(page):
        page.on("download", handle_download)
        page.on("dialog", lambda dialog: dialog.dismiss())

    for existing_page in context.pages:
        attach(existing_page)
    context.on("page", attach)


class NativeRect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class NativeWindow:
    def __init__(self, handle, title):
        self.handle, self.title = handle, title

    def window_text(self):
        return self.title

    def rectangle(self):
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.handle, ctypes.byref(rect))
        return NativeRect(rect.left, rect.top, rect.right, rect.bottom)

    def maximize(self):
        ctypes.windll.user32.ShowWindow(self.handle, 3)

    def set_focus(self):
        ctypes.windll.user32.SetForegroundWindow(self.handle)


def native_browser_windows(title_hint):
    windows = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    @callback_type
    def collect(handle, _param):
        if ctypes.windll.user32.IsWindowVisible(handle):
            length = ctypes.windll.user32.GetWindowTextLengthW(handle)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(handle, buffer, length + 1)
                title = buffer.value
                if title_hint.lower() in title.lower():
                    windows.append(NativeWindow(handle, title))
        return True

    ctypes.windll.user32.EnumWindows(collect, 0)
    foreground = ctypes.windll.user32.GetForegroundWindow()
    windows.sort(key=lambda item: (
        0 if ("税务" in item.title or "chinatax" in item.title.lower()) else 1,
        0 if item.handle == foreground else 1,
    ))
    return windows


def native_mouse_click(x, y):
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


def native_ctrl_a():
    user32 = ctypes.windll.user32
    user32.keybd_event(0x11, 0, 0, 0)
    user32.keybd_event(0x41, 0, 0, 0)
    user32.keybd_event(0x41, 0, 0x0002, 0)
    user32.keybd_event(0x11, 0, 0x0002, 0)


def native_escape():
    """关闭 Chrome 密码建议等浮层，避免后续点击误选已保存的密码。"""
    user32 = ctypes.windll.user32
    user32.keybd_event(0x1B, 0, 0, 0)
    user32.keybd_event(0x1B, 0, 0x0002, 0)


def native_send_text(text):
    user32 = ctypes.windll.user32
    for character in text:
        code = ord(character)
        user32.keybd_event(0, code, 0x0004, 0)
        user32.keybd_event(0, code, 0x0004 | 0x0002, 0)
        time.sleep(0.004)


def fill_login_in_system_browser(browser_name, tax_id, user_id, password, status, timeout=25):
    """通过可见画面填写日常浏览器，不依赖 win32api 或调试接口。"""

    title_hint = {
        "Google Chrome": "Chrome",
        "Microsoft Edge": "Edge",
        "Mozilla Firefox": "Firefox",
    }.get(browser_name, browser_name)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for window in native_browser_windows(title_hint)[:3]:
                if fill_visible_login_form(window, tax_id, user_id, password):
                    status("三项信息已填写并点击登录，请手工完成图片验证。")
                    return True
        except Exception:
            pass
        time.sleep(0.15)
    status("已进入登录页，但未识别到全部输入框，请手工补充。")
    return False


def fill_visible_login_form(window, tax_id, user_id, password):
    """根据登录页蓝色提交按钮定位三个输入框，模拟人工输入并提交。"""
    from PIL import ImageGrab

    rect = window.rectangle()
    if rect.width() < 600 or rect.height() < 400:
        return False
    image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).convert("RGB")
    width, height = image.size
    candidate_rows = []
    for y in range(int(height * 0.12), int(height * 0.9)):
        best = (-1, -1)
        start = -1
        for x in range(width):
            red, green, blue = image.getpixel((x, y))
            is_blue = blue >= 175 and 45 <= red <= 115 and 90 <= green <= 180 and blue >= red + 85
            if is_blue and start < 0:
                start = x
            if (not is_blue or x == width - 1) and start >= 0:
                end = x if is_blue and x == width - 1 else x - 1
                if end - start > best[1] - best[0]:
                    best = (start, end)
                start = -1
        run_width = best[1] - best[0]
        if 150 <= run_width <= int(width * 0.85):
            candidate_rows.append((y, best[0], best[1]))
    groups = []
    for row in candidate_rows:
        if groups and row[0] == groups[-1][-1][0] + 1:
            groups[-1].append(row)
        else:
            groups.append([row])
    groups = [group for group in groups if 18 <= len(group) <= 90]
    if not groups:
        return False
    # 只接受登录卡片右半区域、页面中上部的宽按钮，避免将首页其他蓝色模块误判。
    groups = [
        group for group in groups
        if width * 0.45 <= (min(row[1] for row in group) + max(row[2] for row in group)) / 2 <= width * 0.88
        and height * 0.30 <= group[len(group) // 2][0] <= height * 0.72
    ]
    if not groups:
        return False
    group = max(groups, key=lambda rows: max(row[2] - row[1] for row in rows))
    button_height = len(group)
    button_x = (min(row[1] for row in group) + max(row[2] for row in group)) // 2
    button_y = group[len(group) // 2][0]
    values = (tax_id, user_id, password)
    offsets = (-4.5, -3.0, -1.5)
    for value, multiplier in zip(values, offsets):
        input_y = int(button_y + multiplier * button_height)
        native_mouse_click(rect.left + button_x, rect.top + input_y)
        native_ctrl_a()
        native_send_text(value)
    # 密码框获得焦点后 Chrome 会弹出密码建议；先关闭浮层，防止点击登录时误选建议。
    native_escape()
    # 等待网页完成最后一个密码框的输入校验，再单独点击提交按钮。
    time.sleep(0.4)
    native_mouse_click(rect.left + button_x, rect.top + button_y)
    # 若页面仍保持原样且按钮仍是亮蓝色，说明第一次点击未被网页接收，只补点一次。
    time.sleep(0.8)
    check = ImageGrab.grab(bbox=(
        rect.left + button_x - 3,
        rect.top + button_y - 3,
        rect.left + button_x + 4,
        rect.top + button_y + 4,
    )).convert("RGB")
    blue_pixels = 0
    for red, green, blue in check.getdata():
        if blue >= 175 and 45 <= red <= 115 and 90 <= green <= 180 and blue >= red + 85:
            blue_pixels += 1
    if blue_pixels >= 20:
        native_mouse_click(rect.left + button_x, rect.top + button_y)
    return True


def click_visible_sms_button(window):
    """识别短信验证页右侧较短的蓝色“获取验证码”按钮。"""
    from PIL import ImageGrab

    rect = window.rectangle()
    if rect.width() < 600 or rect.height() < 400:
        return False
    image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).convert("RGB")
    width, height = image.size
    rows = []
    for y in range(int(height * 0.20), int(height * 0.78)):
        best = (-1, -1)
        start = -1
        for x in range(width):
            red, green, blue = image.getpixel((x, y))
            is_blue = blue >= 175 and 45 <= red <= 115 and 90 <= green <= 180 and blue >= red + 85
            if is_blue and start < 0:
                start = x
            if (not is_blue or x == width - 1) and start >= 0:
                end = x if is_blue and x == width - 1 else x - 1
                if end - start > best[1] - best[0]:
                    best = (start, end)
                start = -1
        run_width = best[1] - best[0]
        if 70 <= run_width <= min(280, int(width * 0.28)):
            rows.append((y, best[0], best[1]))
    groups = []
    for row in rows:
        # 按钮中央的白色文字会打断蓝色像素，允许中间出现少量空行。
        if groups and row[0] <= groups[-1][-1][0] + 16:
            groups[-1].append(row)
        else:
            groups.append([row])
    groups = [
        group for group in groups
        if 20 <= group[-1][0] - group[0][0] + 1 <= 75
        and width * 0.50 <= (min(row[1] for row in group) + max(row[2] for row in group)) / 2 <= width * 0.72
    ]
    if not groups:
        return False
    group = max(groups, key=lambda item: max(row[2] - row[1] for row in item))
    x = (min(row[1] for row in group) + max(row[2] for row in group)) // 2
    y = (group[0][0] + group[-1][0]) // 2
    native_mouse_click(rect.left + x, rect.top + y)
    return True


def wait_and_click_sms_in_system_browser(browser_name, status, timeout=300, announce=True):
    """等待用户完成人工图片验证，进入短信页后只点击一次获取验证码。"""
    title_hint = {
        "Google Chrome": "Chrome",
        "Microsoft Edge": "Edge",
        "Mozilla Firefox": "Firefox",
    }.get(browser_name, browser_name)
    if announce:
        status("请完成人工图片验证；通过后将自动点击一次“获取验证码”。")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for window in native_browser_windows(title_hint)[:3]:
                if click_visible_sms_button(window):
                    status("已点击获取验证码，自动操作已结束，请手工输入短信验证码。")
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    status("等待短信验证页超时，请手工点击“获取验证码”。")
    return False


def click_visible_orange_login(window):
    """在浏览器上方右侧识别税务首页的橙色登录按钮并模拟鼠标点击。"""
    from PIL import ImageGrab

    rect = window.rectangle()
    if rect.width() < 500 or rect.height() < 300:
        return False
    left = rect.left + int(rect.width() * 0.52)
    top = rect.top
    right = rect.right
    bottom = rect.top + int(rect.height() * 0.34)
    image = ImageGrab.grab(bbox=(left, top, right, bottom)).convert("RGB")
    width, height = image.size

    candidate_rows = []
    for y in range(height):
        longest_start = -1
        longest_end = -1
        run_start = -1
        for x in range(width):
            red, green, blue = image.getpixel((x, y))
            orange = red >= 215 and 70 <= green <= 190 and blue <= 105 and red >= green + 45
            if orange and run_start < 0:
                run_start = x
            if (not orange or x == width - 1) and run_start >= 0:
                run_end = x if orange and x == width - 1 else x - 1
                if run_end - run_start > longest_end - longest_start:
                    longest_start, longest_end = run_start, run_end
                run_start = -1
        if longest_end - longest_start >= 28:
            candidate_rows.append((y, longest_start, longest_end))

    groups = []
    for row in candidate_rows:
        if groups and row[0] == groups[-1][-1][0] + 1:
            groups[-1].append(row)
        else:
            groups.append([row])
    groups = [group for group in groups if len(group) >= 8]
    if not groups:
        return False
    group = max(groups, key=lambda rows: (len(rows), max(r[2] - r[1] for r in rows)))
    center_y = group[len(group) // 2][0]
    center_x = (min(row[1] for row in group) + max(row[2] for row in group)) // 2
    native_mouse_click(left + center_x, top + center_y)
    return True


def click_login_in_system_browser(browser_name, tax_id, user_id, password, status, timeout=18):
    """通过 Windows 原生接口模拟人工点击，不接管或调试浏览器。"""

    title_hint = {
        "Google Chrome": "Chrome",
        "Microsoft Edge": "Edge",
        "Mozilla Firefox": "Firefox",
    }.get(browser_name, browser_name)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for window in native_browser_windows(title_hint)[:3]:
                try:
                    window.maximize()
                    window.set_focus()
                    time.sleep(0.1)
                except Exception:
                    pass
                if click_visible_orange_login(window):
                    status("已点击登录，正在识别并填写三项登录信息……")
                    time.sleep(0.25)
                    filled = fill_login_in_system_browser(
                        browser_name, tax_id, user_id, password, status
                    )
                    return filled
        except Exception:
            pass
        time.sleep(0.12)
    status("官网已打开，但未识别到登录按钮，请手工点击右上角“登录”。")
    return False


def start_browser(company, region, tax_id, user_id, password, status):
    try:
        if company and tax_id and user_id and password:
            save_config(company, region, tax_id, user_id)
            save_password(company, user_id, password)
        settings = get_region_settings(region)
        if not settings.get("portal"):
            raise RuntimeError(f"{region}电子税务局网址尚未配置")
        browser_name, _engine, executable = find_installed_browser()
        # 不添加远程调试、自动化或独立 user-data-dir 参数，确保使用浏览器的
        # 默认用户资料、实名环境和税务组件，与用户平时手工打开完全一致。
        subprocess.Popen([
            executable,
            "--new-window",
            "--start-maximized",
            "--force-renderer-accessibility",
            settings["portal"],
        ])
        # 独立监控短信验证页，不依赖自动填写或登录步骤是否成功返回。
        threading.Thread(
            target=wait_and_click_sms_in_system_browser,
            args=(browser_name, status, 300, False),
            daemon=True,
        ).start()
        status(f"已用日常 {browser_name} 打开官网，正在点击登录……")
        click_login_in_system_browser(browser_name, tax_id, user_id, password, status)
    except Exception as exc:
        status(f"启动失败：{exc}")


def main():
    import_portable_bundle_if_needed()
    migrate_sensitive_storage()
    config = load_config()
    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("1240x820")
    root.minsize(1040, 720)
    root.resizable(True, True)
    root.configure(bg="#0B1426")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    navy = "#0B1426"
    navy_soft = "#13233D"
    gold = "#C9A45C"
    gold_hover = "#D9B76F"
    card = "#F7F5F0"
    ink = "#15233A"
    muted = "#6C7583"
    style.configure("TFrame", background=card)
    style.configure("Header.TFrame", background=navy)
    style.configure("Brand.TLabel", background=navy, font=("Microsoft YaHei UI", 22, "bold"), foreground="#FFFFFF")
    style.configure("HeaderHint.TLabel", background=navy, font=("Microsoft YaHei UI", 10), foreground="#AAB6C9")
    style.configure("Badge.TLabel", background=navy_soft, font=("Microsoft YaHei UI", 9, "bold"), foreground="#E6CA91", padding=(12, 6))
    style.configure("Hint.TLabel", background=card, font=("Microsoft YaHei UI", 10), foreground=muted)
    style.configure("Status.TLabel", background="#EEEAE1", font=("Microsoft YaHei UI", 10), foreground="#4F5B6D", padding=(12, 8))
    style.configure("Field.TLabel", background=card, font=("Microsoft YaHei UI", 10, "bold"), foreground="#34445D")
    style.configure("CurrentCompany.TLabel", background=card, font=("Microsoft YaHei UI", 11, "bold"), foreground="#9A7432")
    style.configure("Company.TCombobox", fieldbackground="#FFFDF8", foreground=ink, bordercolor="#C9A45C", lightcolor="#C9A45C", darkcolor="#C9A45C", padding=9)
    style.map("Company.TCombobox", fieldbackground=[("readonly", "#FFFDF8")], foreground=[("readonly", ink)])
    style.configure("TEntry", fieldbackground="#FFFFFF", foreground=ink, bordercolor="#D4D8DE", lightcolor="#D4D8DE", darkcolor="#D4D8DE", padding=5)
    style.map("TEntry", bordercolor=[("focus", gold)], lightcolor=[("focus", gold)], darkcolor=[("focus", gold)])
    style.configure("TCombobox", fieldbackground="#FFFFFF", foreground=ink, bordercolor="#D4D8DE", lightcolor="#D4D8DE", darkcolor="#D4D8DE", padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")], foreground=[("readonly", ink)])
    style.configure("TSeparator", background="#D9D4C9")
    style.configure("Secondary.TButton", font=("Microsoft YaHei UI", 10), padding=(9, 8), background="#E9EDF2", foreground="#263750", bordercolor="#CAD1DA")
    style.map("Secondary.TButton", background=[("active", "#DCE3EB")], foreground=[("active", navy)])
    style.configure("Danger.TButton", font=("Microsoft YaHei UI", 10), padding=(9, 8), background="#F3E7E5", foreground="#8A3F3A", bordercolor="#E3C7C3")
    style.map("Danger.TButton", background=[("active", "#EAD6D2")], foreground=[("active", "#74312D")])
    style.configure("Primary.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=(14, 11), background=gold, foreground=navy, bordercolor=gold)
    style.map("Primary.TButton", background=[("active", gold_hover), ("pressed", "#B58E45")], foreground=[("active", navy), ("pressed", navy)])

    shell = tk.Frame(root, bg=navy, highlightthickness=0)
    shell.pack(fill="both", expand=True)
    header = ttk.Frame(shell, style="Header.TFrame", padding=(54, 22))
    header.pack(fill="x")
    brand = ttk.Frame(header, style="Header.TFrame")
    brand.pack(side="left")
    ttk.Label(brand, text="云境 · 企业税务工作台", style="Brand.TLabel").pack(anchor="w")
    ttk.Label(brand, text="SECURE ENTERPRISE TAX WORKSPACE", style="HeaderHint.TLabel").pack(anchor="w", pady=(3, 0))
    ttk.Label(header, text=f"PRO  ·  v{APP_VERSION}", style="Badge.TLabel").pack(side="right", anchor="e")

    frame = ttk.Frame(shell, padding=(54, 28))
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="企业身份与登录资料管理  ·  敏感信息仅在本机加密保存", style="Hint.TLabel").pack(anchor="w", pady=(0, 10))

    fields = ttk.Frame(frame)
    fields.pack(fill="both", expand=True, pady=(8, 0))
    fields.columnconfigure(0, weight=1, uniform="form")
    fields.columnconfigure(1, weight=1, uniform="form")
    ttk.Label(fields, text="当前选择公司", style="CurrentCompany.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(fields, text="搜索公司", style="Field.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 14), pady=(12, 0))
    ttk.Label(fields, text="公司档案管理", style="Field.TLabel").grid(row=2, column=1, sticky="w", padx=(14, 0), pady=(12, 0))
    search_row = ttk.Frame(fields)
    search_row.grid(row=3, column=0, sticky="ew", padx=(0, 14), pady=(5, 14))
    search_var = tk.StringVar()
    search_entry = ttk.Entry(search_row, textvariable=search_var, font=("Microsoft YaHei UI", 10))
    search_entry.pack(side="left", fill="x", expand=True)

    search_popup = tk.Toplevel(root)
    search_popup.withdraw()
    search_popup.overrideredirect(True)
    search_popup.transient(root)
    search_popup.configure(bg="#C9A45C")
    result_list = tk.Listbox(
        search_popup,
        font=("Microsoft YaHei UI", 10),
        bg="#FFFFFF",
        fg=ink,
        selectbackground=navy_soft,
        selectforeground="#FFFFFF",
        borderwidth=1,
        relief="solid",
        activestyle="none",
    )
    result_list.pack(fill="both", expand=True, padx=1, pady=1)

    company_names = company_list_from(config)
    if not company_names:
        company_names = list(COMPANIES)
    selected_name = config.get("selected_company")
    if selected_name not in company_names:
        selected_name = company_names[0]
    company_var = tk.StringVar(value=selected_name)
    company_box = ttk.Combobox(fields, textvariable=company_var, values=company_names, state="readonly", font=("Microsoft YaHei UI", 14, "bold"), style="Company.TCombobox")
    company_box.grid(row=1, column=0, columnspan=2, ipady=7, pady=(6, 6), sticky="ew")

    selected_profile = config.get("companies", {}).get(company_var.get(), {})
    ttk.Label(fields, text="选择地区", style="Field.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 14))
    region_var = tk.StringVar(value=selected_profile.get("region") or default_region(company_var.get()))
    region_box = ttk.Combobox(fields, textvariable=region_var, values=list(REGIONS), state="readonly", width=55, font=("Microsoft YaHei UI", 10))
    region_box.grid(row=5, column=0, ipady=4, padx=(0, 14), pady=(5, 14), sticky="ew")

    ttk.Label(fields, text="统一社会信用代码 / 纳税人识别号", style="Field.TLabel").grid(row=4, column=1, sticky="w", padx=(14, 0))
    tax_var = tk.StringVar(value=selected_profile.get("tax_id", ""))
    ttk.Entry(fields, textvariable=tax_var, font=("Microsoft YaHei UI", 10)).grid(row=5, column=1, ipady=6, padx=(14, 0), pady=(5, 14), sticky="ew")

    ttk.Label(fields, text="当前地区电子税务局网址（可手工维护）", style="Field.TLabel").grid(row=6, column=0, columnspan=2, sticky="w")
    url_row = ttk.Frame(fields)
    url_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(5, 14))
    region_url_var = tk.StringVar(value=get_region_settings(region_var.get()).get("portal", ""))
    region_url_entry = ttk.Entry(url_row, textvariable=region_url_var, font=("Microsoft YaHei UI", 10))
    region_url_entry.pack(side="left", fill="x", expand=True)

    def load_region_url(_event=None):
        region_url_var.set(get_region_settings(region_var.get()).get("portal", ""))

    def save_current_region_url():
        region = region_var.get()
        url = region_url_var.get().strip()
        if not region:
            status("请先选择地区。")
            return
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                status("网址格式不正确，请输入以 http:// 或 https:// 开头的完整网址。")
                return
            if "state=" in url.lower():
                status("该链接含一次性 state 参数，请填写电子税务局首页网址。")
                return
        save_region_url(region, url)
        load_region_url()
        status(f"已保存{region}电子税务局网址。")

    def restore_region_url():
        region = region_var.get()
        save_region_url(region, "")
        load_region_url()
        status(f"已恢复{region}的程序内置网址。")

    ttk.Button(url_row, text="保存网址", command=save_current_region_url, style="Secondary.TButton").pack(side="left", padx=(8, 4))
    ttk.Button(url_row, text="恢复默认", command=restore_region_url, style="Secondary.TButton").pack(side="left", padx=(4, 0))
    region_box.bind("<<ComboboxSelected>>", load_region_url)

    ttk.Label(fields, text="办税人员身份证号 / 手机号 / 用户名（加密保存）", style="Field.TLabel").grid(row=8, column=0, sticky="w", padx=(0, 14))
    user_var = tk.StringVar(value=load_account(company_var.get()))
    user_row = ttk.Frame(fields)
    user_row.grid(row=9, column=0, padx=(0, 14), pady=(5, 14), sticky="ew")
    user_entry = ttk.Entry(user_row, textvariable=user_var, show="●", font=("Microsoft YaHei UI", 10))
    user_entry.pack(side="left", fill="x", expand=True, ipady=6)

    def toggle_user_visibility():
        visible = user_entry.cget("show") == ""
        user_entry.configure(show="●" if visible else "")
        user_visibility_button.configure(text="显示" if visible else "隐藏")

    user_visibility_button = ttk.Button(user_row, text="显示", command=toggle_user_visibility, style="Secondary.TButton", width=6)
    user_visibility_button.pack(side="left", padx=(8, 0))

    ttk.Label(fields, text="个人用户密码（Windows 原生加密保存）", style="Field.TLabel").grid(row=8, column=1, sticky="w", padx=(14, 0))
    pass_var = tk.StringVar()
    if user_var.get():
        pass_var.set(load_password(company_var.get(), user_var.get()))
    password_entry = ttk.Entry(fields, textvariable=pass_var, show="●", font=("Microsoft YaHei UI", 10))
    password_entry.grid(row=9, column=1, ipady=6, padx=(14, 0), pady=(5, 14), sticky="ew")

    def load_company(_event=None):
        profile = load_config().get("companies", {}).get(company_var.get(), {})
        region_var.set(profile.get("region") or default_region(company_var.get()))
        tax_var.set(profile.get("tax_id", ""))
        user_var.set(load_account(company_var.get()))
        user_entry.configure(show="●")
        user_visibility_button.configure(text="显示")
        pass_var.set("")
        if user_var.get():
            pass_var.set(load_password(company_var.get(), user_var.get()))
        load_region_url()

    company_box.bind("<<ComboboxSelected>>", load_company)

    def filter_companies(*_args):
        keyword = search_var.get().strip().lower()
        names = company_list_from(load_config())
        matches = [name for name in names if keyword in name.lower()]
        company_box.configure(values=matches)
        result_list.delete(0, tk.END)
        for name in matches:
            result_list.insert(tk.END, name)
        if keyword and matches:
            root.update_idletasks()
            x = search_entry.winfo_rootx()
            y = search_entry.winfo_rooty() + search_entry.winfo_height()
            width = search_row.winfo_width()
            height = min(180, 28 * len(matches) + 4)
            search_popup.geometry(f"{width}x{height}+{x}+{y}")
            search_popup.deiconify()
            search_popup.lift()
        else:
            search_popup.withdraw()

    def choose_search_result(_event=None):
        selection = result_list.curselection()
        if not selection:
            return
        company_var.set(result_list.get(selection[0]))
        load_company()
        search_var.set("")
        search_popup.withdraw()

    def clear_search():
        search_var.set("")
        search_popup.withdraw()
        search_entry.focus_set()

    ttk.Button(search_row, text="清空", command=clear_search, style="Secondary.TButton").pack(side="left", padx=(8, 0))
    search_var.trace_add("write", filter_companies)
    result_list.bind("<<ListboxSelect>>", choose_search_result)
    result_list.bind("<Return>", choose_search_result)

    def refresh_companies(select_name=None):
        names = company_list_from(load_config())
        company_box.configure(values=names)
        if select_name in names:
            company_var.set(select_name)
        elif names:
            company_var.set(names[0])
        else:
            company_var.set("")
            region_var.set("")
            tax_var.set("")
            user_var.set("")
            pass_var.set("")
            return
        load_company()

    def add_company():
        name = search_var.get().strip()
        if not name:
            status("请先在“搜索公司”框中输入要新增的公司全称。")
            return
        config_now = load_config()
        names = company_list_from(config_now)
        if name in names:
            status("该公司已经存在。")
            return
        config_now["company_list"] = names + [name]
        config_now["selected_company"] = name
        write_config(config_now)
        search_var.set("")
        refresh_companies(name)
        status(f"已新增公司：{name}")

    def rename_company():
        old_name = company_var.get()
        if not old_name:
            return
        new_name = search_var.get().strip()
        if not new_name:
            status("请在“搜索公司”框中输入新的公司名称，再点击重命名。")
            return
        if new_name == old_name:
            status("新公司名称与当前名称相同。")
            return
        config_now = load_config()
        names = company_list_from(config_now)
        if new_name in names:
            status("该公司名称已经存在。")
            return
        config_now["company_list"] = [new_name if name == old_name else name for name in names]
        profiles = config_now.setdefault("companies", {})
        profile = profiles.pop(old_name, None)
        if profile is not None:
            profiles[new_name] = profile
        config_now["selected_company"] = new_name
        account = load_account(old_name)
        if account:
            old_password = load_password(old_name, account)
            if old_password:
                save_password(new_name, account, old_password)
                delete_password(old_name)
        write_config(config_now)
        search_var.set("")
        refresh_companies(new_name)
        status(f"公司已重命名为：{new_name}")

    delete_confirmation = {"name": "", "until": 0.0}
    def delete_company():
        name = company_var.get()
        if not name:
            return
        now = time.monotonic()
        if delete_confirmation["name"] != name or now > delete_confirmation["until"]:
            delete_confirmation["name"] = name
            delete_confirmation["until"] = now + 5
            status(f"再次点击“删除公司”以确认删除：{name}（5秒内有效）")
            return
        config_now = load_config()
        config_now["company_list"] = [item for item in company_list_from(config_now) if item != name]
        profile = config_now.setdefault("companies", {}).pop(name, {})
        delete_password(name)
        remaining = config_now["company_list"]
        config_now["selected_company"] = remaining[0] if remaining else ""
        write_config(config_now)
        refresh_companies(config_now["selected_company"])
        delete_confirmation["name"] = ""
        status(f"已删除公司：{name}")

    company_actions = ttk.Frame(fields)
    company_actions.grid(row=3, column=1, sticky="ew", padx=(14, 0), pady=(5, 14))
    ttk.Button(company_actions, text="新增公司", command=add_company, style="Secondary.TButton").pack(side="left", fill="x", expand=True, padx=(0, 4))
    ttk.Button(company_actions, text="重命名", command=rename_company, style="Secondary.TButton").pack(side="left", fill="x", expand=True, padx=4)
    ttk.Button(company_actions, text="删除公司", command=delete_company, style="Danger.TButton").pack(side="left", fill="x", expand=True, padx=(4, 0))

    status_var = tk.StringVar(value="就绪")
    def status(text):
        root.after(0, status_var.set, text)

    def launch():
        company, region = company_var.get(), region_var.get()
        tax_id, user_id, password = tax_var.get().strip(), user_var.get().strip(), pass_var.get()
        if not region:
            status("该公司所属地区尚未配置税务局入口。")
            return
        if not get_region_settings(region).get("portal"):
            status(f"{region}电子税务局网址尚未配置，后续添加网址后即可启用。")
            return
        if not all((tax_id, user_id, password)):
            status("请填写统一社会信用代码、办税人员账号和个人用户密码。")
            return
        threading.Thread(target=start_browser, args=(company, region, tax_id, user_id, password, status), daemon=True).start()

    def save_profile():
        company, region = company_var.get(), region_var.get()
        tax_id, user_id, password = tax_var.get().strip(), user_var.get().strip(), pass_var.get()
        if not company or not region or not all((tax_id, user_id, password)):
            status("请选择公司和地区，并填写三项登录信息。")
            return
        try:
            save_config(company, region, tax_id, user_id)
            save_password(company, user_id, password)
            status(f"“{company}”的资料已安全保存")
        except Exception as exc:
            status(f"保存失败：{exc}")

    actions = ttk.Frame(frame)
    actions.pack(fill="x")
    ttk.Button(actions, text="保存公司资料", command=save_profile, style="Secondary.TButton").pack(side="left", fill="x", expand=True, padx=(0, 6))
    ttk.Button(actions, text="打开官网并自动填写", command=launch, style="Primary.TButton").pack(side="left", fill="x", expand=True, padx=(6, 0))
    ttk.Label(frame, textvariable=status_var, style="Status.TLabel").pack(fill="x", anchor="w", pady=(13, 0))
    ttk.Separator(frame).pack(fill="x", pady=(12, 8))
    ttk.Label(frame, text="使用日常浏览器打开、自动点击首页登录并填写三项资料；提交与验证由本人完成。", style="Hint.TLabel").pack(anchor="w")
    root.after(
        1500,
        lambda: threading.Thread(target=check_for_updates, args=(root, status), daemon=True).start(),
    )
    root.mainloop()


if __name__ == "__main__":
    main()
