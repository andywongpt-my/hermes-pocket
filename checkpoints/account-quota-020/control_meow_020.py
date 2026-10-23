"""Approved fixed-path meow activation/verification/field-only rollback controller."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shlex
import stat
import subprocess
import sys
import time
import types

B=Path('/home/example/.hermes/pocket/gateway-only-20261015')
P=B/'staging/account-quota-020-claude-meow-passive-r1'
R=B/'staging/account-quota-020-claude-passive-r1'
SETTINGS=Path('/home/example/.claude/settings.json')
BACKUP=P/'statusline-before.json'
sha=lambda raw:hashlib.sha256(raw).hexdigest()
canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def checked(path,digest=None):
    info=path.lstat();assert stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not path.is_symlink()
    assert info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=1048576
    raw=path.read_bytes();after=path.lstat()
    assert (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
    assert digest is None or sha(raw)==digest
    return raw

def load(path,name,digest):
    raw=checked(path,digest);module=types.ModuleType(name);module.__file__=str(path)
    sys.modules[name]=module;exec(compile(raw,str(path),'exec'),module.__dict__);return module

def save(path,value):
    assert not os.path.lexists(path),'receipt_exists_check_outcome_first'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as stream:stream.write(canonical(value)+b'\n');stream.flush();os.fsync(stream.fileno())

def replace_profile(raw,expected):
    target=R/'combined-profile.json';assert sha(checked(target))==expected,'profile_changed'
    temporary=R/'.meow-profile-update.tmp'
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb')as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        assert sha(checked(target))==expected;os.replace(temporary,target)
        assert checked(target)==raw
    finally:
        if temporary.exists():temporary.unlink()

def units():
    return {name:subprocess.check_output(['systemctl','--user','show',name,'--property=MainPID','--value'],text=True,timeout=5).strip()
        for name in ('hermes-gateway.service','hermes-pocket-gateway-only.service','hermes-pocket-funnel-021.service')}

def main(mode,freeze_sha):
    freeze=json.loads(checked(P/'deployment-freeze.json',freeze_sha))
    assert sha(checked(P/'control_meow_020.py'))==freeze['controller_sha256']
    preparation=json.loads(checked(P/'preparation-result.json',freeze['preparation_sha256']))
    if mode=='rollback':
        # Recovery needs only its own pinned controller/settings helper and exact profile guards.
        checked(P/'settings/claude_settings_020.py',freeze['source_pins']['settings/claude_settings_020.py'])
        guardian=load(P/'settings/meow_settings_020.py','meow_rollback_settings',freeze['source_pins']['settings/meow_settings_020.py'])
        current_profile=checked(R/'combined-profile.json')
        target_profile=checked(P/'receiver-profile-enabled.json',preparation['enabled_profile_sha256'])
        prior=json.loads(checked(P/'receiver-profile-before.json'));prior_raw=canonical(prior)+b'\n'
        assert sha(prior_raw)==preparation['expected_paused_profile_sha256']
        if sha(current_profile)==sha(target_profile):
            replace_profile(prior_raw,sha(target_profile))
        else:
            assert sha(current_profile)==sha(prior_raw),'profile_owner_changed'
        guardian.restore(SETTINGS,BACKUP)
        result={'ok':True,'statusline_restored':True,'profile_restored_paused':True,'settings_metadata_preserved':guardian.metadata(SETTINGS)==preparation['settings_metadata_before'],'services_untouched':True}
        if not (P/'rollback-result.json').exists():save(P/'rollback-result.json',result)
        print(json.dumps(result,sort_keys=True),flush=True)
        return
    qualification=json.loads(checked(P/'qualification-result.json',freeze['qualification_sha256']))
    assert qualification['ok'] and qualification['source_pins']==freeze['source_pins']
    for relative,digest in freeze['source_pins'].items():checked(P/relative,digest)
    observer=load(P/'observer/claude_meow_020.py','meow_control_observer',freeze['source_pins']['observer/claude_meow_020.py'])
    remote=load(R/'claude_remote_020.py','meow_control_receiver','be8838bd7aac1ed5c74a10477dda9e0290474f3638bf73c47cef4614f3ed94d8')
    guardian=load(P/'settings/meow_settings_020.py','meow_control_settings',freeze['source_pins']['settings/meow_settings_020.py'])
    config_raw=checked(P/'meow-config.json',preparation['meow_config_sha256']);config=json.loads(config_raw)
    observer._config(config);observer._dependencies(observer._pins(config['source_pins']));observer._verify_auth(config,observer._pins(config['auth_file_pins']))
    for folder in (P,P/'runtime',P/'observer',P/'settings',P/'backend',P/'backend/pocket',P/'claude-callback-020',P/'callback-state',P/'.meow-claude.account-quota-v1',P/'relay-lock'):
        observer._chain(folder);info=folder.lstat();assert info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700
    target_profile=checked(P/'receiver-profile-enabled.json',preparation['enabled_profile_sha256']);new_profile=remote.validate_config(json.loads(target_profile))
    profile_path=R/'combined-profile.json';current_profile=checked(profile_path);ops=remote._load_ops(remote.PATHS,new_profile)
    before_units=units()
    if mode=='activate':
        assert not BACKUP.exists() and not (P/'installation-result.json').exists(),'activation_already_attempted_check_outcome'
        assert sha(current_profile)==preparation['expected_paused_profile_sha256']
        remote._policy(remote.PATHS,json.loads(current_profile),sha(current_profile),ops)
        assert sha(SETTINGS.read_bytes())==preparation['settings_before_sha256'],'settings_owner_changed'
        assert guardian.metadata(SETTINGS)==preparation['settings_metadata_before']
        assert observer._identity(observer._bounded_process(config['auth_argv'],maximum=65536,timeout=8))==config['expected_account_sha256']
        observer._verify_auth(config,observer._pins(config['auth_file_pins']))
        save(P/'receiver-profile-before.json',json.loads(current_profile))
        command=shlex.join([preparation['python_resolved'],'-B',str(P/'observer/claude_meow_020.py'),'--config',str(P/'meow-config.json'),'--config-sha256',preparation['meow_config_sha256']])
        replace_profile(target_profile,sha(current_profile))
        try:
            remote._policy(remote.PATHS,new_profile,sha(target_profile),ops)
            empty=subprocess.run(config['receiver_argv'],input=b'',stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8)
            assert empty.returncode==1 and json.loads(empty.stdout)=={'ok':False,'category':'receiver_rejected'}
            receipt=guardian.install(SETTINGS,BACKUP,command)
        except BaseException:
            # Disable the new source before any field-only recovery. Never alter Codex.
            if sha(checked(profile_path))==sha(target_profile):replace_profile(current_profile,sha(target_profile))
            if BACKUP.exists():
                doc=guardian.base.parse(guardian.base.read_raw(SETTINGS)[0]);saved=guardian.base._backup(SETTINGS,BACKUP)
                if doc.get('statusLine')==saved['installed_statusline']:guardian.restore(SETTINGS,BACKUP)
            raise
        result={'ok':True,'installed_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'settings_receipt':receipt,'settings_metadata_preserved':guardian.metadata(SETTINGS)==preparation['settings_metadata_before'],'statusline_installed':True,'profile_enabled':True,'config_sha256':sha(config_raw),'profile_sha256':sha(target_profile),'service_pids_before':before_units,'service_pids_after':units(),'model_requests':0,'synthetic_production_publications':0}
        save(P/'installation-result.json',result)
    elif mode=='verify':
        assert sha(current_profile)==sha(target_profile)
        remote._policy(remote.PATHS,new_profile,sha(target_profile),ops)
        assert guardian.base.verify_settings(SETTINGS,BACKUP)
        assert guardian.metadata(SETTINGS)==preparation['settings_metadata_before']
        result={'ok':True,'statusline_installed':True,'profile_enabled':True,'settings_metadata_preserved':True,'source_config_pins_verified':True,'service_pids':units(),'local_state_exists':(P/'callback-state/claude-state.json').exists(),'local_snapshot_exists':(P/'.meow-claude.account-quota-v1/claude.json').exists(),'production_claude_snapshot_exists':(remote.PATHS.numeric/'claude.json').exists(),'model_requests':0}
        audit=P/'relay-lock/audit.json'
        if audit.exists():
            value=json.loads(checked(audit));assert set(value)<={'status','observed_at','attempted_at','fetched_at','snapshot_sha256','error'};result['audit']=value
        else:result['audit']=None
    else:raise ValueError('unsupported_control_mode')
    print(json.dumps(result,sort_keys=True),flush=True)

if __name__=='__main__':
    assert len(sys.argv)==4 and sys.argv[2]=='--freeze-sha256'
    main(sys.argv[1],sys.argv[3])
