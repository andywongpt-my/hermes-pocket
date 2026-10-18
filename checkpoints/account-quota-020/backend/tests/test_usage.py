from copy import deepcopy
import json
import unittest
from pocket.usage import codex_limits, claude_limits, ollama_balance, UsageError, UsageCache

class UsageTests(unittest.TestCase):
    def test_codex_snapshot(self):
        rows=codex_limits({'rateLimits':{'primary':{'usedPercent':25,'windowDurationMins':300,'resetsAt':2000}}},1000)
        self.assertEqual(rows[0]['value'],25);self.assertEqual(rows[0]['unit'],'percent_used')
        self.assertEqual(rows[0]['category'],'subscription')
    def test_codex_multiple_buckets(self):
        row={'primary':{'usedPercent':0,'resetsAt':2000}}
        self.assertEqual(len(codex_limits({'rateLimitsByLimitId':{'codex':row,'other':row}},1000)),2)
    def test_missing_not_zero(self):
        self.assertEqual(codex_limits({},1000),[]);self.assertEqual(claude_limits({},1000),[])
    def test_claude_partial(self):
        rows=claude_limits({'rate_limits':{'five_hour':{'used_percentage':23.5,'resets_at':2000}}},1000)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['value'],23.5)
    def test_expired_window_not_available(self):
        rows=claude_limits({'rate_limits':{'five_hour':{'used_percentage':50,'resets_at':1000}}},1000)
        self.assertEqual(rows[0]['availability'],'stale')
    def test_invalid_percent(self):
        for v in (True,-1,101,float('nan'),'5'):
            with self.assertRaises(UsageError):claude_limits({'rate_limits':{'five_hour':{'used_percentage':v,'resets_at':2000}}},1000)
    def test_ollama_credit_balances_separate(self):
        rows=ollama_balance({'included':{'balance_usd':2,'allowance_usd':10,'period':{'until':'2030-01-01T00:00:00Z'}},'purchased':{'balance_usd':3}},1000)
        self.assertEqual([r['value'] for r in rows],[2,3]);self.assertTrue(all(r['unit']=='USD_credit' for r in rows))
    def test_ollama_legacy(self):
        rows=ollama_balance({'included':{'session':{'remaining_percent':45,'resets_at':'2030-01-01T00:00:00Z'}},'purchased':{}},1000)
        self.assertEqual(rows[0]['unit'],'percent_remaining')
    def test_cache_stale_and_target_scope(self):
        cache=UsageCache(ttl=60)
        cache.put('windows','claude',claude_limits({'rate_limits':{'five_hour':{'used_percentage':1,'resets_at':2000}}},1000))
        self.assertEqual(cache.get('linux','claude',1001),[])
        self.assertEqual(cache.get('windows','claude',1060)[0]['availability'],'stale')
        self.assertEqual(cache.get('windows','claude',1059)[0]['availability'],'available')

class UsageReportTests(unittest.TestCase):
    def setUp(self):
        self.cache=UsageCache(ttl=60)
        self.rows=claude_limits({'rate_limits':{'five_hour':{'used_percentage':0,'resets_at':2000}}},1000)

    def test_default_is_not_configured(self):
        self.assertEqual(self.cache.report('windows','claude',1001),{
            'provider':'claude','availability':'not_configured','value':None,
            'fetched_at':None,'source':None,'metrics':[]})

    def test_explicit_configuration_and_empty_snapshot_are_unavailable(self):
        self.cache.configure('windows','claude',source='fixture snapshot')
        report=self.cache.report('windows','claude',1001)
        self.assertEqual(report['availability'],'unavailable')
        self.assertEqual(report['source'],'fixture snapshot')
        self.cache.put('windows','claude',[],fetched_at=1000)
        report=self.cache.report('windows','claude',1001)
        self.assertEqual((report['availability'],report['fetched_at']),( 'unavailable',1000))
        self.assertIsNone(report['value'])

    def test_zero_value_and_reset_ttl_boundary(self):
        self.rows[0]['resets_at']=1030
        self.cache.put('windows','claude',self.rows)
        report=self.cache.report('windows','claude',1029)
        self.assertEqual(report['availability'],'available')
        self.assertIsNone(report['value'])
        self.assertEqual(report['fetched_at'],1000)
        self.assertEqual(report['source'],'Claude Code statusline')
        metric=report['metrics'][0]
        self.assertEqual((metric['value'],metric['unit'],metric['window']),(0,'percent_used','five_hour'))
        self.assertEqual(metric['expires_at'],1030)
        self.assertEqual(self.cache.report('windows','claude',1030)['availability'],'stale')

    def test_unknown_measurement_and_time_remain_null(self):
        rows=claude_limits({'rate_limits':{'five_hour':{'used_percentage':None}}},1000)
        self.assertIsNone(rows[0]['value'])
        rows[0]['fetched_at']=None
        self.cache.put('windows','claude',rows)
        report=self.cache.report('windows','claude',1001)
        self.assertEqual(report['availability'],'unavailable')
        metric=report['metrics'][0]
        for field in ('value','fetched_at','resets_at','expires_at'):
            self.assertIsNone(metric[field],field)
        self.assertEqual(metric['availability'],'unavailable')

    def test_known_value_without_observation_time_is_stale(self):
        self.rows[0]['fetched_at']=None
        self.cache.put('windows','claude',self.rows)
        self.assertEqual(self.cache.report('windows','claude',1001)['availability'],'stale')

    def test_mixed_missing_or_stale_windows_are_partial(self):
        for other in ({'value':None,'availability':'unavailable'},
                      {'resets_at':1000,'availability':'stale'}):
            with self.subTest(other=other):
                second=dict(self.rows[0],window='seven_day',**other)
                self.cache.put('windows','claude',self.rows+[second])
                self.assertEqual(self.cache.report('windows','claude',1001)['availability'],'partial')

    def test_all_windows_expire_at_ttl(self):
        self.cache.put('windows','claude',self.rows)
        self.assertEqual(self.cache.report('windows','claude',1059)['availability'],'available')
        report=self.cache.report('windows','claude',1060)
        self.assertEqual(report['availability'],'stale')
        self.assertEqual(report['metrics'][0]['expires_at'],1060)
        self.assertEqual(report['metrics'][0]['availability'],'stale')

    def test_failed_refresh_preserves_stale_snapshot_and_success_recovers(self):
        self.cache.put('windows','claude',self.rows)
        self.cache.failed('windows','claude')
        report=self.cache.report('windows','claude',1001)
        self.assertEqual((report['availability'],report['fetched_at']),('failed',1000))
        self.assertEqual(report['metrics'][0]['availability'],'stale')
        self.assertEqual(report['metrics'][0]['value'],0)
        self.cache.put('windows','claude',self.rows)
        self.assertEqual(self.cache.report('windows','claude',1001)['availability'],'available')

    def test_failure_without_snapshot_and_explicit_unavailable(self):
        self.cache.failed('windows','claude')
        self.assertEqual(self.cache.report('windows','claude',1001)['availability'],'failed')
        self.cache.put('windows','claude',self.rows)
        self.cache.unavailable('windows','claude',fetched_at=1001)
        report=self.cache.report('windows','claude',1002)
        self.assertEqual((report['availability'],report['metrics']),('unavailable',[]))
        self.assertEqual(report['fetched_at'],1001)

    def test_cache_isolates_targets_providers_and_copies_both_directions(self):
        self.cache.put('windows','claude',self.rows)
        self.rows[0]['value']=99
        report=self.cache.report('windows','claude',1001)
        report['metrics'][0]['value']=88
        self.cache.get('windows','claude',1001)[0]['value']=77
        self.assertEqual(self.cache.report('windows','claude',1001)['metrics'][0]['value'],0)
        for target,provider in (('linux','claude'),('windows','codex')):
            self.assertEqual(self.cache.report(target,provider,1001)['availability'],'not_configured')

    def test_output_is_bounded_and_omission_is_truthful(self):
        self.cache.put('windows','claude',[dict(self.rows[0],window='window-'+str(i)) for i in range(5)])
        report=self.cache.report('windows','claude',1001)
        self.assertEqual(report['availability'],'partial')
        self.assertEqual(len(report['metrics']),4)
        self.assertEqual(report['omitted_metrics'],1)

    def test_source_is_sanitized_and_unrecognized_fields_are_not_exposed(self):
        self.rows[0].update(source='  fixture\n\x00<script> '+('x'*200),raw_error='sensitive exception')
        self.cache.put('windows','claude',self.rows)
        report=self.cache.report('windows','claude',1001)
        self.assertLessEqual(len(report['source']),64)
        self.assertTrue(all(32<=ord(c)<127 for c in report['source']))
        encoded=json.dumps(report)
        for value in ('sensitive exception','raw_error','<','>','\\u0000','\\n'):
            self.assertNotIn(value,encoded)

    def test_invalid_snapshot_is_rejected_atomically(self):
        self.cache.put('windows','claude',self.rows)
        invalid=[{'value':True},{'value':-1},{'value':101},{'value':float('nan')},
                 {'value':float('inf')},{'value':10**1000},{'unit':'requests'},
                 {'category':'api_spend'},{'window':'x'*49},{'window':'bad\nwindow'},
                 {'fetched_at':True},{'fetched_at':-1},{'resets_at':'not a timestamp'},
                 {'provider':'codex'},{'availability':'pretend'},{'window_minutes':-1},
                 {'source':{'raw':'exception'}}]
        for update in invalid:
            with self.subTest(update=str(update)[:100]):
                rows=deepcopy(self.rows);rows[0].update(update)
                with self.assertRaises(UsageError):self.cache.put('windows','claude',rows)
                self.assertEqual(self.cache.report('windows','claude',1001)['metrics'][0]['value'],0)
        with self.assertRaises(UsageError):self.cache.put('windows','claude',self.rows*65)

    def test_category_unit_mismatches_are_rejected_atomically(self):
        self.cache.put('windows','claude',self.rows)
        mismatches=(('subscription','USD_credit'),
                    ('included_credit','percent_used'),('included_credit','percent_remaining'),
                    ('purchased_credit','percent_used'),('purchased_credit','percent_remaining'))
        for category,unit in mismatches:
            for value in (0,None):
                with self.subTest(category=category,unit=unit,value=value):
                    rows=[dict(self.rows[0],category=category,unit=unit,value=value)]
                    with self.assertRaises(UsageError):self.cache.put('windows','claude',rows)
                    self.assertEqual(self.cache.report('windows','claude',1001)['metrics'][0]['value'],0)

    def test_cache_validates_scope_and_clock(self):
        for target,provider in (('../linux','claude'),('windows','unknown'),('', 'codex')):
            with self.subTest(target=target,provider=provider):
                with self.assertRaises(UsageError):self.cache.put(target,provider,[])
                with self.assertRaises(UsageError):self.cache.report(target,provider,1000)
        for now in (True,-1,float('nan')):
            with self.assertRaises(UsageError):self.cache.report('windows','claude',now)
