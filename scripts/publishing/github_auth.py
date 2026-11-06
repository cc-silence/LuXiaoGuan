"""Project-isolated GitHub publisher authorization; credentials stay in Windows vault."""
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime,timezone
from modules.platform_services.windows_credentials import read_generic_password,write_generic_password

ROOT=Path(__file__).resolve().parents[2]
from modules.app_update_services.sources import REPOSITORY
ACCOUNT='luxiaoguan-ui'
TARGET='RouterManager:Publisher:GitHub:luxiaoguan-ui:luxiaoguan-publish'
CLI=ROOT/'infra/update_gateway/github-cli/gh.exe'
CONFIG=ROOT/'build/publishing/github-config'

def environment(*,authenticated=False):
    env=os.environ.copy()
    for name in ['GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN','GH_CONFIG_DIR','GH_HOST','GH_BROWSER','BROWSER','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy']:
        env.pop(name,None)
    CONFIG.mkdir(parents=True,exist_ok=True)
    env.update(GH_CONFIG_DIR=str(CONFIG),GH_HOST='github.com',GH_PROMPT_DISABLED='1')
    from modules.network_policy import configured_proxy_channels
    routes=configured_proxy_channels()
    if routes:
        route=next((r.proxy_url for r in routes if r.proxy_url.startswith('http://')),None)
        if route:env.update(HTTP_PROXY=route,HTTPS_PROXY=route)
    if authenticated:
        token=read_generic_password(TARGET)
        if not token:raise ValueError('Dedicated GitHub publisher authorization is missing')
        env['GH_TOKEN']=token
    return env

def api(path,*,env=None):
    result=subprocess.run([str(CLI),'api',path],env=env if env is not None else environment(authenticated=True),capture_output=True,timeout=30)
    if result.returncode:raise ValueError('GitHub identity or repository permission check failed')
    return json.loads(result.stdout)

def verify(*,env=None):
    user=api('user',env=env)
    if user.get('login')!=ACCOUNT:raise ValueError('Wrong GitHub account; no publishing is permitted')
    repo=api('repos/'+REPOSITORY,env=env)
    if repo.get('full_name')!=REPOSITORY or not repo.get('permissions',{}).get('push'):
        raise ValueError('Dedicated account cannot publish to the configured repository')
    return dict(account=ACCOUNT,repository=REPOSITORY,can_publish=True,visibility=repo.get('visibility'))

def login():
    env=environment()
    edge=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    if not edge.is_file():raise ValueError('Expected Edge browser is unavailable')
    env['GH_BROWSER']='"'+str(edge)+'"'
    result=subprocess.run([str(CLI),'auth','login','--hostname','github.com','--git-protocol','https','--web','--skip-ssh-key'],env=env,timeout=900)
    if result.returncode:raise ValueError('GitHub device authorization was not completed')
    verified=verify(env=env)
    result=subprocess.run([str(CLI),'auth','token','--hostname','github.com','--user',ACCOUNT],env=env,capture_output=True,timeout=15)
    if result.returncode:raise ValueError('Dedicated GitHub credential retrieval failed')
    token=result.stdout.decode().strip()
    if not token:raise ValueError('Empty GitHub credential')
    write_generic_password(TARGET,ACCOUNT,token)
    if read_generic_password(TARGET)!=token:raise ValueError('Windows credential verification failed')
    import yaml
    hosts=CONFIG/'hosts.yml'
    if hosts.exists():
        data=yaml.safe_load(hosts.read_text(encoding='utf-8')) or {}
        def scrub(value):
            if isinstance(value,dict):
                value.pop('oauth_token',None)
                value.pop('oauth_refresh_token',None)
                for child in value.values():scrub(child)
        scrub(data)
        hosts.write_bytes(yaml.safe_dump(data,sort_keys=False).encode('utf-8'))
    verified=verify()
    verified.update(credential_storage='Windows Credential Manager',verified_at=datetime.now(timezone.utc).isoformat())
    (ROOT/'build/publishing/github-auth.json').write_bytes((json.dumps(verified,indent=2)+'\n').encode('utf-8'))
    print(json.dumps(verified),flush=True)

if __name__=='__main__':login()
