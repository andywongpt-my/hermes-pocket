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
