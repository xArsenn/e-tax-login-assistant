import base64
import csv
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tempfile
import urllib.request
import winreg
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import urlparse

APP_NAME = "电子税务局企业登录助手"
APP_VERSION = "2.0.0"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/xArsenn/e-tax-login-assistant/main/latest.json"
BETA_UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/xArsenn/e-tax-login-assistant/main/latest-beta.json"
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
LOGIN_LOG_FILE = DATA_DIR / "login-history.jsonl"
DIAGNOSTIC_LOG_FILE = DATA_DIR / "diagnostic.log"
AUDIT_LOG_FILE = DATA_DIR / "audit-history.jsonl"
BACKUP_DIR = DATA_DIR / "backups"

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
        backup_exe = current_exe.with_suffix(current_exe.suffix + ".previous")
        quote = lambda path: str(path).replace("'", "''")
        command = (
            f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
            "Start-Sleep -Milliseconds 500; "
            f"Copy-Item -LiteralPath '{quote(current_exe)}' -Destination '{quote(backup_exe)}' -Force; "
            f"Move-Item -LiteralPath '{quote(destination)}' -Destination '{quote(current_exe)}' -Force; "
            f"Start-Process -FilePath '{quote(current_exe)}'"
        )
        subprocess.Popen([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command
        ], creationflags=0x08000000)
        root.after(0, root.destroy)
    except Exception as exc:
        status(f"自动更新失败：{exc}")


def check_for_updates(root, status, manual=False):
    if not getattr(sys, "frozen", False):
        if manual:
            status(f"当前已是最新版本 v{APP_VERSION}。")
        return
    try:
        channel = load_config().get("update_channel", "稳定版")
        manifest_url = BETA_UPDATE_MANIFEST_URL if channel == "测试版" else UPDATE_MANIFEST_URL
        request = urllib.request.Request(
            manifest_url,
            headers={"User-Agent": "ETaxLoginAssistant-Updater", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            manifest = json.loads(response.read().decode("utf-8"))
        if version_tuple(manifest.get("version", "0")) <= version_tuple(APP_VERSION):
            if manual:
                status(f"当前已是最新版本 v{APP_VERSION}。")
            return

        def ask_update():
            if messagebox.askyesno(
                "发现新版本",
                f"{channel}通道发现版本 {manifest['version']}，是否立即更新？\n\n更新不会删除本机公司资料，并会保留上一版本用于回退。",
                parent=root,
            ):
                status("正在下载并校验新版本……")
                threading.Thread(
                    target=download_update, args=(manifest, root, status), daemon=True
                ).start()

        root.after(0, ask_update)
    except Exception as exc:
        if manual:
            status(f"检查更新失败：{exc}")
        # 自动检查失败时静默跳过，不影响正常登录。
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
        names = [name for name in saved if isinstance(name, str) and name.strip()]
        favorites = [name for name in config.get("favorite_companies", []) if name in names]
        recent = [name for name in config.get("recent_companies", []) if name in names and name not in favorites]
        return favorites + recent + [name for name in names if name not in favorites and name not in recent]
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
    previous = secrets.get(company, {})
    unchanged = False
    try:
        unchanged = dpapi_decrypt(previous.get("account", "")) == account and dpapi_decrypt(previous.get("password", "")) == password
    except Exception:
        pass
    secrets[company] = {
        "account": dpapi_encrypt(account),
        "password": dpapi_encrypt(password),
        "password_updated_at": previous.get("password_updated_at") if unchanged and previous.get("password_updated_at") else datetime.now().isoformat(timespec="seconds"),
        "password_fingerprint": hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), APP_NAME.encode("utf-8"), 120000
        ).hex(),
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


def save_identity_profile(company, profile):
    secrets = load_secrets()
    item = secrets.setdefault(company, {})
    item["identity_profile"] = dpapi_encrypt(json.dumps(profile, ensure_ascii=False))
    write_secrets(secrets)


def load_identity_profile(company):
    encoded = load_secrets().get(company, {}).get("identity_profile", "")
    if not encoded:
        return {}
    try:
        return json.loads(dpapi_decrypt(encoded))
    except Exception:
        return {}


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


def remove_online_service_settings():
    """清理不再使用的服务端、云同步和机器人通知配置。"""
    config = load_config()
    changed = False
    for key in ("service_url", "last_webhook_reminder"):
        if key in config:
            config.pop(key, None)
            changed = True
    if changed:
        write_config(config)
    secrets_data = load_secrets()
    app_secrets = secrets_data.get("__app__", {})
    if "notification_webhook" in app_secrets:
        app_secrets.pop("notification_webhook", None)
        if not app_secrets:
            secrets_data.pop("__app__", None)
        write_secrets(secrets_data)
    (DATA_DIR / "license.json").unlink(missing_ok=True)


def append_login_history(company, region, result, detail="", elapsed=None, stages=None):
    """记录脱敏登录结果；绝不写入账号、密码或验证码。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "company": company,
        "region": region,
        "result": result,
        "detail": str(detail)[:240],
        "elapsed": round(float(elapsed), 2) if elapsed is not None else None,
        "stages": stages or {},
        "operator": load_config().get("operator", {}).get("name", "本机用户"),
    }
    with LOGIN_LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_login_history(limit=200):
    try:
        lines = LOGIN_LOG_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(records))


def profile_completeness(company):
    config = load_config()
    profile = config.get("companies", {}).get(company, {})
    missing = []
    if not profile.get("region"):
        missing.append("地区")
    if not profile.get("tax_id"):
        missing.append("税号")
    account = load_account(company)
    if not account:
        missing.append("账号")
    elif not load_password(company, account):
        missing.append("密码")
    region = profile.get("region", "")
    if region and not get_region_settings(region).get("portal"):
        missing.append("网址")
    return "资料完整" if not missing else "缺" + "、".join(missing)


def export_encrypted_backup(destination, master_password):
    ensure_package("cryptography")
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    credentials = {}
    for company in company_list_from(load_config()):
        account = load_account(company)
        password = load_password(company, account) if account else ""
        identity_profile = load_identity_profile(company)
        if account or password or identity_profile:
            credentials[company] = {"account": account, "password": password, "identity_profile": identity_profile}
    payload = {
        "format": 2,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config": load_config(),
        "credentials": credentials,
    }
    salt = os.urandom(16)
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000
    ).derive(master_password.encode("utf-8"))
    token = Fernet(base64.urlsafe_b64encode(key)).encrypt(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    package = {"salt": base64.b64encode(salt).decode("ascii"), "token": token.decode("ascii")}
    Path(destination).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")


def import_encrypted_backup(source, master_password):
    ensure_package("cryptography")
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    package = json.loads(Path(source).read_text(encoding="utf-8"))
    salt = base64.b64decode(package["salt"])
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000
    ).derive(master_password.encode("utf-8"))
    payload = json.loads(Fernet(base64.urlsafe_b64encode(key)).decrypt(package["token"].encode("ascii")))
    write_config(payload.get("config", {}))
    for company, item in payload.get("credentials", {}).items():
        if item.get("account"):
            save_password(company, item["account"], item.get("password", ""))
        if item.get("identity_profile"):
            save_identity_profile(company, item["identity_profile"])
    return len(payload.get("config", {}).get("company_list", []))


def configured_browser_preference():
    return load_config().get("browser_preference", "自动选择")


def cached_layout(kind, width, height):
    item = load_config().get("layout_cache", {}).get(kind, {})
    if not item or abs(item.get("width", 0) - width) > 80 or abs(item.get("height", 0) - height) > 80:
        return None
    return item


def save_cached_layout(kind, width, height, **values):
    config = load_config()
    cache = config.setdefault("layout_cache", {})
    cache[kind] = {"width": width, "height": height, **values}
    write_config(config)


def device_identifier():
    seed = f"{os.environ.get('COMPUTERNAME','')}|{os.environ.get('USERNAME','')}|{os.name}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16].upper()


def diagnostic_event(stage, message, error=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    line = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "message": str(message)[:300],
        "error_type": type(error).__name__ if error else "",
    }
    with DIAGNOSTIC_LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(line, ensure_ascii=False) + "\n")


def append_audit(action, company="", detail=""):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operator": load_config().get("operator", {}).get("name", "本机用户"),
        "action": action,
        "company": company,
        "detail": str(detail)[:300],
    }
    with AUDIT_LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_audit_history(limit=1000):
    try:
        lines = AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:
        return []
    result = []
    for line in reversed(lines):
        try:
            result.append(json.loads(line))
        except Exception:
            pass
    return result


def create_local_snapshot():
    """创建仅能由当前Windows用户解密的本地快照。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config": load_config(),
        "secrets": load_secrets(),
    }
    target = BACKUP_DIR / f"snapshot-{datetime.now():%Y%m%d-%H%M%S}.etaxbak"
    target.write_text(dpapi_encrypt(json.dumps(payload, ensure_ascii=False)), encoding="ascii")
    snapshots = sorted(BACKUP_DIR.glob("snapshot-*.etaxbak"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in snapshots[30:]:
        old.unlink(missing_ok=True)
    return target


def restore_local_snapshot(path):
    payload = json.loads(dpapi_decrypt(Path(path).read_text(encoding="ascii")))
    write_config(payload.get("config", {}))
    write_secrets(payload.get("secrets", {}))


def ensure_daily_snapshot():
    today = datetime.now().strftime("%Y-%m-%d")
    config = load_config()
    if config.get("last_local_snapshot") == today:
        return None
    target = create_local_snapshot()
    config = load_config()
    config["last_local_snapshot"] = today
    write_config(config)
    return target


def password_health_rows():
    rows = []
    secrets_data = load_secrets()
    fingerprints = {}
    for company in company_list_from(load_config()):
        item = secrets_data.get(company, {})
        updated = item.get("password_updated_at", "")
        age_days = None
        if updated:
            try:
                age_days = (datetime.now() - datetime.fromisoformat(updated)).days
            except ValueError:
                pass
        fingerprint = item.get("password_fingerprint", "")
        if fingerprint:
            fingerprints.setdefault(fingerprint, []).append(company)
        rows.append({"company": company, "updated": updated[:10], "age_days": age_days, "duplicate": False})
    duplicates = {fingerprint for fingerprint, names in fingerprints.items() if len(names) > 1}
    for row in rows:
        fingerprint = secrets_data.get(row["company"], {}).get("password_fingerprint", "")
        row["duplicate"] = bool(fingerprint and fingerprint in duplicates)
    return rows


def set_vault_password(password):
    config = load_config()
    if not password:
        config.pop("vault", None)
    else:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 300000)
        config["vault"] = {
            "salt": base64.b64encode(salt).decode("ascii"),
            "digest": base64.b64encode(digest).decode("ascii"),
            "idle_minutes": 10,
        }
    write_config(config)


def verify_vault_password(password):
    vault = load_config().get("vault", {})
    if not vault:
        return True
    try:
        salt = base64.b64decode(vault["salt"])
        expected = base64.b64decode(vault["digest"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 300000)
        return secrets_compare(actual, expected)
    except Exception:
        return False


def secrets_compare(left, right):
    if len(left) != len(right):
        return False
    difference = 0
    for a, b in zip(left, right):
        difference |= a ^ b
    return difference == 0


def save_region_url_with_history(region, url):
    config = load_config()
    current = config.get("region_urls", {}).get(region, REGIONS.get(region, {}).get("portal", ""))
    if current and current != url:
        history = config.setdefault("region_url_history", {}).setdefault(region, [])
        history.insert(0, {"url": current, "time": datetime.now().isoformat(timespec="seconds")})
        del history[10:]
    write_config(config)
    save_region_url(region, url)


def sanitized_config():
    config = load_config()
    cleaned = {
        "company_count": len(company_list_from(config)),
        "selected_region": config.get("companies", {}).get(config.get("selected_company", ""), {}).get("region", ""),
        "custom_regions": sorted(config.get("region_urls", {}).keys()),
        "browser_preference": config.get("browser_preference", "自动选择"),
        "update_channel": config.get("update_channel", "稳定版"),
        "has_layout_cache": bool(config.get("layout_cache")),
    }
    return cleaned


def create_diagnostic_bundle(destination):
    """生成不含公司名称、税号、账号、密码和验证码的售后诊断包。"""
    metadata = {
        "app_version": APP_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        "device_id": device_identifier(),
        "settings": sanitized_config(),
    }
    safe_history = []
    for item in read_login_history(100):
        safe_history.append({
            "time": item.get("time"), "region": item.get("region"),
            "result": item.get("result"), "elapsed": item.get("elapsed"),
            "detail": item.get("detail"), "stages": item.get("stages", {}),
        })
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("environment.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        archive.writestr("login-history-sanitized.json", json.dumps(safe_history, ensure_ascii=False, indent=2))
        if DIAGNOSTIC_LOG_FILE.exists():
            archive.writestr("diagnostic.log", DIAGNOSTIC_LOG_FILE.read_text(encoding="utf-8", errors="replace")[-100000:])


TASK_STATES = ["未开始", "已登录", "待人工验证", "待申报", "已申报", "待缴款", "已完成", "异常"]


def current_task_month():
    return datetime.now().strftime("%Y-%m")


def company_task_state(company, month=None):
    month = month or current_task_month()
    return load_config().get("tax_tasks", {}).get(month, {}).get(company, "未开始")


def set_company_task_state(company, state, month=None):
    month = month or current_task_month()
    config = load_config()
    config.setdefault("tax_tasks", {}).setdefault(month, {})[company] = state
    write_config(config)


def login_guard_state(company):
    today = datetime.now().strftime("%Y-%m-%d")
    state = load_config().get("login_guard", {}).get(company, {})
    if state.get("date") != today:
        return {"date": today, "failures": 0}
    return state


def update_login_guard(company, success):
    config = load_config()
    guards = config.setdefault("login_guard", {})
    state = login_guard_state(company)
    state["failures"] = 0 if success else int(state.get("failures", 0)) + 1
    state["last_result"] = "success" if success else "failed"
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    guards[company] = state
    write_config(config)
    return state


def reset_login_guard(company):
    config = load_config()
    config.setdefault("login_guard", {}).pop(company, None)
    write_config(config)


def sms_guard_state(company):
    today = datetime.now().strftime("%Y-%m-%d")
    state = load_config().get("sms_guard", {}).get(company, {})
    if state.get("date") != today:
        return {"date": today, "count": 0, "last_sent": 0}
    return state


def sms_click_allowed(company, cooldown=60, daily_limit=10):
    state = sms_guard_state(company)
    remaining = cooldown - (time.time() - float(state.get("last_sent", 0)))
    if remaining > 0:
        return False, f"短信验证码处于冷却期，请等待 {int(remaining) + 1} 秒"
    if int(state.get("count", 0)) >= daily_limit:
        return False, f"今天已自动获取 {daily_limit} 次验证码，为避免触发限制已停止自动点击"
    return True, ""


def mark_sms_clicked(company):
    config = load_config()
    state = sms_guard_state(company)
    state["count"] = int(state.get("count", 0)) + 1
    state["last_sent"] = time.time()
    config.setdefault("sms_guard", {})[company] = state
    write_config(config)
    return state


def browser_environment_report(region=""):
    report = []
    try:
        name, _engine, executable = find_installed_browser()
        report.append(("浏览器", "通过", f"{name} · {executable}"))
    except Exception as exc:
        report.append(("浏览器", "异常", str(exc)))
    try:
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        dpi = ctypes.windll.user32.GetDpiForSystem() if hasattr(ctypes.windll.user32, "GetDpiForSystem") else 96
        report.append(("显示环境", "通过", f"{width}×{height} · 系统缩放约 {round(dpi / 96 * 100)}%"))
    except Exception as exc:
        report.append(("显示环境", "提示", str(exc)))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice") as key:
            pdf_handler = winreg.QueryValueEx(key, "ProgId")[0]
        report.append(("PDF 默认程序", "通过", pdf_handler))
    except OSError:
        report.append(("PDF 默认程序", "提示", "未检测到明确的 PDF 默认程序"))
    downloads = Path.home() / "Downloads"
    report.append(("下载目录", "通过" if downloads.exists() else "提示", str(downloads)))
    if region:
        url = get_region_settings(region).get("portal", "")
        if not url:
            report.append(("税务局网址", "异常", f"{region}尚未配置网址"))
        else:
            began = time.monotonic()
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=8) as response:
                    code = response.status
                report.append(("官网连通", "通过", f"HTTP {code} · {time.monotonic() - began:.2f} 秒"))
            except Exception as exc:
                report.append(("官网连通", "异常", str(exc)))
    return report


def classify_tax_file(filename):
    text = filename.lower()
    if "完税" in filename or "缴款" in filename:
        return "完税及缴款凭证"
    if "申报" in filename:
        return "申报表"
    if "发票" in filename:
        return "发票资料"
    if "税" in filename or "tax" in text:
        return "其他税务资料"
    return "待分类"


def archive_recent_downloads(company, source, destination, minutes=180):
    source_path, destination_path = Path(source), Path(destination)
    if not source_path.exists():
        raise RuntimeError("下载目录不存在")
    if not company:
        raise RuntimeError("尚未选择公司")
    allowed = {".pdf", ".xlsx", ".xls", ".csv", ".xml", ".zip", ".ofd"}
    threshold = time.time() - minutes * 60
    copied = []
    now = datetime.now()
    for item in source_path.iterdir():
        if not item.is_file() or item.suffix.lower() not in allowed or item.stat().st_mtime < threshold:
            continue
        category = classify_tax_file(item.name)
        folder = destination_path / company / f"{now:%Y}年" / f"{now:%m}月" / category
        folder.mkdir(parents=True, exist_ok=True)
        safe_company = re.sub(r'[\\/:*?"<>|]', "_", company)
        target_name = item.name if item.name.startswith(safe_company) else f"{safe_company}_{item.name}"
        target = folder / target_name
        index = 2
        while target.exists():
            if target.stat().st_size == item.stat().st_size:
                target = None
                break
            target = folder / f"{Path(target_name).stem}_{index}{item.suffix}"
            index += 1
        if target:
            shutil.copy2(item, target)
            copied.append(str(target))
    return copied


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
    preference = configured_browser_preference()
    if preference != "自动选择":
        candidates.sort(key=lambda item: 0 if item[0] == preference else 1)
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
    """在固定坐标完成点击并恢复用户鼠标，避免移动鼠标干扰自动操作。"""
    user32 = ctypes.windll.user32
    original = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(original))
    blocked = bool(user32.BlockInput(True))
    try:
        user32.SetCursorPos(int(x), int(y))
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        user32.SetCursorPos(original.x, original.y)
    finally:
        if blocked:
            user32.BlockInput(False)


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


def native_f5():
    user32 = ctypes.windll.user32
    user32.keybd_event(0x74, 0, 0, 0)
    user32.keybd_event(0x74, 0, 0x0002, 0)


def native_send_text(text):
    user32 = ctypes.windll.user32
    for character in text:
        code = ord(character)
        user32.keybd_event(0, code, 0x0004, 0)
        user32.keybd_event(0, code, 0x0004 | 0x0002, 0)
        time.sleep(0.001)


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
        time.sleep(0.06)
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
    pixels = image.load()

    def submit_at(button_x, button_y, button_height):
        values = (tax_id, user_id, password)
        offsets = (-4.5, -3.0, -1.5)
        for value, multiplier in zip(values, offsets):
            input_y = int(button_y + multiplier * button_height)
            native_mouse_click(rect.left + button_x, rect.top + input_y)
            native_ctrl_a()
            native_send_text(value)
        native_escape()
        time.sleep(0.15)
        native_mouse_click(rect.left + button_x, rect.top + button_y)
        time.sleep(0.35)
        check = ImageGrab.grab(bbox=(
            rect.left + button_x - 3, rect.top + button_y - 3,
            rect.left + button_x + 4, rect.top + button_y + 4,
        )).convert("RGB")
        blue_pixels = sum(
            1 for red, green, blue in check.getdata()
            if blue >= 175 and 45 <= red <= 115 and 90 <= green <= 180 and blue >= red + 85
        )
        if blue_pixels >= 20:
            native_mouse_click(rect.left + button_x, rect.top + button_y)
        return True

    remembered = cached_layout("login_form", width, height)
    if remembered:
        cx, cy = int(remembered.get("x", 0)), int(remembered.get("y", 0))
        if 0 <= cx < width and 0 <= cy < height:
            red, green, blue = pixels[cx, cy]
            if blue >= 175 and blue >= red + 85:
                return submit_at(cx, cy, int(remembered.get("button_height", 40)))

    candidate_rows = []
    scan_left = int(width * 0.38)
    scan_right = int(width * 0.94)
    for y in range(int(height * 0.12), int(height * 0.9)):
        best = (-1, -1)
        start = -1
        for x in range(scan_left, scan_right):
            red, green, blue = pixels[x, y]
            is_blue = blue >= 175 and 45 <= red <= 115 and 90 <= green <= 180 and blue >= red + 85
            if is_blue and start < 0:
                start = x
            if (not is_blue or x == scan_right - 1) and start >= 0:
                end = x if is_blue and x == scan_right - 1 else x - 1
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
    save_cached_layout("login_form", width, height, x=button_x, y=button_y, button_height=button_height)
    return submit_at(button_x, button_y, button_height)


def click_visible_sms_button(window):
    """识别短信验证页右侧较短的蓝色“获取验证码”按钮。"""
    from PIL import ImageGrab

    rect = window.rectangle()
    if rect.width() < 600 or rect.height() < 400:
        return False
    image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).convert("RGB")
    width, height = image.size
    pixels = image.load()
    remembered = cached_layout("sms_button", width, height)
    if remembered:
        cx, cy = int(remembered.get("x", 0)), int(remembered.get("y", 0))
        if 0 <= cx < width and 0 <= cy < height:
            red, green, blue = pixels[cx, cy]
            if blue >= 175 and blue >= red + 85:
                native_mouse_click(rect.left + cx, rect.top + cy)
                return True
    rows = []
    scan_left = int(width * 0.46)
    scan_right = int(width * 0.78)
    for y in range(int(height * 0.20), int(height * 0.78)):
        best = (-1, -1)
        start = -1
        for x in range(scan_left, scan_right):
            red, green, blue = pixels[x, y]
            is_blue = blue >= 175 and 45 <= red <= 115 and 90 <= green <= 180 and blue >= red + 85
            if is_blue and start < 0:
                start = x
            if (not is_blue or x == scan_right - 1) and start >= 0:
                end = x if is_blue and x == scan_right - 1 else x - 1
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
    save_cached_layout("sms_button", width, height, x=x, y=y)
    native_mouse_click(rect.left + x, rect.top + y)
    return True


def wait_and_click_sms_in_system_browser(browser_name, status, timeout=300, announce=True, company=""):
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
                allowed, reason = sms_click_allowed(company) if company else (True, "")
                if not allowed:
                    status(reason)
                    diagnostic_event("sms_guard", reason)
                    return False
                if click_visible_sms_button(window):
                    if company:
                        sms_state = mark_sms_clicked(company)
                        set_company_task_state(company, "待人工验证")
                        diagnostic_event("sms", f"已自动获取验证码，当日第 {sms_state['count']} 次")
                    status("已点击获取验证码，自动操作已结束，请手工输入短信验证码。")
                    return True
        except Exception:
            pass
        time.sleep(0.06)
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
    pixels = image.load()

    remembered = cached_layout("home_login", rect.width(), rect.height())
    if remembered:
        absolute_x = int(remembered.get("x", 0))
        absolute_y = int(remembered.get("y", 0))
        local_x, local_y = absolute_x - (left - rect.left), absolute_y - (top - rect.top)
        if 0 <= local_x < width and 0 <= local_y < height:
            red, green, blue = pixels[local_x, local_y]
            if red >= 215 and red >= green + 45 and blue <= 105:
                native_mouse_click(rect.left + absolute_x, rect.top + absolute_y)
                return True

    candidate_rows = []
    for y in range(height):
        longest_start = -1
        longest_end = -1
        run_start = -1
        for x in range(width):
            red, green, blue = pixels[x, y]
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
    save_cached_layout("home_login", rect.width(), rect.height(), x=(left - rect.left) + center_x, y=(top - rect.top) + center_y)
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
    started = time.monotonic()
    refreshed = False
    while time.monotonic() < deadline:
        try:
            for window in native_browser_windows(title_hint)[:3]:
                try:
                    window.maximize()
                    window.set_focus()
                    time.sleep(0.05)
                except Exception:
                    pass
                if click_visible_orange_login(window):
                    diagnostic_event("home_login", "已识别并点击首页登录入口")
                    status("已点击登录，正在识别并填写三项登录信息……")
                    time.sleep(0.10)
                    filled = fill_login_in_system_browser(
                        browser_name, tax_id, user_id, password, status
                    )
                    diagnostic_event("login_form", "资料已自动提交" if filled else "登录表单识别超时")
                    return "submitted" if filled else "form_timeout"
                if not refreshed and time.monotonic() - started >= 6:
                    native_f5()
                    refreshed = True
                    diagnostic_event("browser_recovery", "首页识别超时，已自动刷新一次")
                    status("页面加载较慢，已自动刷新一次并继续识别……")
        except Exception:
            pass
        time.sleep(0.06)
    status("官网已打开，但未识别到登录按钮，请手工点击右上角“登录”。")
    diagnostic_event("home_login", "首页登录入口识别超时")
    return "home_login_timeout"


def start_browser(company, region, tax_id, user_id, password, status):
    started = time.monotonic()
    stages = {}
    try:
        diagnostic_event("start", f"开始登录流程：{region}")
        if company and tax_id and user_id and password:
            save_config(company, region, tax_id, user_id)
            save_password(company, user_id, password)
        settings = get_region_settings(region)
        if not settings.get("portal"):
            raise RuntimeError(f"{region}电子税务局网址尚未配置")
        browser_name, _engine, executable = find_installed_browser()
        stages["browser_ready"] = round(time.monotonic() - started, 2)
        diagnostic_event("browser", f"已选择 {browser_name}")
        # 不添加远程调试、自动化或独立 user-data-dir 参数，确保使用浏览器的
        # 默认用户资料、实名环境和税务组件，与用户平时手工打开完全一致。
        subprocess.Popen([
            executable,
            "--new-tab",
            "--start-maximized",
            "--force-renderer-accessibility",
            settings["portal"],
        ])
        stages["portal_opened"] = round(time.monotonic() - started, 2)
        diagnostic_event("portal", "已向系统浏览器发送官网地址")
        # 独立监控短信验证页，不依赖自动填写或登录步骤是否成功返回。
        threading.Thread(
            target=wait_and_click_sms_in_system_browser,
            args=(browser_name, status, 300, False, company),
            daemon=True,
        ).start()
        status(f"已用日常 {browser_name} 打开官网，正在点击登录……")
        outcome = click_login_in_system_browser(browser_name, tax_id, user_id, password, status)
        filled = outcome == "submitted"
        stages["form_submitted"] = round(time.monotonic() - started, 2)
        result = "已提交" if filled else "需要人工处理"
        details = {
            "submitted": "资料已填写并点击登录",
            "home_login_timeout": "首页登录按钮识别超时，可从首页阶段重试",
            "form_timeout": "登录表单识别超时，可从填写阶段重试",
        }
        detail = details.get(outcome, "自动登录流程未完成")
        update_login_guard(company, filled)
        set_company_task_state(company, "待人工验证" if filled else "异常")
        append_login_history(company, region, result, detail, time.monotonic() - started, stages)
        diagnostic_event("complete", detail)
        config = load_config()
        config["last_login"] = {"company": company, "region": region, "time": datetime.now().isoformat(timespec="seconds")}
        recent = [name for name in config.get("recent_companies", []) if name != company]
        config["recent_companies"] = [company, *recent[:9]]
        write_config(config)
        return filled
    except Exception as exc:
        status(f"启动失败：{exc}")
        diagnostic_event("failed", str(exc), exc)
        update_login_guard(company, False)
        set_company_task_state(company, "异常")
        append_login_history(company, region, "失败", exc, time.monotonic() - started, stages)
        return False


def main():
    import_portable_bundle_if_needed()
    migrate_sensitive_storage()
    remove_online_service_settings()
    ensure_daily_snapshot()
    config = load_config()
    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("1240x700")
    root.minsize(1040, 650)
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
    style.configure("Header.TButton", background=navy_soft, foreground="#E7ECF4", bordercolor="#263A58", font=("Microsoft YaHei UI", 9), padding=(11, 6))
    style.map("Header.TButton", background=[("active", "#1D3150"), ("pressed", "#263A58")], foreground=[("active", "#FFFFFF")])
    style.configure("Hint.TLabel", background=card, font=("Microsoft YaHei UI", 10), foreground=muted)
    style.configure("Status.TLabel", background="#EEEAE1", font=("Microsoft YaHei UI", 10), foreground="#4F5B6D", padding=(12, 8))
    style.configure("ActionHint.TLabel", background=card, font=("Microsoft YaHei UI", 9, "bold"), foreground="#7B6742")
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
    header = ttk.Frame(shell, style="Header.TFrame", padding=(46, 16))
    header.pack(fill="x")
    brand = ttk.Frame(header, style="Header.TFrame")
    brand.pack(side="left")
    ttk.Label(brand, text="云境 · 企业税务工作台", style="Brand.TLabel").pack(anchor="w")
    ttk.Label(brand, text="SECURE ENTERPRISE TAX WORKSPACE", style="HeaderHint.TLabel").pack(anchor="w", pady=(3, 0))
    ttk.Label(header, text=f"PRO  ·  v{APP_VERSION}", style="Badge.TLabel").pack(side="right", anchor="e")
    update_button = ttk.Button(header, text="检查更新", style="Header.TButton")
    update_button.pack(side="right", padx=(0, 10))
    tools_button = ttk.Button(header, text="工具中心", style="Header.TButton")
    tools_button.pack(side="right", padx=(0, 10))

    frame = ttk.Frame(shell, padding=(46, 20))
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="企业身份与登录资料管理  ·  敏感信息仅在本机加密保存", style="Hint.TLabel").pack(anchor="w", pady=(0, 6))

    fields = ttk.Frame(frame)
    fields.pack(fill="x", pady=(5, 0))
    fields.columnconfigure(0, weight=1, uniform="form")
    fields.columnconfigure(1, weight=1, uniform="form")
    ttk.Label(fields, text="当前选择公司", style="CurrentCompany.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(fields, text="搜索公司", style="Field.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 14), pady=(8, 0))
    ttk.Label(fields, text="公司档案管理", style="Field.TLabel").grid(row=2, column=1, sticky="w", padx=(14, 0), pady=(8, 0))
    search_row = ttk.Frame(fields)
    search_row.grid(row=3, column=0, sticky="ew", padx=(0, 14), pady=(4, 9))
    search_var = tk.StringVar()
    search_entry = ttk.Entry(search_row, textvariable=search_var, font=("Microsoft YaHei UI", 10))
    search_entry.pack(side="left", fill="x", expand=True)
    saved_groups = sorted({
        profile.get("group", "未分组") or "未分组"
        for profile in config.get("companies", {}).values()
    })
    group_filter_var = tk.StringVar(value="全部分组")
    group_filter_box = ttk.Combobox(
        search_row, textvariable=group_filter_var, state="readonly",
        values=["全部分组", *saved_groups], width=12,
    )
    group_filter_box.pack(side="left", padx=(8, 0))

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
    completeness_var = tk.StringVar(value=profile_completeness(selected_name))
    ttk.Label(fields, textvariable=completeness_var, style="Hint.TLabel").grid(row=0, column=1, sticky="e")
    company_box = ttk.Combobox(fields, textvariable=company_var, values=company_names, state="readonly", font=("Microsoft YaHei UI", 14, "bold"), style="Company.TCombobox")
    company_box.grid(row=1, column=0, columnspan=2, ipady=5, pady=(4, 4), sticky="ew")

    selected_profile = config.get("companies", {}).get(company_var.get(), {})
    ttk.Label(fields, text="选择地区", style="Field.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 14))
    region_var = tk.StringVar(value=selected_profile.get("region") or default_region(company_var.get()))
    region_box = ttk.Combobox(fields, textvariable=region_var, values=list(REGIONS), state="readonly", width=55, font=("Microsoft YaHei UI", 10))
    region_box.grid(row=5, column=0, ipady=3, padx=(0, 14), pady=(4, 9), sticky="ew")

    ttk.Label(fields, text="统一社会信用代码 / 纳税人识别号", style="Field.TLabel").grid(row=4, column=1, sticky="w", padx=(14, 0))
    tax_var = tk.StringVar(value=selected_profile.get("tax_id", ""))
    ttk.Entry(fields, textvariable=tax_var, font=("Microsoft YaHei UI", 10)).grid(row=5, column=1, ipady=5, padx=(14, 0), pady=(4, 9), sticky="ew")

    ttk.Label(fields, text="当前地区电子税务局网址（可手工维护）", style="Field.TLabel").grid(row=6, column=0, columnspan=2, sticky="w")
    url_row = ttk.Frame(fields)
    url_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 9))
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
        save_region_url_with_history(region, url)
        load_region_url()
        append_audit("修改税务局网址", detail=region)
        status(f"已保存{region}电子税务局网址。")

    def restore_region_url():
        region = region_var.get()
        save_region_url(region, "")
        load_region_url()
        append_audit("恢复税务局默认网址", detail=region)
        status(f"已恢复{region}的程序内置网址。")

    def show_region_url_history():
        region = region_var.get()
        history = load_config().get("region_url_history", {}).get(region, [])
        if not history:
            status(f"{region}暂无网址修改历史。")
            return
        viewer = tk.Toplevel(root)
        viewer.title(f"{region}网址历史")
        viewer.geometry("720x360")
        viewer.transient(root)
        listbox = tk.Listbox(viewer, font=("Microsoft YaHei UI", 10), activestyle="none")
        listbox.pack(fill="both", expand=True, padx=14, pady=(14, 8))
        for item in history:
            listbox.insert("end", f"{item.get('time','')}  {item.get('url','')}")
        def restore_selected():
            selection = listbox.curselection()
            if not selection:
                return
            url = history[selection[0]].get("url", "")
            region_url_var.set(url)
            save_region_url_with_history(region, url)
            viewer.destroy()
            status(f"已恢复{region}的历史网址。")
        ttk.Button(viewer, text="恢复所选网址", command=restore_selected, style="Primary.TButton").pack(pady=(0, 14))

    ttk.Button(url_row, text="保存网址", command=save_current_region_url, style="Secondary.TButton").pack(side="left", padx=(8, 4))
    ttk.Button(url_row, text="恢复默认", command=restore_region_url, style="Secondary.TButton").pack(side="left", padx=(4, 0))
    ttk.Button(url_row, text="历史", command=show_region_url_history, style="Secondary.TButton").pack(side="left", padx=(8, 0))
    region_box.bind("<<ComboboxSelected>>", load_region_url)

    ttk.Label(fields, text="办税人员身份证号 / 手机号 / 用户名（加密保存）", style="Field.TLabel").grid(row=8, column=0, sticky="w", padx=(0, 14))
    user_var = tk.StringVar(value=load_account(company_var.get()))
    user_row = ttk.Frame(fields)
    user_row.grid(row=9, column=0, padx=(0, 14), pady=(4, 9), sticky="ew")
    user_entry = ttk.Entry(user_row, textvariable=user_var, show="●", font=("Microsoft YaHei UI", 10))
    user_entry.pack(side="left", fill="x", expand=True, ipady=5)

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
    password_entry.grid(row=9, column=1, ipady=5, padx=(14, 0), pady=(4, 9), sticky="ew")

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
        completeness_var.set(profile_completeness(company_var.get()))

    company_box.bind("<<ComboboxSelected>>", load_company)

    def filter_companies(*_args):
        keyword = search_var.get().strip().lower()
        config_now = load_config()
        names = company_list_from(config_now)
        selected_group = group_filter_var.get()
        matches = [
            name for name in names
            if keyword in name.lower()
            and (
                selected_group == "全部分组"
                or config_now.get("companies", {}).get(name, {}).get("group", "未分组") == selected_group
            )
        ]
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
    group_filter_box.bind("<<ComboboxSelected>>", filter_companies)
    result_list.bind("<<ListboxSelect>>", choose_search_result)
    result_list.bind("<Return>", choose_search_result)

    def refresh_companies(select_name=None):
        names = company_list_from(load_config())
        company_box.configure(values=names)
        groups = sorted({
            profile.get("group", "未分组") or "未分组"
            for profile in load_config().get("companies", {}).values()
        })
        group_filter_box.configure(values=["全部分组", *groups])
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
        append_audit("新增公司", name)
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
        append_audit("重命名公司", new_name, f"原名称：{old_name}")
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
        append_audit("删除公司", name)
        delete_confirmation["name"] = ""
        status(f"已删除公司：{name}")

    def toggle_favorite_company():
        name = company_var.get()
        if not name:
            return
        config_now = load_config()
        favorites = [item for item in config_now.get("favorite_companies", []) if item != name]
        if name not in config_now.get("favorite_companies", []):
            favorites.insert(0, name)
            message = "已将当前公司置顶。"
        else:
            message = "已取消当前公司置顶。"
        config_now["favorite_companies"] = favorites
        write_config(config_now)
        refresh_companies(name)
        append_audit("切换公司置顶", name)
        status(message)

    company_actions = ttk.Frame(fields)
    company_actions.grid(row=3, column=1, sticky="ew", padx=(14, 0), pady=(4, 9))
    ttk.Button(company_actions, text="新增公司", command=add_company, style="Secondary.TButton").pack(side="left", fill="x", expand=True, padx=(0, 4))
    ttk.Button(company_actions, text="修改名称", command=rename_company, style="Secondary.TButton").pack(side="left", fill="x", expand=True, padx=4)
    ttk.Button(company_actions, text="删除公司", command=delete_company, style="Danger.TButton").pack(side="left", fill="x", expand=True, padx=(4, 0))
    ttk.Button(company_actions, text="置顶/取消", command=toggle_favorite_company, style="Secondary.TButton").pack(side="left", fill="x", expand=True, padx=(8, 0))

    status_var = tk.StringVar(value="")
    status_clear_job = {"id": None}
    def status(text):
        def apply_status():
            if status_clear_job["id"] is not None:
                root.after_cancel(status_clear_job["id"])
            status_var.set(text)
            if text:
                if not status_label.winfo_manager():
                    status_label.pack(fill="x", anchor="w", pady=(13, 0))
                def clear_status():
                    status_var.set("")
                    status_label.pack_forget()
                    status_clear_job["id"] = None
                status_clear_job["id"] = root.after(6000, clear_status)
            else:
                status_label.pack_forget()
                status_clear_job["id"] = None
        root.after(0, apply_status)

    vault_state = {"locked": False, "job": None, "last_activity": time.monotonic()}
    vault_overlay = tk.Frame(shell, bg=navy)
    vault_card = tk.Frame(vault_overlay, bg="#F7F5F0", padx=42, pady=34)
    vault_card.place(relx=0.5, rely=0.5, anchor="center")
    tk.Label(vault_card, text="本地数据保险箱", bg="#F7F5F0", fg=ink, font=("Microsoft YaHei UI", 18, "bold")).pack()
    tk.Label(vault_card, text="输入主密码后继续使用", bg="#F7F5F0", fg=muted, font=("Microsoft YaHei UI", 10)).pack(pady=(5, 16))
    vault_password_var = tk.StringVar()
    vault_entry = ttk.Entry(vault_card, textvariable=vault_password_var, show="●", width=34)
    vault_entry.pack(ipady=7)
    vault_message_var = tk.StringVar()
    tk.Label(vault_card, textvariable=vault_message_var, bg="#F7F5F0", fg="#9A3E38", font=("Microsoft YaHei UI", 9)).pack(pady=(8, 0))

    def unlock_application(_event=None):
        if verify_vault_password(vault_password_var.get()):
            vault_state["locked"] = False
            vault_state["last_activity"] = time.monotonic()
            vault_password_var.set("")
            vault_message_var.set("")
            vault_overlay.place_forget()
            load_company()
            append_audit("解锁软件")
            return
        vault_message_var.set("主密码不正确")
        vault_entry.select_range(0, tk.END)

    ttk.Button(vault_card, text="解锁", command=unlock_application, style="Primary.TButton").pack(fill="x", pady=(12, 0))
    vault_entry.bind("<Return>", unlock_application)

    def lock_application(reason="手工锁定"):
        if not load_config().get("vault") or vault_state["locked"]:
            return
        vault_state["locked"] = True
        user_var.set("")
        pass_var.set("")
        vault_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        vault_overlay.lift()
        vault_entry.focus_set()
        append_audit("锁定软件", detail=reason)

    def record_activity(_event=None):
        vault_state["last_activity"] = time.monotonic()

    def check_idle_lock():
        vault = load_config().get("vault", {})
        idle_minutes = int(vault.get("idle_minutes", 10)) if vault else 0
        if idle_minutes and not vault_state["locked"] and time.monotonic() - vault_state["last_activity"] >= idle_minutes * 60:
            lock_application("闲置自动锁定")
        vault_state["job"] = root.after(15000, check_idle_lock)

    root.bind_all("<KeyPress>", record_activity, add="+")
    root.bind_all("<Button>", record_activity, add="+")
    root.bind("<Unmap>", lambda _event: root.after(250, lambda: lock_application("最小化自动锁定") if root.state() == "iconic" else None), add="+")
    root.after(15000, check_idle_lock)
    if load_config().get("vault"):
        root.after(300, lambda: lock_application("启动锁定"))

    update_button.configure(
        command=lambda: threading.Thread(
            target=check_for_updates, args=(root, status, True), daemon=True
        ).start()
    )

    def open_tools_center():
        window = tk.Toplevel(root)
        window.title("工具中心")
        window.geometry("920x650")
        window.minsize(820, 580)
        window.configure(bg=card)
        window.transient(root)
        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=18, pady=18)

        task_page = ttk.Frame(notebook, padding=16)
        overview = ttk.Frame(notebook, padding=16)
        operations_page = ttk.Frame(notebook, padding=16)
        identity_page = ttk.Frame(notebook, padding=16)
        data_page = ttk.Frame(notebook, padding=16)
        settings_page = ttk.Frame(notebook, padding=16)
        security_page = ttk.Frame(notebook, padding=16)
        notebook.add(task_page, text="办税任务")
        notebook.add(overview, text="登录记录")
        notebook.add(operations_page, text="环境与归档")
        notebook.add(identity_page, text="身份与权限")
        notebook.add(data_page, text="数据与迁移")
        notebook.add(settings_page, text="偏好设置")
        notebook.add(security_page, text="安全与隐私")

        task_header = ttk.Frame(task_page)
        task_header.pack(fill="x", pady=(0, 8))
        ttk.Label(task_header, text="月份", style="Field.TLabel").pack(side="left")
        task_month_var = tk.StringVar(value=current_task_month())
        ttk.Entry(task_header, textvariable=task_month_var, width=10).pack(side="left", padx=(6, 16))
        ttk.Label(task_header, text="征期截止日", style="Field.TLabel").pack(side="left")
        deadlines = load_config().get("tax_deadlines", {})
        deadline_var = tk.StringVar(value=deadlines.get(task_month_var.get(), ""))
        ttk.Entry(task_header, textvariable=deadline_var, width=14).pack(side="left", padx=(6, 8))

        task_columns = ("company", "group", "region", "state", "complete", "guard", "sms")
        task_tree = ttk.Treeview(task_page, columns=task_columns, show="headings", height=17)
        task_labels = {"company": "公司", "group": "分组", "region": "地区", "state": "办税状态", "complete": "资料", "guard": "登录保护", "sms": "短信次数"}
        task_widths = {"company": 260, "group": 100, "region": 60, "state": 90, "complete": 115, "guard": 85, "sms": 70}
        for key in task_columns:
            task_tree.heading(key, text=task_labels[key])
            task_tree.column(key, width=task_widths[key], anchor="w")
        task_tree.pack(fill="both", expand=True)

        def refresh_tasks():
            task_tree.delete(*task_tree.get_children())
            config_now = load_config()
            month = task_month_var.get().strip() or current_task_month()
            deadline_var.set(config_now.get("tax_deadlines", {}).get(month, deadline_var.get()))
            for name in company_list_from(config_now):
                profile = config_now.get("companies", {}).get(name, {})
                failures = login_guard_state(name).get("failures", 0)
                sms_count = sms_guard_state(name).get("count", 0)
                task_tree.insert("", "end", iid=name, values=(
                    name, profile.get("group", "未分组"), profile.get("region", ""),
                    company_task_state(name, month), profile_completeness(name),
                    "已保护" if failures >= 2 else (f"失败{failures}次" if failures else "正常"), sms_count,
                ))

        task_controls = ttk.Frame(task_page)
        task_controls.pack(fill="x", pady=(10, 0))
        task_state_var = tk.StringVar(value="已完成")
        ttk.Combobox(task_controls, textvariable=task_state_var, state="readonly", values=TASK_STATES, width=14).pack(side="left")

        def set_selected_task():
            selected = task_tree.selection()
            if not selected:
                status("请先在任务看板选择公司。")
                return
            for name in selected:
                set_company_task_state(name, task_state_var.get(), task_month_var.get().strip())
            refresh_tasks()
            status(f"已更新 {len(selected)} 家公司的办税状态。")

        def reset_selected_guard():
            selected = task_tree.selection()
            if not selected:
                status("请先选择需要解除保护的公司。")
                return
            for name in selected:
                reset_login_guard(name)
            refresh_tasks()
            status("登录失败保护已解除，请确认密码正确后再重试。")

        def save_deadline():
            config_now = load_config()
            config_now.setdefault("tax_deadlines", {})[task_month_var.get().strip()] = deadline_var.get().strip()
            write_config(config_now)
            status("本月征期截止日已保存。")

        def export_monthly_report():
            target = filedialog.asksaveasfilename(parent=window, title="导出本地办税报表", defaultextension=".csv", filetypes=[("CSV 文件", "*.csv")], initialfile=f"办税进度-{task_month_var.get()}.csv")
            if not target:
                return
            config_now = load_config()
            records = read_login_history(5000)
            with open(target, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["公司", "分组", "地区", "办税状态", "资料完整度", "本月登录次数", "平均登录耗时", "短信次数"])
                for name in company_list_from(config_now):
                    profile = config_now.get("companies", {}).get(name, {})
                    company_records = [item for item in records if item.get("company") == name and str(item.get("time", "")).startswith(task_month_var.get())]
                    measured = [float(item["elapsed"]) for item in company_records if item.get("elapsed") is not None]
                    average = round(sum(measured) / len(measured), 2) if measured else ""
                    writer.writerow([name, profile.get("group", "未分组"), profile.get("region", ""), company_task_state(name, task_month_var.get()), profile_completeness(name), len(company_records), average, sms_guard_state(name).get("count", 0)])
            append_audit("导出月度办税报表", detail=task_month_var.get())
            status("本地办税进度报表已导出。")

        ttk.Button(task_controls, text="更新状态", command=set_selected_task, style="Primary.TButton").pack(side="left", padx=8)
        ttk.Button(task_controls, text="解除登录保护", command=reset_selected_guard, style="Secondary.TButton").pack(side="left")
        ttk.Button(task_controls, text="保存截止日", command=save_deadline, style="Secondary.TButton").pack(side="left", padx=8)
        ttk.Button(task_controls, text="刷新", command=refresh_tasks, style="Secondary.TButton").pack(side="left")
        ttk.Button(task_controls, text="导出报表", command=export_monthly_report, style="Secondary.TButton").pack(side="left", padx=8)
        task_month_var.trace_add("write", lambda *_: refresh_tasks())
        refresh_tasks()

        ttk.Label(operations_page, text="浏览器与税务局环境体检", style="Field.TLabel").pack(anchor="w")
        environment_tree = ttk.Treeview(operations_page, columns=("item", "state", "detail"), show="headings", height=9)
        environment_tree.heading("item", text="检查项目")
        environment_tree.heading("state", text="状态")
        environment_tree.heading("detail", text="详情")
        environment_tree.column("item", width=130)
        environment_tree.column("state", width=70)
        environment_tree.column("detail", width=610)
        environment_tree.pack(fill="x", pady=(7, 8))

        def run_environment_check():
            environment_tree.delete(*environment_tree.get_children())
            environment_tree.insert("", "end", values=("正在检测", "…", "请稍候"))
            def worker():
                report = browser_environment_report(region_var.get())
                def render():
                    environment_tree.delete(*environment_tree.get_children())
                    for row in report:
                        environment_tree.insert("", "end", values=row)
                    status("环境体检已完成。")
                root.after(0, render)
            threading.Thread(target=worker, daemon=True).start()
        ttk.Button(operations_page, text="开始环境体检", command=run_environment_check, style="Secondary.TButton").pack(anchor="w")

        ttk.Separator(operations_page).pack(fill="x", pady=15)
        ttk.Label(operations_page, text="税务资料归档", style="Field.TLabel").pack(anchor="w")
        archive_config = load_config().get("archive", {})
        download_var = tk.StringVar(value=archive_config.get("download_dir", str(Path.home() / "Downloads")))
        archive_var = tk.StringVar(value=archive_config.get("archive_dir", str(Path.home() / "Documents" / "税务资料")))
        auto_archive_var = tk.BooleanVar(value=bool(archive_config.get("enabled", False)))

        def choose_folder(variable):
            selected = filedialog.askdirectory(parent=window, initialdir=variable.get() or str(Path.home()))
            if selected:
                variable.set(selected)

        for label, variable in (("浏览器下载目录", download_var), ("税务资料归档目录", archive_var)):
            row = ttk.Frame(operations_page)
            row.pack(fill="x", pady=(7, 0))
            ttk.Label(row, text=label, style="Hint.TLabel", width=16).pack(side="left")
            ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="选择", command=lambda value=variable: choose_folder(value), style="Secondary.TButton").pack(side="left", padx=(8, 0))

        ttk.Checkbutton(operations_page, text="每 30 秒检查一次新下载文件并自动复制归档（保留原下载文件）", variable=auto_archive_var).pack(anchor="w", pady=(10, 7))

        def save_archive_settings():
            config_now = load_config()
            config_now["archive"] = {"download_dir": download_var.get(), "archive_dir": archive_var.get(), "enabled": bool(auto_archive_var.get())}
            write_config(config_now)
            status("资料归档设置已保存。")

        def archive_now():
            try:
                copied = archive_recent_downloads(company_var.get(), download_var.get(), archive_var.get())
                status(f"本次已归档 {len(copied)} 个新文件；原下载文件仍保留。")
            except Exception as exc:
                status(f"归档失败：{exc}")

        def open_archive_folder():
            folder = Path(archive_var.get()) / company_var.get()
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)

        archive_actions = ttk.Frame(operations_page)
        archive_actions.pack(fill="x")
        ttk.Button(archive_actions, text="立即归档", command=archive_now, style="Primary.TButton").pack(side="left")
        ttk.Button(archive_actions, text="保存归档设置", command=save_archive_settings, style="Secondary.TButton").pack(side="left", padx=8)
        ttk.Button(archive_actions, text="打开公司资料目录", command=open_archive_folder, style="Secondary.TButton").pack(side="left")

        ttk.Label(identity_page, text="办税人员身份与权限健康档案", style="Field.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(identity_page, text="以下资料使用 Windows 本机加密保存，不写入普通配置文件。", style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 14))
        identity_company_var = tk.StringVar(value=company_var.get())
        identity_company_box = ttk.Combobox(identity_page, textvariable=identity_company_var, state="readonly", values=company_list_from(load_config()))
        identity_company_box.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        identity_values = {
            "operator": tk.StringVar(), "role": tk.StringVar(), "phone_tail": tk.StringVar(),
            "auth_level": tk.StringVar(), "expires": tk.StringVar(), "permissions": tk.StringVar(),
        }
        identity_fields = [
            ("operator", "办税人员姓名"), ("role", "身份角色"),
            ("phone_tail", "接收短信手机号尾号"), ("auth_level", "实名认证等级"),
            ("expires", "授权到期日期"), ("permissions", "已授权业务"),
        ]
        for index, (key, label) in enumerate(identity_fields, start=3):
            ttk.Label(identity_page, text=label, style="Field.TLabel").grid(row=index, column=0, sticky="w", pady=6)
            if key == "role":
                widget = ttk.Combobox(identity_page, textvariable=identity_values[key], values=["法定代表人", "财务负责人", "办税员", "开票员", "代理人员"], state="readonly")
            elif key == "auth_level":
                widget = ttk.Combobox(identity_page, textvariable=identity_values[key], values=["一级", "二级", "三级", "四级", "未知"], state="readonly")
            else:
                widget = ttk.Entry(identity_page, textvariable=identity_values[key])
            widget.grid(row=index, column=1, sticky="ew", pady=6, padx=(14, 0))

        identity_health_var = tk.StringVar()
        ttk.Label(identity_page, textvariable=identity_health_var, style="Hint.TLabel").grid(row=9, column=0, columnspan=2, sticky="w", pady=(12, 8))

        def load_identity(_event=None):
            profile = load_identity_profile(identity_company_var.get())
            for key, variable in identity_values.items():
                variable.set(profile.get(key, ""))
            missing = [label for key, label in identity_fields if not profile.get(key)]
            identity_health_var.set("档案完整" if not missing else "待补充：" + "、".join(missing))

        def save_identity():
            profile = {key: variable.get().strip() for key, variable in identity_values.items()}
            save_identity_profile(identity_company_var.get(), profile)
            load_identity()
            status("身份与权限档案已加密保存。")

        identity_company_box.bind("<<ComboboxSelected>>", load_identity)
        load_identity()
        identity_buttons = ttk.Frame(identity_page)
        identity_buttons.grid(row=10, column=0, columnspan=2, sticky="ew")
        ttk.Button(identity_buttons, text="保存身份档案", command=save_identity, style="Primary.TButton").pack(side="left")
        ttk.Button(
            identity_buttons, text="身份切换使用说明", style="Secondary.TButton",
            command=lambda: messagebox.showinfo(
                "优先使用税局身份切换",
                "同一办税人员已登录且关联多家企业时，建议在电子税务局“账户中心 → 身份切换 → 企业办税”中切换，无需重复退出和获取短信验证码。\n\n若税局提示切换次数、认证等级或权限限制，请以税务局页面提示为准，本工具不会规避风控。",
                parent=window,
            ),
        ).pack(side="left", padx=8)
        identity_page.columnconfigure(1, weight=1)

        summary = ttk.Label(overview, text="最近登录与耗时（不记录账号、密码和验证码）", style="Field.TLabel")
        summary.pack(anchor="w", pady=(0, 8))
        columns = ("time", "company", "operator", "region", "result", "elapsed", "detail")
        history_tree = ttk.Treeview(overview, columns=columns, show="headings", height=17)
        headings = {"time": "时间", "company": "公司", "operator": "经办人", "region": "地区", "result": "结果", "elapsed": "耗时", "detail": "说明"}
        widths = {"time": 130, "company": 185, "operator": 80, "region": 50, "result": 80, "elapsed": 60, "detail": 210}
        for key in columns:
            history_tree.heading(key, text=headings[key])
            history_tree.column(key, width=widths[key], anchor="w")
        history_tree.pack(fill="both", expand=True)

        def refresh_history():
            history_tree.delete(*history_tree.get_children())
            records = read_login_history()
            for item in records:
                elapsed = "" if item.get("elapsed") is None else f"{item['elapsed']} 秒"
                history_tree.insert("", "end", values=(
                    item.get("time", ""), item.get("company", ""), item.get("operator", "本机用户"), item.get("region", ""),
                    item.get("result", ""), elapsed, item.get("detail", ""),
                ))
            successful = sum(1 for item in records if item.get("result") == "已提交")
            measured = [float(item["elapsed"]) for item in records if item.get("elapsed") is not None]
            average = f" · 平均 {sum(measured) / len(measured):.2f} 秒" if measured else ""
            summary.configure(text=f"最近 {len(records)} 次记录 · 自动提交 {successful} 次{average} · 日志不含敏感字段")

        history_actions = ttk.Frame(overview)
        history_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(history_actions, text="刷新", command=refresh_history, style="Secondary.TButton").pack(side="left")
        ttk.Button(history_actions, text="一键重试当前公司", command=lambda: (window.destroy(), launch()), style="Primary.TButton").pack(side="left", padx=8)

        def export_log():
            target = filedialog.asksaveasfilename(
                parent=window, title="导出脱敏登录记录", defaultextension=".csv",
                filetypes=[("CSV 文件", "*.csv")], initialfile="登录记录.csv",
            )
            if not target:
                return
            with open(target, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["时间", "公司", "经办人", "地区", "结果", "耗时（秒）", "说明"])
                for item in reversed(read_login_history(5000)):
                    writer.writerow([item.get("time"), item.get("company"), item.get("operator", "本机用户"), item.get("region"), item.get("result"), item.get("elapsed"), item.get("detail")])
            status("脱敏登录记录已导出。")

        ttk.Button(history_actions, text="导出记录", command=export_log, style="Secondary.TButton").pack(side="left")
        ttk.Button(
            history_actions, text="异常处理指南", style="Secondary.TButton",
            command=lambda: messagebox.showinfo(
                "本地异常处理指南",
                "网络或官网异常：先在“环境与归档”运行环境体检。\n\n页面白屏或加载中：程序会自动刷新一次；仍失败时可切换Edge。\n\n首页按钮识别失败：确认浏览器最大化、缩放接近100%。\n\n表单识别失败：检查税局页面是否改版，并导出脱敏诊断包。\n\n密码错误：连续两次失败后程序会暂停，请核对密码再解除保护。\n\n权限不足：检查办税人员身份、实名认证等级和企业授权期限。",
                parent=window,
            ),
        ).pack(side="left", padx=8)
        refresh_history()

        ttk.Label(data_page, text="加密备份与恢复", style="Field.TLabel").pack(anchor="w")
        ttk.Label(data_page, text="备份文件使用独立主密码加密，可安全迁移到另一台电脑。", style="Hint.TLabel").pack(anchor="w", pady=(3, 9))

        def backup_data():
            password = simpledialog.askstring("设置备份主密码", "请输入至少 8 位的备份主密码：", show="●", parent=window)
            if not password:
                return
            if len(password) < 8:
                status("备份主密码至少需要 8 位。")
                return
            target = filedialog.asksaveasfilename(parent=window, title="保存加密备份", defaultextension=".enc", filetypes=[("加密备份", "*.enc")], initialfile="企业税务资料备份.enc")
            if not target:
                return
            try:
                export_encrypted_backup(target, password)
                status("加密备份已生成。请妥善保管备份主密码。")
            except Exception as exc:
                status(f"备份失败：{exc}")

        def restore_data():
            source = filedialog.askopenfilename(parent=window, title="选择加密备份", filetypes=[("加密备份", "*.enc"), ("所有文件", "*.*")])
            if not source:
                return
            password = simpledialog.askstring("恢复备份", "请输入备份主密码：", show="●", parent=window)
            if not password:
                return
            try:
                count = import_encrypted_backup(source, password)
                refresh_companies(load_config().get("selected_company"))
                status(f"已恢复 {count} 家公司的资料。")
            except Exception:
                status("恢复失败：备份主密码错误或文件已损坏。")

        backup_row = ttk.Frame(data_page)
        backup_row.pack(fill="x", pady=(0, 18))
        ttk.Button(backup_row, text="导出加密备份", command=backup_data, style="Primary.TButton").pack(side="left")
        ttk.Button(backup_row, text="从备份恢复", command=restore_data, style="Secondary.TButton").pack(side="left", padx=8)

        ttk.Separator(data_page).pack(fill="x", pady=4)
        ttk.Label(data_page, text="批量导入公司", style="Field.TLabel").pack(anchor="w", pady=(12, 0))
        ttk.Label(data_page, text="CSV 列名：公司名称、地区、统一社会信用代码、办税账号、密码、分组。账号和密码导入后立即转为本机加密保存。", style="Hint.TLabel", wraplength=820).pack(anchor="w", pady=(3, 8))

        def import_companies_csv():
            source = filedialog.askopenfilename(parent=window, title="选择公司 CSV", filetypes=[("CSV 文件", "*.csv")])
            if not source:
                return
            imported = 0
            try:
                with open(source, "r", encoding="utf-8-sig", newline="") as stream:
                    rows = csv.DictReader(stream)
                    config_now = load_config()
                    names = company_list_from(config_now)
                    profiles = config_now.setdefault("companies", {})
                    for row in rows:
                        name = (row.get("公司名称") or row.get("公司") or "").strip()
                        if not name:
                            continue
                        if name not in names:
                            names.append(name)
                        profile = profiles.setdefault(name, {})
                        profile["region"] = (row.get("地区") or profile.get("region") or default_region(name)).strip()
                        profile["tax_id"] = (row.get("统一社会信用代码") or row.get("税号") or profile.get("tax_id") or "").strip()
                        profile["group"] = (row.get("分组") or profile.get("group") or "未分组").strip()
                        account = (row.get("办税账号") or row.get("账号") or "").strip()
                        password = row.get("密码") or ""
                        if account:
                            save_password(name, account, password)
                        imported += 1
                    config_now["company_list"] = names
                    write_config(config_now)
                refresh_companies(company_var.get())
                status(f"已导入或更新 {imported} 家公司。")
            except Exception as exc:
                status(f"批量导入失败：{exc}")

        def export_companies_csv():
            target = filedialog.asksaveasfilename(parent=window, title="导出公司维护表", defaultextension=".csv", filetypes=[("CSV 文件", "*.csv")], initialfile="公司资料维护.csv")
            if not target:
                return
            config_now = load_config()
            with open(target, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["公司名称", "地区", "统一社会信用代码", "分组", "资料完整度"])
                for name in company_list_from(config_now):
                    profile = config_now.get("companies", {}).get(name, {})
                    writer.writerow([name, profile.get("region", ""), profile.get("tax_id", ""), profile.get("group", "未分组"), profile_completeness(name)])
            append_audit("导出公司维护表")
            status("公司维护表已导出；账号和密码未包含在CSV中。")

        csv_actions = ttk.Frame(data_page)
        csv_actions.pack(anchor="w")
        ttk.Button(csv_actions, text="选择 CSV 并导入", command=import_companies_csv, style="Secondary.TButton").pack(side="left")
        ttk.Button(csv_actions, text="导出公司维护表", command=export_companies_csv, style="Secondary.TButton").pack(side="left", padx=8)

        ttk.Separator(data_page).pack(fill="x", pady=16)
        ttk.Label(data_page, text="公司资料完整度", style="Field.TLabel").pack(anchor="w")
        completeness = tk.Text(data_page, height=8, bg="#FFFFFF", fg=ink, relief="solid", borderwidth=1, font=("Microsoft YaHei UI", 9))
        completeness.pack(fill="both", expand=True, pady=(6, 0))
        for name in company_list_from(load_config()):
            completeness.insert("end", f"{name}  ·  {profile_completeness(name)}\n")
        completeness.configure(state="disabled")

        ttk.Label(settings_page, text="默认浏览器", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        browser_var = tk.StringVar(value=configured_browser_preference())
        browser_box = ttk.Combobox(settings_page, textvariable=browser_var, state="readonly", values=["自动选择", "Google Chrome", "Microsoft Edge", "Mozilla Firefox"], width=28)
        browser_box.grid(row=1, column=0, sticky="ew", pady=(5, 14), padx=(0, 12))
        ttk.Label(settings_page, text="自动选择仍按 Chrome、Edge、Firefox 的顺序。", style="Hint.TLabel").grid(row=1, column=1, sticky="w")

        ttk.Label(settings_page, text="更新通道", style="Field.TLabel").grid(row=2, column=0, sticky="w")
        channel_var = tk.StringVar(value=load_config().get("update_channel", "稳定版"))
        ttk.Combobox(settings_page, textvariable=channel_var, state="readonly", values=["稳定版", "测试版"], width=28).grid(row=3, column=0, sticky="ew", pady=(5, 14), padx=(0, 12))
        ttk.Label(settings_page, text="当前在线更新仍使用稳定版；测试版通道已预留。", style="Hint.TLabel").grid(row=3, column=1, sticky="w")

        ttk.Label(settings_page, text="公司分组", style="Field.TLabel").grid(row=4, column=0, sticky="w")
        group_company_var = tk.StringVar(value=company_var.get())
        group_company_box = ttk.Combobox(settings_page, textvariable=group_company_var, state="readonly", values=company_list_from(load_config()))
        group_company_box.grid(row=5, column=0, sticky="ew", pady=(5, 8), padx=(0, 12))
        group_var = tk.StringVar()
        group_entry = ttk.Entry(settings_page, textvariable=group_var)
        group_entry.grid(row=5, column=1, sticky="ew", pady=(5, 8))

        def load_group(_event=None):
            group_var.set(load_config().get("companies", {}).get(group_company_var.get(), {}).get("group", "未分组"))
        group_company_box.bind("<<ComboboxSelected>>", load_group)
        load_group()

        ttk.Label(settings_page, text="当前软件经办人", style="Field.TLabel").grid(row=6, column=0, sticky="w", pady=(10, 0))
        operator_config = load_config().get("operator", {})
        operator_name_var = tk.StringVar(value=operator_config.get("name", ""))
        operator_role_var = tk.StringVar(value=operator_config.get("role", "管理员"))
        operator_row = ttk.Frame(settings_page)
        operator_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(5, 8))
        ttk.Entry(operator_row, textvariable=operator_name_var).pack(side="left", fill="x", expand=True)
        ttk.Combobox(operator_row, textvariable=operator_role_var, state="readonly", values=["管理员", "普通经办人", "只读查看"], width=14).pack(side="left", padx=(8, 0))
        def save_preferences():
            config_now = load_config()
            config_now["browser_preference"] = browser_var.get()
            config_now["update_channel"] = channel_var.get()
            config_now["operator"] = {"name": operator_name_var.get().strip() or "本机用户", "role": operator_role_var.get()}
            if group_company_var.get():
                config_now.setdefault("companies", {}).setdefault(group_company_var.get(), {})["group"] = group_var.get().strip() or "未分组"
            write_config(config_now)
            status("偏好设置和公司分组已保存。")

        ttk.Button(settings_page, text="保存设置", command=save_preferences, style="Primary.TButton").grid(row=8, column=0, sticky="w", pady=(8, 0))

        def test_current_url():
            url = region_url_var.get().strip()
            if not url:
                status("当前地区尚未配置网址。")
                return
            def worker():
                began = time.monotonic()
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        code = response.status
                    status(f"网址可访问：HTTP {code}，响应 {time.monotonic() - began:.2f} 秒。")
                except Exception as exc:
                    status(f"网址检测失败：{exc}")
            threading.Thread(target=worker, daemon=True).start()
        ttk.Button(settings_page, text="检测当前税务局网址", command=test_current_url, style="Secondary.TButton").grid(row=8, column=1, sticky="w", pady=(8, 0))
        settings_page.columnconfigure(0, weight=1)
        settings_page.columnconfigure(1, weight=2)

        ttk.Label(security_page, text="隐私与数据", style="Field.TLabel").pack(anchor="w", pady=(16, 0))
        privacy_text = "账号与密码使用 Windows DPAPI 绑定本机加密；图片验证码始终由用户本人完成；软件不会上传税务账号、密码或短信验证码；登录记录仅保存公司、地区、结果和耗时。"
        ttk.Label(security_page, text=privacy_text, style="Hint.TLabel", wraplength=820).pack(anchor="w", pady=(5, 12))

        ttk.Label(security_page, text="本地数据保险箱", style="Field.TLabel").pack(anchor="w", pady=(8, 0))
        vault_controls = ttk.Frame(security_page)
        vault_controls.pack(fill="x", pady=(6, 12))
        idle_var = tk.StringVar(value=str(load_config().get("vault", {}).get("idle_minutes", 10)))

        def configure_vault():
            first = simpledialog.askstring("设置保险箱", "设置本地保险箱主密码（至少8位）：", show="●", parent=window)
            if not first:
                return
            if len(first) < 8:
                status("保险箱主密码至少需要8位。")
                return
            second = simpledialog.askstring("确认主密码", "请再次输入主密码：", show="●", parent=window)
            if first != second:
                status("两次输入的主密码不一致。")
                return
            set_vault_password(first)
            config_now = load_config()
            config_now.setdefault("vault", {})["idle_minutes"] = int(idle_var.get())
            write_config(config_now)
            append_audit("设置本地保险箱")
            status("本地保险箱已启用。")

        def disable_vault():
            current = simpledialog.askstring("关闭保险箱", "请输入当前主密码：", show="●", parent=window)
            if current is None:
                return
            if not verify_vault_password(current):
                status("主密码不正确。")
                return
            set_vault_password("")
            append_audit("关闭本地保险箱")
            status("本地保险箱已关闭。")

        ttk.Combobox(vault_controls, textvariable=idle_var, state="readonly", values=["3", "5", "10", "15", "30"], width=7).pack(side="left")
        ttk.Label(vault_controls, text="分钟无操作自动锁定", style="Hint.TLabel").pack(side="left", padx=(5, 12))
        ttk.Button(vault_controls, text="设置/修改主密码", command=configure_vault, style="Primary.TButton").pack(side="left")
        ttk.Button(vault_controls, text="立即锁定", command=lambda: (window.destroy(), lock_application()), style="Secondary.TButton").pack(side="left", padx=8)
        ttk.Button(vault_controls, text="关闭保险箱", command=disable_vault, style="Secondary.TButton").pack(side="left")

        ttk.Label(security_page, text="本地备份、审计和密码健康", style="Field.TLabel").pack(anchor="w", pady=(8, 0))

        def snapshot_now():
            try:
                target = create_local_snapshot()
                append_audit("创建本地快照")
                status(f"本地加密快照已创建：{target.name}")
            except Exception as exc:
                status(f"快照创建失败：{exc}")

        def restore_snapshot_ui():
            source = filedialog.askopenfilename(parent=window, title="选择本地快照", initialdir=str(BACKUP_DIR), filetypes=[("本地加密快照", "*.etaxbak")])
            if not source:
                return
            if not messagebox.askyesno("恢复快照", "恢复前将自动备份当前资料，确定继续吗？", parent=window):
                return
            try:
                create_local_snapshot()
                restore_local_snapshot(source)
                refresh_companies(load_config().get("selected_company"))
                append_audit("恢复本地快照")
                status("本地快照恢复完成。")
            except Exception as exc:
                status(f"快照恢复失败：{exc}")

        def export_audit():
            target = filedialog.asksaveasfilename(parent=window, title="导出本地审计记录", defaultextension=".csv", filetypes=[("CSV 文件", "*.csv")], initialfile="本地操作审计.csv")
            if not target:
                return
            with open(target, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["时间", "经办人", "操作", "公司", "说明"])
                for item in reversed(read_audit_history(10000)):
                    writer.writerow([item.get("time"), item.get("operator"), item.get("action"), item.get("company"), item.get("detail")])
            status("本地操作审计记录已导出。")

        def show_password_health():
            rows = password_health_rows()
            lines = []
            for item in rows:
                state = "未保存密码" if item["age_days"] is None else (f"已使用 {item['age_days']} 天")
                if item["duplicate"]:
                    state += " · 与其他公司密码相同"
                lines.append(f"{item['company']}：{state}")
            viewer = tk.Toplevel(window)
            viewer.title("密码健康检查")
            viewer.geometry("720x520")
            text = tk.Text(viewer, wrap="word", font=("Microsoft YaHei UI", 10), padx=15, pady=15)
            text.pack(fill="both", expand=True)
            text.insert("1.0", "\n".join(lines) or "暂无公司资料")
            text.configure(state="disabled")

        local_actions = ttk.Frame(security_page)
        local_actions.pack(fill="x", pady=(6, 12))
        ttk.Button(local_actions, text="立即创建快照", command=snapshot_now, style="Primary.TButton").pack(side="left")
        ttk.Button(local_actions, text="恢复本地快照", command=restore_snapshot_ui, style="Secondary.TButton").pack(side="left", padx=8)
        ttk.Button(local_actions, text="密码健康检查", command=show_password_health, style="Secondary.TButton").pack(side="left")
        ttk.Button(local_actions, text="导出审计记录", command=export_audit, style="Secondary.TButton").pack(side="left", padx=8)

        def clear_history():
            if messagebox.askyesno("清除记录", "确定清除全部脱敏登录记录吗？", parent=window):
                LOGIN_LOG_FILE.unlink(missing_ok=True)
                refresh_history()
                status("登录记录已清除。")

        def export_diagnostics():
            target = filedialog.asksaveasfilename(
                parent=window, title="导出脱敏诊断包", defaultextension=".zip",
                filetypes=[("ZIP 诊断包", "*.zip")], initialfile=f"税务助手诊断-{datetime.now():%Y%m%d-%H%M}.zip",
            )
            if not target:
                return
            try:
                create_diagnostic_bundle(target)
                status("脱敏诊断包已导出，不含公司名称、税号、账号和密码。")
            except Exception as exc:
                status(f"诊断包导出失败：{exc}")

        def clear_current_company_data():
            name = company_var.get()
            if not name or not messagebox.askyesno("清除敏感资料", f"确定清除“{name}”的账号、密码和税号吗？", parent=window):
                return
            config_now = load_config()
            profile = config_now.setdefault("companies", {}).setdefault(name, {})
            profile["tax_id"] = ""
            write_config(config_now)
            delete_password(name)
            load_company()
            status("当前公司的敏感资料已清除。")

        def rollback_update():
            if not getattr(sys, "frozen", False):
                status("开发运行版无需回退；安装版更新后才会生成上一版本。")
                return
            current = Path(sys.executable).resolve()
            previous = current.with_suffix(current.suffix + ".previous")
            if not previous.exists():
                status("没有找到可回退的上一版本。")
                return
            if not messagebox.askyesno("回退版本", "确定恢复到更新前的版本吗？", parent=window):
                return
            command = (
                f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
                "Start-Sleep -Milliseconds 500; "
                f"Copy-Item -LiteralPath '{str(previous).replace(chr(39), chr(39)*2)}' -Destination '{str(current).replace(chr(39), chr(39)*2)}' -Force; "
                f"Start-Process -FilePath '{str(current).replace(chr(39), chr(39)*2)}'"
            )
            subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], creationflags=0x08000000)
            root.destroy()

        privacy_actions = ttk.Frame(security_page)
        privacy_actions.pack(fill="x")
        ttk.Button(privacy_actions, text="清除登录记录", command=clear_history, style="Secondary.TButton").pack(side="left")
        ttk.Button(privacy_actions, text="清除当前公司敏感资料", command=clear_current_company_data, style="Danger.TButton").pack(side="left", padx=8)
        ttk.Button(privacy_actions, text="回退上一版本", command=rollback_update, style="Secondary.TButton").pack(side="left")
        ttk.Button(privacy_actions, text="导出脱敏诊断包", command=export_diagnostics, style="Secondary.TButton").pack(side="left", padx=8)

    tools_button.configure(command=open_tools_center)

    def launch():
        if load_config().get("operator", {}).get("role") == "只读查看":
            status("当前经办人权限为只读查看，不能发起税务登录。")
            return
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
        guard = login_guard_state(company)
        if int(guard.get("failures", 0)) >= 2:
            status("该公司今天已连续自动登录失败 2 次。为避免账号被锁定，请核对密码后在工具中心解除保护。")
            return
        threading.Thread(target=start_browser, args=(company, region, tax_id, user_id, password, status), daemon=True).start()

    def save_profile():
        if load_config().get("operator", {}).get("role") == "只读查看":
            status("当前经办人权限为只读查看，不能修改公司资料。")
            return
        company, region = company_var.get(), region_var.get()
        tax_id, user_id, password = tax_var.get().strip(), user_var.get().strip(), pass_var.get()
        if not company or not region or not all((tax_id, user_id, password)):
            status("请选择公司和地区，并填写三项登录信息。")
            return
        try:
            save_config(company, region, tax_id, user_id)
            save_password(company, user_id, password)
            completeness_var.set(profile_completeness(company))
            append_audit("保存公司资料", company)
            status(f"“{company}”的资料已安全保存")
        except Exception as exc:
            status(f"保存失败：{exc}")

    actions = ttk.Frame(frame)
    actions.pack(fill="x", pady=(8, 0))
    actions.columnconfigure(0, weight=2, uniform="actions")
    actions.columnconfigure(1, weight=3, uniform="actions")
    ttk.Button(actions, text="保存当前公司资料", command=save_profile, style="Secondary.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=1)
    ttk.Button(actions, text="打开税务局并自动填写", command=launch, style="Primary.TButton").grid(row=0, column=1, sticky="ew", padx=(8, 0), ipady=1)
    status_label = ttk.Label(frame, textvariable=status_var, style="Status.TLabel")

    def show_onboarding():
        guide = tk.Toplevel(root)
        guide.title("欢迎使用")
        guide.geometry("620x430")
        guide.resizable(False, False)
        guide.transient(root)
        guide.grab_set()
        body = ttk.Frame(guide, padding=28)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="三步开始安全办税", style="CurrentCompany.TLabel").pack(anchor="w")
        steps = [
            ("1  建立公司档案", "新增或批量导入公司，选择地区并维护官方税务局网址。"),
            ("2  保存登录资料", "账号和密码使用 Windows 本机加密保存，不上传到服务器。"),
            ("3  打开并登录", "程序填写资料并点击登录；图片验证码仍由本人完成，随后自动获取短信验证码。"),
        ]
        for title, description in steps:
            ttk.Label(body, text=title, style="Field.TLabel").pack(anchor="w", pady=(18, 2))
            ttk.Label(body, text=description, style="Hint.TLabel", wraplength=550).pack(anchor="w")
        accepted = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="我已了解：本工具不破解验证码，实际办税行为由用户本人确认", variable=accepted).pack(anchor="w", pady=(20, 10))

        def finish():
            if not accepted.get():
                status("请先阅读并确认使用说明。")
                return
            config_now = load_config()
            config_now["onboarding_seen"] = True
            write_config(config_now)
            guide.destroy()
        ttk.Button(body, text="开始使用", command=finish, style="Primary.TButton").pack(anchor="e")
        guide.protocol("WM_DELETE_WINDOW", finish)

    if not load_config().get("onboarding_seen"):
        root.after(250, show_onboarding)

    def show_tax_deadline_reminder():
        config_now = load_config()
        month = current_task_month()
        deadline_text = config_now.get("tax_deadlines", {}).get(month, "")
        incomplete = [
            name for name in company_list_from(config_now)
            if company_task_state(name, month) != "已完成"
        ]
        if not deadline_text or not incomplete:
            return
        try:
            deadline = datetime.strptime(deadline_text, "%Y-%m-%d")
            days = (deadline.date() - datetime.now().date()).days
        except ValueError:
            return
        if days <= 7:
            wording = "今天截止" if days == 0 else (f"已逾期 {-days} 天" if days < 0 else f"还有 {days} 天")
            reminder = f"本月办税征期{wording}，仍有 {len(incomplete)} 家公司未标记完成。"
            status(reminder)
    root.after(2200, show_tax_deadline_reminder)

    def background_archive_check():
        archive_settings = load_config().get("archive", {})
        if archive_settings.get("enabled"):
            try:
                copied = archive_recent_downloads(
                    company_var.get(), archive_settings.get("download_dir", str(Path.home() / "Downloads")),
                    archive_settings.get("archive_dir", str(Path.home() / "Documents" / "税务资料")), minutes=10,
                )
                if copied:
                    status(f"已自动归档 {len(copied)} 个新下载文件。")
            except Exception as exc:
                diagnostic_event("archive", str(exc), exc)
        root.after(30000, background_archive_check)
    root.after(30000, background_archive_check)
    root.bind("<Control-f>", lambda _event: search_entry.focus_set())
    root.bind("<Control-s>", lambda _event: save_profile())
    root.bind("<Control-l>", lambda _event: launch())
    root.bind("<Control-k>", lambda _event: lock_application("快捷键锁定"))
    root.bind("<Control-t>", lambda _event: open_tools_center())

    root.after(
        1500,
        lambda: threading.Thread(target=check_for_updates, args=(root, status), daemon=True).start(),
    )
    root.mainloop()


if __name__ == "__main__":
    main()
