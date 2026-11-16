#!/usr/bin/env python3
"""安装和管理 macOS launchd 用户级 xcar-xhs 监控服务。"""

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


LABEL = "com.xcar-xhs.monitor"
SCRIPT_DIR = Path(__file__).resolve().parent
MONITOR_SCRIPT = SCRIPT_DIR / "monitor.py"
PROJECT_ROOT = SCRIPT_DIR.parents[3]
PLIST_FILE = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
LOG_DIR = Path.home() / "Library/Logs/xcar-xhs"
STDOUT_LOG = LOG_DIR / "monitor.log"
STDERR_LOG = LOG_DIR / "monitor.error.log"


def service_target(uid=None):
    return "gui/%d/%s" % (os.getuid() if uid is None else uid, LABEL)


def service_definition(python_executable=None):
    python = str(Path(python_executable or sys.executable).resolve())
    return {
        "Label": LABEL,
        "ProgramArguments": [python, str(MONITOR_SCRIPT),
                             "--host", "127.0.0.1", "--port", "8769"],
        "WorkingDirectory": str(PROJECT_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "ThrottleInterval": 5,
        "Umask": 18,
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "PYTHONPYCACHEPREFIX": "/tmp/xcar-monitor-pycache",
        },
        "StandardOutPath": str(STDOUT_LOG),
        "StandardErrorPath": str(STDERR_LOG),
    }


def launchctl(*arguments, check=True):
    return subprocess.run(["launchctl", *arguments], check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def write_plist(value):
    PLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=PLIST_FILE.name + ".",
                                             dir=PLIST_FILE.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            plistlib.dump(value, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, PLIST_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def wait_until_ready(timeout=10):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8769/api/overview", timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError("监控服务未在 %s 秒内就绪：%s" % (timeout, last_error))


def install():
    if sys.platform != "darwin":
        raise RuntimeError("长期后台服务安装仅支持 macOS launchd")
    if not MONITOR_SCRIPT.is_file():
        raise RuntimeError("找不到监控入口：%s" % MONITOR_SCRIPT)
    write_plist(service_definition())
    target = service_target()
    launchctl("bootout", target, check=False)
    launchctl("bootstrap", "gui/%d" % os.getuid(), str(PLIST_FILE))
    launchctl("enable", target)
    launchctl("kickstart", "-k", target)
    wait_until_ready()
    return {"status": "running", "label": LABEL, "target": target,
            "plist": str(PLIST_FILE), "url": "http://127.0.0.1:8769/overview",
            "stdout_log": str(STDOUT_LOG), "stderr_log": str(STDERR_LOG)}


def restart():
    target = service_target()
    launchctl("kickstart", "-k", target)
    wait_until_ready()
    return {"status": "running", "target": target,
            "url": "http://127.0.0.1:8769/overview"}


def status():
    result = launchctl("print", service_target(), check=False)
    return {"loaded": result.returncode == 0, "target": service_target(),
            "details": result.stdout.strip()}


def uninstall():
    target = service_target()
    launchctl("bootout", target, check=False)
    if PLIST_FILE.exists():
        PLIST_FILE.unlink()
    return {"status": "uninstalled", "target": target, "plist": str(PLIST_FILE)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "restart", "status", "uninstall"))
    args = parser.parse_args(argv)
    try:
        result = {"install": install, "restart": restart,
                  "status": status, "uninstall": uninstall}[args.command]()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("loaded", True) else 1
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
