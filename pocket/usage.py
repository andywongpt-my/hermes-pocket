"""Read-only parsers for explicitly supplied official usage snapshots.

No account access, credential discovery or model requests. Subscription allowances
and usage credits are never presented as OpenAI/Anthropic API spending.
"""
from copy import deepcopy
from datetime import datetime
import math
import threading

class UsageError(ValueError):pass

def number(value,maximum=None):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0 or maximum is not None and value>maximum:raise UsageError('invalid numeric usage')
    return value

def epoch(value):
    if isinstance(value,str):
        try:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            if parsed.tzinfo is None:raise ValueError()
            return parsed.timestamp()
        except ValueError:raise UsageError('invalid reset timestamp') from None
    return number(value)

def item(provider,category,window,value,unit,fetched_at,reset=None,source=None):
    fetched_at=number(fetched_at);reset=epoch(reset) if reset is not None else None
    return {'provider':provider,'category':category,'window':window,'value':value,'unit':unit,
            'fetched_at':fetched_at,'resets_at':reset,'source':source,
            'availability':'stale' if reset is not None and reset<=fetched_at else 'available'}

def obj(value):
    if not isinstance(value,dict):raise UsageError('invalid usage object')
    return value

def codex_limits(data,fetched_at):
    data=obj(data);result=[]
    buckets=data.get('rateLimitsByLimitId')
    if buckets is None:buckets={'codex':data.get('rateLimits')}
    for bucket,limits in obj(buckets).items():
        if limits is None:continue
        for name in ('primary','secondary'):
            value=obj(limits).get(name)
            if value is None:continue
            value=obj(value)
            record=item('codex','subscription',str(bucket)+':'+name,number(value.get('usedPercent'),100),'percent_used',fetched_at,value.get('resetsAt'),'account/rateLimits')
            duration=value.get('windowDurationMins')
            record['window_minutes']=number(duration) if duration is not None else None
            result.append(record)
    return result

def claude_limits(data,fetched_at):
    limits=obj(data).get('rate_limits') or {};result=[]
    for name in ('five_hour','seven_day'):
        value=obj(limits).get(name)
        if value is None:continue
        value=obj(value)
        result.append(item('claude','subscription',name,number(value.get('used_percentage'),100),'percent_used',fetched_at,value.get('resets_at'),'Claude Code statusline'))
    return result

def ollama_balance(data,fetched_at):
    data=obj(data);result=[];included=obj(data.get('included',{}));purchased=obj(data.get('purchased',{}))
    if 'balance_usd' in included:
        result.append(item('ollama_cloud','included_credit','included',number(included['balance_usd']),'USD_credit',fetched_at,obj(included.get('period',{})).get('until'),'/api/balance'))
    else:
        for name in ('session','weekly'):
            if name in included:
                value=obj(included[name])
                result.append(item('ollama_cloud','subscription',name,number(value.get('remaining_percent'),100),'percent_remaining',fetched_at,value.get('resets_at'),'/api/balance'))
    if 'balance_usd' in purchased:result.append(item('ollama_cloud','purchased_credit','purchased',number(purchased['balance_usd']),'USD_credit',fetched_at,source='/api/balance'))
    return result

class UsageCache:
    def __init__(self,ttl=300):self.ttl=number(ttl);self.rows={};self.lock=threading.RLock()
    def put(self,target,provider,rows):
        with self.lock:self.rows[(target,provider)]=deepcopy(rows)
    def get(self,target,provider,now):
        now=number(now)
        with self.lock:rows=deepcopy(self.rows.get((target,provider),[]))
        for row in rows:
            if now-row['fetched_at']>=self.ttl or row['resets_at'] is not None and now>=row['resets_at']:row['availability']='stale'
        return rows
