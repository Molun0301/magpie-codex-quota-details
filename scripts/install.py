#!/usr/bin/env python3
"""安装 Magpie 额度详情及可选的 Linux 用户定时器。"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from lifecycle import Installation, read_json, atomic
ROOT = Path(__file__).resolve().parents[1]
NAME = 'magpie-codex-quota-details'
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('action', choices=['install', 'uninstall'])
p.add_argument('--destination', type=Path, default=Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'magpie/plugins/codex-quota-details')
p.add_argument('--replace', action='store_true')
p.add_argument('--no-service', action='store_true', help='不启用用户定时器；可手动运行 collect.py')
p.add_argument('--no-register', action='store_true', help='仅管理文件，不调用 Magpie 注册命令')
a = p.parse_args()

def run(*args):
    subprocess.run(args, check=True)

def quoted(value):
    # systemd 双引号路径，百分号需要转义，避免解释为单元说明符。
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

try:
    destination = a.destination.expanduser().absolute()
    magpie = os.environ.get('MAGPIE_BIN') or shutil.which('magpie') or str(Path.home() / '.local/bin/magpie')
    if not a.no_register and not Path(magpie).is_file():
        raise ValueError('找不到 Magpie；安装它或设置 MAGPIE_BIN')
    settings_root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    units = settings_root / 'systemd/user'
    install = Installation(destination, NAME, '1.6.0')
    service = Installation(units, NAME, '1.6.0')
    if a.action == 'uninstall':
        if service.receipt.exists():
            if not a.no_service:
                run('systemctl', '--user', 'disable', '--now', 'magpie-quota-details.timer')
            service.uninstall()
            if not a.no_service:
                run('systemctl', '--user', 'daemon-reload')
        if not a.no_register and install.receipt.exists():
            run(magpie, 'plugin', 'rm', str(destination))
        install.uninstall()
        print('卸载完成；保留采集快照，Magpie 自己管理的价格选项由宿主处理。')
    else:
        files = {name: (ROOT / name).read_bytes() for name in ['collect.py', 'cycles.py', 'pricing.py', 'services.py', 'service-prices.json', 'package.json']}
        files.update({str(f.relative_to(ROOT)): f.read_bytes() for f in (ROOT / 'page').iterdir() if f.is_file() and f.name not in ['snapshot.js', 'snapshot.example.js']})
        install.install(files, a.replace)
        snapshot = destination / 'page/snapshot.js'
        if not snapshot.exists():
            atomic(snapshot, (ROOT / 'page/snapshot.example.js').read_bytes())
        if not a.no_register:
            config = read_json(settings_root / 'magpie/plugins.json')
            entries = config.get('plugins', [])
            existing = next((entry for entry in entries if entry.get('spec') == str(destination)), None)
            if not existing:
                run(magpie, 'plugin', 'add', str(destination))
        if not a.no_service:
            if sys.platform != 'linux' or not shutil.which('systemctl'):
                raise ValueError('文件已安装；当前系统不支持用户定时器，请加 --no-service 并手动采集')
            unit = '[Unit]\nDescription=Magpie Codex 额度详情采集\n\n[Service]\nType=oneshot\nExecStart=' + quoted(sys.executable) + ' ' + quoted(destination / 'collect.py') + ' --installed-only\nEnvironment=' + quoted('MAGPIE_BIN=' + magpie) + '\nEnvironment=' + quoted('XDG_CONFIG_HOME=' + settings_root.as_posix()) + '\nEnvironment=' + quoted('XDG_CACHE_HOME=' + os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) + '\nTimeoutStartSec=120\nUMask=0077\nNoNewPrivileges=true\n'
            timer = '[Unit]\nDescription=每分钟更新 Magpie 额度详情\n\n[Timer]\nOnStartupSec=10\nOnUnitInactiveSec=60\nAccuracySec=5\nUnit=magpie-quota-details.service\n\n[Install]\nWantedBy=timers.target\n'
            service.install({'magpie-quota-details.service': unit.encode(), 'magpie-quota-details.timer': timer.encode()}, a.replace)
            run('systemctl', '--user', 'daemon-reload')
            run('systemctl', '--user', 'enable', '--now', 'magpie-quota-details.timer')
        print(f'安装完成：{destination}；重新打开 Magpie 插件页面生效。')
except (ValueError, OSError, subprocess.CalledProcessError) as exc:
    p.exit(1, str(exc) + '\n')
