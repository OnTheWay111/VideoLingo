#!/usr/bin/env python3
"""macOS: 从 Chromium 系浏览器的 Cookie 库里取出 X 的 auth_token / ct0。

只用于本机、只读浏览器自己的 Cookie 库（复制一份临时副本），
借助钥匙串里的 "<Browser> Safe Storage" 密钥解密，最后写成一个
KEY=VALUE 文件供 xfetch / x-car-video-finder 使用。不会打印 cookie 值。

用法:
  python3 auth_from_chrome.py --out /tmp/xfetch-auth.env
  python3 auth_from_chrome.py --browser edge --profile "Profile 1" --out /tmp/xfetch-auth.env
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

BROWSERS = {
    "chrome": {
        "keychain": "Chrome Safe Storage",
        "cookie_db": "~/Library/Application Support/Google/Chrome/{profile}/Cookies",
    },
    "edge": {
        "keychain": "Microsoft Edge Safe Storage",
        "cookie_db": "~/Library/Application Support/Microsoft Edge/{profile}/Cookies",
    },
    "brave": {
        "keychain": "Brave Safe Storage",
        "cookie_db": "~/Library/Application Support/BraveSoftware/Brave-Browser/{profile}/Cookies",
    },
    "arc": {
        "keychain": "Arc Safe Storage",
        "cookie_db": "~/Library/Application Support/Arc/User Data/{profile}/Cookies",
    },
    "chromium": {
        "keychain": "Chromium Safe Storage",
        "cookie_db": "~/Library/Application Support/Chromium/{profile}/Cookies",
    },
}

X_HOSTS = (".x.com", "x.com", ".twitter.com", "twitter.com")
WANTED = ("auth_token", "ct0")


def die(msg):
    print("错误: " + msg, file=sys.stderr)
    sys.exit(1)


def get_keychain_password(service):
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        die("调用 security 失败: %s" % exc)
    if out.returncode != 0:
        die(
            "读取钥匙串失败（%s）。请在弹出的钥匙串弹窗点“允许”；"
            "仍失败可改用浏览器导出 cookies.txt，再执行 "
            "`xfetch.py auth import --file cookies.txt`。" % service
        )
    pwd = out.stdout.strip()
    if not pwd:
        die("钥匙串项 %s 为空。" % service)
    return pwd


def derive_key(password):
    return hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, 16)


def _unpad(data):
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


def decrypt_with_cryptography(ct, key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    dec = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    return _unpad(dec.update(ct) + dec.finalize())


def decrypt_with_openssl(ct, key):
    proc = subprocess.run(
        ["openssl", "enc", "-d", "-aes-128-cbc", "-nopad",
         "-K", key.hex(), "-iv", (b" " * 16).hex()],
        input=ct, capture_output=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace")[:300])
    return _unpad(proc.stdout)


def decrypt_cookie(encrypted, key, host_key):
    if not encrypted:
        raise ValueError("empty encrypted_value")
    if encrypted[:3] in (b"v10", b"v11"):
        ct = encrypted[3:]
    else:
        # 未加密的旧格式
        return encrypted.decode(errors="replace")
    try:
        pt = decrypt_with_cryptography(ct, key)
    except ImportError:
        pt = decrypt_with_openssl(ct, key)
    # 新版 Chrome 在明文前拼 32 字节 host 摘要，校验后剥掉
    if len(pt) > 32 and pt[:32] == hashlib.sha256(host_key.encode()).digest():
        pt = pt[32:]
    return pt.decode(errors="replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--browser", choices=sorted(BROWSERS), default="chrome")
    ap.add_argument("--profile", default="Default")
    ap.add_argument("--cookie-db", default=None, help="直接指定 Cookies 数据库路径")
    ap.add_argument("--out", default="/tmp/xfetch-auth.env",
                    help="输出 KEY=VALUE 文件（默认 /tmp/xfetch-auth.env）")
    ap.add_argument("--json", action="store_true", help="打印 JSON 摘要（不含 cookie 值）")
    args = ap.parse_args()

    if sys.platform != "darwin":
        die("该脚本只处理 macOS 钥匙串。其他平台请用 `xfetch.py auth extract --browser ...`。")

    conf = BROWSERS[args.browser]
    db_path = args.cookie_db or os.path.expanduser(conf["cookie_db"].format(profile=args.profile))
    if not os.path.exists(db_path):
        die("找不到 Cookie 数据库: %s（检查 --profile/--browser）" % db_path)

    key = derive_key(get_keychain_password(conf["keychain"]))

    tmp = os.path.join(tempfile.mkdtemp(prefix="xcf-"), "Cookies")
    shutil.copy2(db_path, tmp)
    try:
        con = sqlite3.connect(tmp)
        placeholders = ",".join("?" * len(X_HOSTS))
        rows = con.execute(
            "SELECT host_key, name, encrypted_value FROM cookies "
            "WHERE host_key IN (%s) AND name IN (%s)" % (placeholders, ",".join("?" * len(WANTED))),
            list(X_HOSTS) + list(WANTED),
        ).fetchall()
    finally:
        try:
            os.remove(tmp)
            os.rmdir(os.path.dirname(tmp))
        except OSError:
            pass

    values = {}
    errors = []
    for host, name, encrypted in rows:
        try:
            value = decrypt_cookie(encrypted, key, host)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s/%s: %s" % (host, name, exc))
            continue
        if value:
            values[name] = value

    missing = [n for n in WANTED if n not in values]
    if missing:
        die(
            "没找到 %s。该浏览器的 %s 配置可能未登录 x.com；"
            "或用 `xfetch.py auth import --file cookies.txt`。详情: %s"
            % (", ".join(missing), args.browser, "; ".join(errors) or "无")
        )

    out = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fp:
        fp.write("XFETCH_AUTH_TOKEN=%s\n" % values["auth_token"])
        fp.write("XFETCH_CT0=%s\n" % values["ct0"])
    os.chmod(out, 0o600)

    summary = {
        "browser": args.browser,
        "profile": args.profile,
        "cookie_db": db_path,
        "have": sorted(values),
        "out": out,
        "note": "cookie 值已写入文件，未打印；用完请删除该文件。",
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("✓ 已从 %s 读取 %s" % (args.browser, ", ".join(sorted(values))))
        print("  登录态写入: %s (权限 600)" % out)
        print("  用法: finder.py --auth-file %s ..." % out)
        print("  用完记得: rm %s" % out)


if __name__ == "__main__":
    main()
