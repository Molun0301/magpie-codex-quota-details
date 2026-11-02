"""验证生成的服务、注册路径和卸载命令，隔离全部外部副作用。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]


class ServiceInstallTests(unittest.TestCase):
    def test_timer_and_registration_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix='quota-service-') as directory:
            home = Path(directory)
            binaries = home / 'bin';binaries.mkdir()
            log = home / 'commands.jsonl'
            stub = '#!' + sys.executable + '''
import json,os,sys
from pathlib import Path
with open(os.environ['FIXTURE_LOG'],'a') as f:f.write(json.dumps([Path(sys.argv[0]).name,*sys.argv[1:]])+'\\n')
if Path(sys.argv[0]).name=='magpie':
 p=Path(os.environ['XDG_CONFIG_HOME'])/'magpie/plugins.json';p.parent.mkdir(parents=True,exist_ok=True)
 if sys.argv[1:3]==['plugin','add']:p.write_text(json.dumps({'plugins':[{'spec':sys.argv[3],'options':{'keep':True}}]}))
 elif sys.argv[1:3]==['plugin','rm']:
  data=json.loads(p.read_text());assert data['plugins'][0]['spec']==sys.argv[3];p.write_text('{"plugins":[]}')
'''
            for name in ['magpie', 'systemctl']:
                file = binaries / name;file.write_text(stub);file.chmod(0o755)
            env = {**os.environ, 'HOME': directory, 'XDG_CONFIG_HOME': str(home / '配置 空格'), 'XDG_DATA_HOME': str(home / '.local/share'), 'XDG_CACHE_HOME': str(home / '.cache'), 'PATH': str(binaries) + os.pathsep + os.environ['PATH'], 'MAGPIE_BIN': str(binaries / 'magpie'), 'FIXTURE_LOG': str(log)}
            target = home / '目标 空格'
            def run(action):
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), action, '--destination', str(target)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run('install')
            units = home / '配置 空格/systemd/user'
            service = units / 'magpie-quota-details.service'
            self.assertIn(str(target / 'collect.py') + '" --installed-only', service.read_text())
            run('install')
            config = home / '配置 空格/magpie/plugins.json'
            self.assertEqual(json.loads(config.read_text())['plugins'][0]['options'], {'keep': True})
            run('uninstall')
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(sum(c[:3] == ['magpie', 'plugin', 'add'] for c in calls), 1)
            self.assertIn(['magpie', 'plugin', 'rm', str(target)], calls)
            self.assertIn(['systemctl', '--user', 'disable', '--now', 'magpie-quota-details.timer'], calls)
            self.assertFalse(service.exists())
            self.assertFalse((units / 'magpie-quota-details.timer').exists())


if __name__ == '__main__':
    unittest.main()
