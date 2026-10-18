"""Read-only parsers for explicitly supplied official usage snapshots.

No account access, credential discovery or model requests. Subscription allowances
and usage credits are never presented as OpenAI/Anthropic API spending.
"""
from copy import deepcopy
from datetime import datetime
import math
import re
import threading

PROVIDERS=('codex','claude','ollama_cloud')
MAX_METRICS=4
MAX_SNAPSHOT_METRICS=64
MAX_TIMESTAMP=2**53-1

class UsageError(ValueError):pass

def number(value,maximum=None):
    if type(value) not in (int,float):raise UsageError('invalid numeric usage')
    try:finite=math.isfinite(value)
    except OverflowError:finite=False
    if not finite or value<0 or maximum is not None and value>maximum:raise UsageError('invalid numeric usage')
    return value

def nullable_number(value,maximum=None):
    return None if value is None else number(value,maximum)

def epoch(value):
    if isinstance(value,str):
        try:
            if len(value)>48:raise ValueError()
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            if parsed.tzinfo is None:raise ValueError()
            return number(parsed.timestamp(),MAX_TIMESTAMP)
        except (ValueError,OverflowError,OSError):raise UsageError('invalid reset timestamp') from None
    return number(value,MAX_TIMESTAMP)

def source_label(value):
    """Keep bounded display labels only; never forward arbitrary snapshot fields."""
    if value is None:return None
    if not isinstance(value,str):raise UsageError('invalid usage source')
    value=' '.join(value.split())
    value=''.join(c for c in value if c.isascii() and (c.isalnum() or c in ' /_:.-()'))
    return value[:64].strip() or None

def metric(row,provider):
    row=obj(row)
    if row.get('provider',provider)!=provider:raise UsageError('invalid usage provider')
    category=row.get('category');unit=row.get('unit');window=row.get('window')
    if category not in ('subscription','included_credit','purchased_credit'):raise UsageError('invalid usage category')
    allowed_units=('percent_used','percent_remaining') if category=='subscription' else ('USD_credit',)
    if unit not in allowed_units:raise UsageError('invalid usage unit')
    if not isinstance(window,str) or not 1<=len(window)<=48 or any(not 32<=ord(c)<127 for c in window):raise UsageError('invalid usage window')
    value=nullable_number(row.get('value'),100 if unit!='USD_credit' else None)
    fetched_at=nullable_number(row.get('fetched_at'),MAX_TIMESTAMP)
    reset=epoch(row['resets_at']) if row.get('resets_at') is not None else None
    availability=row.get('availability','available' if value is not None else 'unavailable')
    if availability not in ('available','unavailable','stale'):raise UsageError('invalid metric availability')
    if value is None:availability='unavailable'
    elif fetched_at is None or reset is not None and reset<=fetched_at:availability='stale'
    result={'provider':provider,'category':category,'window':window,'value':value,'unit':unit,
            'fetched_at':fetched_at,'resets_at':reset,'source':source_label(row.get('source')),
            'availability':availability}
    if 'window_minutes' in row:result['window_minutes']=nullable_number(row['window_minutes'],MAX_TIMESTAMP/60)
    return result

def item(provider,category,window,value,unit,fetched_at,reset=None,source=None):
    return metric({'category':category,'window':window,'value':value,'unit':unit,
                   'fetched_at':number(fetched_at,MAX_TIMESTAMP),'resets_at':reset,'source':source},provider)

def obj(value):
    if not isinstance(value,dict):raise UsageError('invalid usage object')
    return value

def codex_limits(data,fetched_at):
    data=obj(data);result=[]
    buckets=data.get('rateLimitsByLimitId')
    if buckets is None:buckets={'codex':data.get('rateLimits')}
    if len(obj(buckets))>MAX_SNAPSHOT_METRICS:raise UsageError('too many usage windows')
    for bucket,limits in obj(buckets).items():
        if limits is None:continue
        for name in ('primary','secondary'):
            value=obj(limits).get(name)
            if value is None:continue
            value=obj(value)
            record=item('codex','subscription',str(bucket)+':'+name,nullable_number(value.get('usedPercent'),100),'percent_used',fetched_at,value.get('resetsAt'),'account/rateLimits')
            duration=value.get('windowDurationMins')
            record['window_minutes']=number(duration) if duration is not None else None
            result.append(record)
            if len(result)>MAX_SNAPSHOT_METRICS:raise UsageError('too many usage windows')
    return result

def claude_limits(data,fetched_at):
    limits=obj(data).get('rate_limits') or {};result=[]
    for name in ('five_hour','seven_day'):
        value=obj(limits).get(name)
        if value is None:continue
        value=obj(value)
        result.append(item('claude','subscription',name,nullable_number(value.get('used_percentage'),100),'percent_used',fetched_at,value.get('resets_at'),'Claude Code statusline'))
    return result

def ollama_balance(data,fetched_at):
    data=obj(data);result=[];included=obj(data.get('included',{}));purchased=obj(data.get('purchased',{}))
    if 'balance_usd' in included:
        result.append(item('ollama_cloud','included_credit','included',nullable_number(included['balance_usd']),'USD_credit',fetched_at,obj(included.get('period',{})).get('until'),'/api/balance'))
    else:
        for name in ('session','weekly'):
            if name in included:
                value=obj(included[name])
                result.append(item('ollama_cloud','subscription',name,nullable_number(value.get('remaining_percent'),100),'percent_remaining',fetched_at,value.get('resets_at'),'/api/balance'))
    if 'balance_usd' in purchased:result.append(item('ollama_cloud','purchased_credit','purchased',nullable_number(purchased['balance_usd']),'USD_credit',fetched_at,source='/api/balance'))
    return result

class UsageCache:
    """Thread-safe supplied-snapshot storage; no collectors or account access."""
    def __init__(self,ttl=300):
        self.ttl=number(ttl,MAX_TIMESTAMP);self._entries={};self.lock=threading.RLock()

    def _key(self,target,provider):
        if not isinstance(target,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',target) or provider not in PROVIDERS:
            raise UsageError('invalid usage scope')
        return target,provider

    def configure(self,target,provider,*,source=None):
        """Explicit configuration starts without an observation."""
        key=self._key(target,provider);source=source_label(source)
        with self.lock:
            self._entries[key]={'metrics':[],'fetched_at':None,'source':source,'failed':False}

    def put(self,target,provider,rows,*,fetched_at=None,source=None):
        key=self._key(target,provider)
        if not isinstance(rows,(list,tuple)) or len(rows)>MAX_SNAPSHOT_METRICS:raise UsageError('invalid usage snapshot')
        # Normalize into new dictionaries before replacing the last successful entry.
        rows=[metric(row,provider) for row in rows]
        fetched_at=nullable_number(fetched_at,MAX_TIMESTAMP);source=source_label(source)
        if rows:
            times=[row['fetched_at'] for row in rows]
            fetched_at=min(times) if all(t is not None for t in times) else None
        if source is None:
            sources={row['source'] for row in rows}
            if len(sources)==1:source=sources.pop()
        with self.lock:
            previous=self._entries.get(key,{})
            if source is None:source=previous.get('source')
            self._entries[key]={'metrics':rows,'fetched_at':fetched_at,'source':source,'failed':False}

    def unavailable(self,target,provider,*,fetched_at=None,source=None):
        """A supplied source has no supported observations; clear older values."""
        self.put(target,provider,[],fetched_at=fetched_at,source=source)

    def failed(self,target,provider,*,source=None):
        """Record a refresh failure without accepting or exposing exception text."""
        key=self._key(target,provider);source=source_label(source)
        with self.lock:
            entry=self._entries.setdefault(key,{'metrics':[],'fetched_at':None,'source':None})
            entry['failed']=True
            if source is not None:entry['source']=source

    def get(self,target,provider,now):
        return self.report(target,provider,now)['metrics']

    def report(self,target,provider,now):
        key=self._key(target,provider);now=number(now,MAX_TIMESTAMP)
        with self.lock:entry=deepcopy(self._entries.get(key))
        result={'provider':provider,'availability':'not_configured','value':None,
                'fetched_at':None,'source':None,'metrics':[]}
        if entry is None:return result
        rows=entry['metrics']
        for row in rows:
            expiry=[]
            if row['fetched_at'] is not None:expiry.append(min(row['fetched_at']+self.ttl,MAX_TIMESTAMP))
            if row['resets_at'] is not None:expiry.append(row['resets_at'])
            row['expires_at']=min(expiry) if expiry else None
            if row['value'] is not None and (entry['failed'] or row['fetched_at'] is None or
                    now<row['fetched_at'] or row['expires_at'] is not None and now>=row['expires_at']):
                row['availability']='stale'
        states=[row['availability'] for row in rows]
        if entry['failed']:availability='failed'
        elif not rows or all(row['value'] is None for row in rows):availability='unavailable'
        elif all(state=='stale' for state in states):availability='stale'
        elif all(state=='available' for state in states) and len(rows)<=MAX_METRICS:availability='available'
        else:availability='partial'
        result.update(availability=availability,fetched_at=entry['fetched_at'],source=entry['source'],metrics=rows[:MAX_METRICS])
        if len(rows)>MAX_METRICS:result['omitted_metrics']=len(rows)-MAX_METRICS
        return result
