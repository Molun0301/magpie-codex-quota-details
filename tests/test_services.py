"""验证档位证据与跨回合关联，不发起模型请求。"""
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import service_for, read_services, enrich_services
from collect import aggregate
from pricing import PriceBook, calculated_cost, normalize_price

SID = '00000000-0000-0000-0000-000000000001'

def event(kind, payload, sec=0):
    return {'type':kind,'timestamp':f'2026-10-25T00:00:{sec:02d}Z','payload':payload}

def logs():
    return [event('session_meta',{'id':SID}),
      event('turn_context',{'turn_id':'t1'}),
      event('event_msg',{'type':'thread_settings_applied','thread_id':SID,'thread_settings':{'service_tier':'priority'}}),
      event('response_item',{'role':'user','content':[{'text':'service_tier = default'}]}),
      event('token_usage_record',{'thread_id':SID,'turn_id':'t1','response_id':'fast1'},1),
      event('token_usage_record',{'thread_id':SID,'turn_id':'t1','response_id':'downgrade','service_tier':'default'},2),
      event('turn_context',{'turn_id':'t2'}),
      event('event_msg',{'type':'thread_settings_applied','thread_id':SID,'thread_settings':{'service_tier':'default'}}),
      event('token_usage_record',{'thread_id':SID,'turn_id':'t2','response_id':'normal'},3),
      event('token_usage_record',{'thread_id':SID,'turn_id':'t1','response_id':'late1'},4),
      event('token_usage_record',{'thread_id':'foreign','turn_id':'t2','response_id':'foreign'},5)]

class ServiceTests(unittest.TestCase):
    def compress(self, data):
        return subprocess.run(['zstd', '-q', '-c'], input=data, capture_output=True, check=True).stdout

    def test_compressed_sessions_preserve_chinese_and_concatenated_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = logs()
            data.insert(1, event('response_item', {'role': 'user', 'content': [{'text': '中文会话'}]}))
            lines = [(json.dumps(item, ensure_ascii=False) + '\n').encode('utf-8') for item in data]
            plain, compressed = root / 'log.jsonl', root / 'log.jsonl.zst'
            plain.write_bytes(b''.join(lines))
            compressed.write_bytes(self.compress(b''.join(lines[:5])) + self.compress(b''.join(lines[5:])))
            expected = read_services(plain, SID)
            self.assertEqual(read_services(compressed, SID), expected)
            self.assertEqual(expected[0]['fast1'], ('fast', ''))
            self.assertEqual(read_services(compressed, 'wrong'), ({}, {}))

    def test_discovers_compressed_sessions_without_cache(self):
        for location in ('sessions/2026/10/25', 'archived_sessions'):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                root = home / '.codex' / location
                root.mkdir(parents=True)
                data = ('\n'.join(map(json.dumps, logs())) + '\n').encode('utf-8')
                (root / f'rollout-{SID}.jsonl.zst').write_bytes(self.compress(data))
                row = {'source': 'log', 'agent': 'codex', 'session': SID, 'response_id': 'fast1'}
                enrich_services([row], home)
                self.assertEqual(service_for(row), {'tier': 'fast', 'evidence': 'session'})
                cache = home / '.cache/magpie/sessions.json'
                cache.parent.mkdir(parents=True)
                cache.write_text(json.dumps({'files': {str(root / f'rollout-{SID}.jsonl'): {'id': SID}}}))
                row = {'source': 'log', 'agent': 'codex', 'session': SID, 'response_id': 'fast1'}
                with redirect_stderr(io.StringIO()) as errors:
                    enrich_services([row], home)
                self.assertEqual(service_for(row), {'tier': 'fast', 'evidence': 'session'})
                self.assertEqual(errors.getvalue(), '')

    def test_damaged_files_do_not_publish_partial_tiers_or_break_other_sessions(self):
        for damaged in ('truncated_zstd', 'invalid_utf8'):
            with self.subTest(damaged=damaged), tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                root = home / '.codex/sessions'
                root.mkdir(parents=True)
                data = ('\n'.join(map(json.dumps, logs())) + '\n').encode('utf-8')
                good = root / f'rollout-{SID}.jsonl'
                good.write_bytes(data)
                bad_sid = '00000000-0000-0000-0000-000000000002'
                bad_data = data.replace(SID.encode(), bad_sid.encode())
                if damaged == 'truncated_zstd':
                    bad = root / f'rollout-{bad_sid}.jsonl.zst'
                    bad.write_bytes(self.compress(bad_data)[:-3])
                    with self.assertRaises(OSError):
                        read_services(bad, bad_sid)
                else:
                    bad = root / f'rollout-{bad_sid}.jsonl'
                    bad.write_bytes(bad_data + b'\xff\n')
                base = {'source': 'log', 'agent': 'codex', 'response_id': 'fast1'}
                rows = [{**base, 'session': bad_sid}, {**base, 'session': SID}]
                enrich_services(rows, home)
                self.assertEqual([service_for(row)['tier'] for row in rows], ['unknown', 'fast'])

    def test_service_prices_do_not_replace_standard_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);target=home/'.cache/magpie/models.json';target.parent.mkdir(parents=True)
            raw={'input':2,'output':10,'cache_write':2.5,'cache_read':.1}
            target.write_text(json.dumps({'openai':{'models':{'gpt-6.1-sol':{'cost':raw}}}}))
            info=PriceBook(home).get('gpt-6.1-sol')
            self.assertEqual(info['price'],normalize_price(raw))
            self.assertEqual(next(m for m in info['modes'] if m['name']=='fast')['price']['base']['input'],4)
            updated={**raw,'input':6}
            target.write_text(json.dumps({'openai':{'models':{'gpt-6.1-sol':{'cost':raw,
                'experimental':{'modes':{'fast':{'cost':updated}}}}}}}))
            info=PriceBook(home).get('gpt-6.1-sol')
            self.assertEqual(next(m for m in info['modes'] if m['name']=='fast')['price'],normalize_price(updated))

    def test_alias_and_downgrade(self):
        self.assertEqual(service_for({'tier_served':'priority'})['tier'],'fast')
        self.assertEqual(service_for({'tier_served':'default','tier_asked':'fast'})['tier'],'standard')
        self.assertEqual(service_for({'tier_sent':'default','tier_asked':'fast'})['tier'],'standard')
        self.assertEqual(service_for({'tier_served':'auto','tier_asked':'fast'})['tier'],'fast')
        self.assertEqual(service_for({'tier_asked':'auto'})['tier'],'unknown')
        self.assertEqual(service_for({})['evidence'],'unknown')
        self.assertEqual(service_for({'tier_served':'flex','tier_asked':'fast'})['tier'],'flex')

    def test_turn_switch_late_response_and_foreign_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'log.jsonl';p.write_text('\n'.join(map(json.dumps,logs()))+'\n{')
            responses,_=read_services(p,SID)
            self.assertEqual(responses['fast1'],('fast',''))
            self.assertEqual(responses['downgrade'],('fast','standard'))
            self.assertEqual(responses['normal'],('standard',''))
            self.assertEqual(responses['late1'],('fast',''))
            self.assertNotIn('foreign',responses)
            self.assertEqual(read_services(p,'wrong'),({},{}))

    def test_response_matching_never_uses_current_config(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);root=home/'.codex/sessions/2026/10/25';root.mkdir(parents=True)
            (root/f'rollout-{SID}.jsonl').write_text('\n'.join(map(json.dumps,logs())))
            (home/'.codex/config.toml').write_text('service_tier = "ultrafast"')
            base={'source':'log','agent':'codex','session':SID,'time':'2026-10-25T00:00:01Z'}
            rows=[{**base,'response_id':'fast1'},{**base,'response_id':'missing'},
                  {**base,'response_id':'downgrade'},base.copy(),{**base,'source':'gateway'}]
            enrich_services(rows,home)
            self.assertEqual([service_for(r)['tier'] for r in rows],['fast','unknown','standard','fast','unknown'])

    def test_conflicting_duplicate_and_unknown_history(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'log.jsonl'
            data=logs()+[event('token_usage_record',{'turn_id':'t2','response_id':'fast1'},8)]
            p.write_text('\n'.join(map(json.dumps,data)))
            self.assertEqual(read_services(p,SID)[0]['fast1'],('',''))
            p.write_text(json.dumps(event('token_usage_record',{'response_id':'old'})))
            self.assertEqual(read_services(p,SID)[0]['old'],('',''))

    def test_mixed_aggregation_and_long_cache_prices(self):
        from datetime import datetime
        stamp=datetime.fromisoformat('2026-10-25T00:00:00+00:00').timestamp()
        def row(tier):
            return {'time':'2026-10-25T00:00:01Z','source':'log','agent':'codex','session_official_login':'true',
                    'session_account':'test','model':'gpt-6.1-sol','provider':'codex','input_tokens':'1000',
                    'cache_read_tokens':'300000','output_tokens':'100','cost_usd':'0.1','tier_served':tier}
        rows=[row('default'),row('priority'),{**row('fast'),'session_account':'foreign'},
              {**row('fast'),'model':'gpt-reserve'}]
        result=aggregate(rows,'test',stamp,stamp+5,PriceBook())
        self.assertEqual(result['requests'],2)
        self.assertEqual(result['tokens'],602200)
        self.assertEqual(result['services']['fast']['requests'],1)
        tariffs=result['models'][0]['tariffs'];fast=next(t for t in tariffs if t['service']['tier']=='fast')
        self.assertEqual(fast['tierUsage']['272000']['cached'],300000)
        self.assertAlmostEqual(calculated_cost(fast['price'],{'input':1000,'cached':300000,'written':0,'output':100}),0.131)

if __name__=='__main__':unittest.main()
