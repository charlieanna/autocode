"""Run the CLI audit and persist an honest pass/fail inventory (including failures)."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest


SCENARIOS = {
    1: ['test_01_'], 2: ['test_02_'], 3: ['test_01_'], 4: ['test_01_'],
    5: ['test_05_'], 6: ['test_06_'], 7: ['test_07_'], 8: ['test_08_'],
    9: ['test_09_'], 10: ['test_10_'], 11: ['test_11_'], 12: ['test_12_'],
    13: ['test_13_'], 14: ['test_14_'], 15: ['test_15_'], 16: ['test_16_'],
    17: ['test_17_'], 18: ['test_18_'], 19: ['test_19_'],
    20: ['test_chat_brief_feedback_approval_and_autonomous_rework'],
    21: ['test_strong_retry_uses_sol_high', 'test_pins_and_custom_providers'],
    22: ['test_independent_failure_resolver', 'test_exhaustion_resume'],
    23: ['test_23_'], 24: ['test_24_'], 25: ['test_25_'], 26: ['test_26_'],
    27: ['test_01_', 'test_product_e_'], 28: ['test_28_'], 29: ['test_29_'],
    30: ['test_30_'], 31: ['test_31_'], 32: ['test_32_'],
    33: ['test_30_33_'], 34: ['test_02_'],
}
LIMITATIONS = {
    18: 'Scripted infeasibility request; no live impossible-library reasoning trial.',
    23: 'Durable named checkpoint projection tested here; actual browser rendering checked separately, not 21 distinct screens.',
}


def scenario_results(rows):
    result = []
    for number, prefixes in SCENARIOS.items():
        matches = [row for row in rows if any(row['test'].split('.')[-1].startswith(p) for p in prefixes)]
        covered = all(any(row['test'].split('.')[-1].startswith(p) for row in matches) for p in prefixes)
        status = 'PASS' if covered and all(r['status'] == 'PASS' for r in matches) else 'FAIL'
        if status == 'PASS' and number in LIMITATIONS:
            status = 'GAP' if number in (15, 21, 22) else 'PARTIAL'
        result.append(dict(scenario=number, status=status, tests=[r['test'] for r in matches],
                           limitation=LIMITATIONS.get(number)))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifacts',type=Path,required=True)
    args=parser.parse_args()
    if os.environ.get('BUILD_AUDIT_LIVE_CODEX'):
        parser.error('Fault-injection suite is offline; run individual product tests for live builders')
    args.artifacts.mkdir(parents=True,exist_ok=True)
    os.environ['BUILD_AUDIT_ARTIFACTS']=str(args.artifacts.resolve())
    class Result(unittest.TextTestResult):
        rows=[]
        def startTest(self,test):
            self.started=time.monotonic()
            super().startTest(test)
        def addSuccess(self,test):
            self.rows.append(dict(test=test.id(),status='PASS',seconds=time.monotonic()-self.started))
            super().addSuccess(test)
        def addFailure(self,test,error):
            self.rows.append(dict(test=test.id(),status='FAIL',detail=self._exc_info_to_string(error,test)))
            super().addFailure(test,error)
        def addError(self,test,error):
            self.rows.append(dict(test=test.id(),status='ERROR',detail=self._exc_info_to_string(error,test)))
            super().addError(test,error)
        def addSkip(self,test,reason):
            self.rows.append(dict(test=test.id(),status='SKIP',detail=reason))
            super().addSkip(test,reason)
    suite=unittest.defaultTestLoader.loadTestsFromNames([
        'tools.test_build_blackbox', 'tools.test_build_recovery_blackbox',
        'tools.test_report_repair', 'tools.test_dispatch', 'tools.test_assignment_scenarios',
        'tools.test_units', 'tools.test_subprocess', 'tools.test_escalation',
        'tools.test_builder_policy', 'tools.test_execution_checkpoints'])
    result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(suite)
    scenarios = scenario_results(result.rows)
    (args.artifacts/'results.json').write_text(json.dumps(dict(provider='scripted/offline',
        tests=result.testsRun,tests_successful=result.wasSuccessful(),results=result.rows,
        scenarios=scenarios,all_scenarios_verified=all(r['status']=='PASS' for r in scenarios)),indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    raise SystemExit(main())
