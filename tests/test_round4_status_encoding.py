import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from src.round2.evidence import ROOT,KEYS,create_run,result,execute,summarize,read,write,validate_stage
from src.round4.report import KNOWN_SENTENCE,audit_text,save_report
from src.round4.acceptance import manual_audit,evaluate
from src.round3.statistics import aggregate


class Round4Tests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'work/round4_tests';base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base);self.folder=Path(self.temp.name)
        self.ctx=create_run(self.folder/'run',{},[],True)
    def tearDown(self):self.temp.cleanup()
    def stage(self,status='FAIL',check_status='BLOCKED'):
        result(self.ctx,'H1',status,[dict(id='real_positive',status=check_status,reason_code='NO_REAL_FIXED_GRID_EVENTS',reason='计算完成，但冻结网格未取得真实正事件')])
    def command(self,code=1):
        return execute(self.ctx,'H1',[sys.executable,'-c',f'raise SystemExit({code})'],10)
    def test_completed_business_failure_preserved(self):
        self.stage();self.command()
        s=summarize(self.ctx,[dict(id='H1',required=True)]);r=s['stages'][0]
        self.assertEqual(r['checks'][0]['reason_code'],'NO_REAL_FIXED_GRID_EVENTS')
        self.assertIn('冻结网格',r['checks'][0]['reason'])
        self.assertTrue(r['evidence_validation']['valid']);self.assertFalse(r['command']['timed_out'])
        self.assertIn('COMPLETED_TARGET_UNMET',{r['code'] for r in r['reasons']});self.assertEqual(s['exit_code'],1)
    def test_real_timeout_is_distinct(self):
        self.stage();execute(self.ctx,'H1',[sys.executable,'-c','import time;time.sleep(30)'],.15)
        s=summarize(self.ctx,[dict(id='H1',required=True)]);r=s['stages'][0]
        self.assertTrue(r['command']['timed_out']);self.assertIn('COMMAND_TIMEOUT',{r['code'] for r in r['reasons']})
        self.assertNotIn('COMPLETED_TARGET_UNMET',{r['code'] for r in r['reasons']})
    def test_missing_corrupt_old_incomplete_rejected(self):
        for mode in ['missing','corrupt','old','incomplete']:
            self.stage();p=Path(self.ctx['output'])/'H1/result.json'
            if mode=='missing':p.unlink()
            elif mode=='corrupt':p.write_bytes(b'{')
            else:
                x=read(p);x['run_id' if mode=='old' else 'lifecycle']='invalid';write(p,x)
            r=validate_stage(self.ctx,'H1')
            with self.subTest(mode=mode):self.assertFalse(r['evidence_validation']['valid']);self.assertIsNone(r['business_status'])
    def test_pass_command_conflict_preserves_both(self):
        self.stage('PASS','PASS');self.command(7)
        r=summarize(self.ctx,[dict(id='H1',required=True)])['stages'][0]
        self.assertEqual(r['business_status'],'PASS');self.assertEqual(r['status'],'FAIL');self.assertEqual(r['command']['exit_code'],7)
        self.assertIn('COMMAND_BUSINESS_CONFLICT',{x['code'] for x in r['reasons']})
    def test_report_success_does_not_change_acceptance(self):
        self.stage();self.command();s=summarize(self.ctx,[dict(id='H1',required=True)])
        before=copy.deepcopy(s);back=save_report(self.ctx['output'],s,KNOWN_SENTENCE+'\n业务未达标，报告生成成功。\n')
        self.assertEqual(s,before);self.assertEqual(back['core_acceptance'],'FAIL');self.assertEqual(back['exit_code'],1);self.assertTrue(back['report_generated'])
    def test_chinese_file_subprocess_roundtrip(self):
        p=self.folder/'unicode.txt';p.write_bytes(KNOWN_SENTENCE.encode('utf-8'))
        env=os.environ.copy();env['PYTHONIOENCODING']='utf-8'
        # All shell/subprocess code is ASCII; Chinese enters via explicit UTF-8 file.
        child=subprocess.run([sys.executable,'-c','from pathlib import Path;import sys;print(Path(sys.argv[1]).read_text(encoding="utf-8"))',str(p)],env=env,capture_output=True,check=True)
        self.assertEqual(child.stdout.decode('utf-8').strip(),KNOWN_SENTENCE)
        self.assertEqual(audit_text(child.stdout.decode('utf-8'))['status'],'PASS')
        for damaged in [KNOWN_SENTENCE+'???',KNOWN_SENTENCE+'\ufffd','????']:
            self.assertEqual(audit_text(damaged)['status'],'FAIL')
    def test_h_layers_separate_goals_and_states(self):
        def check(records,geom=True,complete=True):
            s=aggregate(['a','b','unvisited'],records,[0,10]);h=manual_audit(['a','b','unvisited'],records,s,[0,10])
            return evaluate(records,s,h,[dict(record_id=r['record_id'],status='PASS' if geom else 'FAIL') for r in records],completed=complete)
        def r(id,p,s,a,b):return dict(record_id=id,point_id=p,sat_id=s,sensor_id='Camera',start_epsec=a,stop_epsec=b)
        first=[r('1','a','s1',1,2)]
        x=check(first);self.assertEqual([x[k]['status'] for k in ['H1','H2','H3']],['PASS','BLOCKED','BLOCKED'])
        x=check(first+[r('2','b','s2',3,4)]);self.assertEqual([x[k]['status'] for k in ['H1','H2','H3']],['PASS','PASS','BLOCKED']);self.assertFalse(x['same_point_multiple_satellites'])
        x=check(first+[r('2','a','s2',3,4)]);self.assertEqual([x[k]['status'] for k in ['H1','H2','H3']],['PASS']*3);self.assertTrue(x['same_point_multiple_satellites']);self.assertFalse(x['natural_cross_satellite_union'])
        self.assertEqual(check(first,False)['H1']['status'],'FAIL')
        self.assertEqual(check(first,complete=False)['H1']['status'],'SKIPPED')
    def test_manual_audit_detects_corrupted_statistics(self):
        raw=[dict(record_id='a',point_id='p',sat_id='s',start_epsec=1.,stop_epsec=2.),dict(record_id='b',point_id='p',sat_id='t',start_epsec=4.,stop_epsec=5.)]
        s=aggregate(['p','zero'],raw,[0,10]);self.assertEqual(manual_audit(['p','zero'],raw,s,[0,10])['status'],'PASS')
        s['conditional_mean_gap_s']=0
        self.assertEqual(manual_audit(['p','zero'],raw,s,[0,10])['status'],'FAIL')

if __name__=='__main__':unittest.main(verbosity=2)
