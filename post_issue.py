"""Post today's signal using gh CLI and GITHUB_TOKEN; do not promise email delivery."""
import json
import os
import subprocess
import sys
from pathlib import Path


def gh(*args):
    return subprocess.run(['gh',*args],text=True,capture_output=True,check=True).stdout.strip()


def main():
    status=sys.argv[1] if len(sys.argv)>1 else 'success'
    owner=os.environ.get('GITHUB_REPOSITORY_OWNER','')
    mention=f'@{owner}\n\n' if owner else ''
    if status=='success':
        data=json.loads(Path('signal_latest.json').read_text(encoding='utf-8'))
        title=data['title'][:220]
        body=mention+Path('signal_latest.txt').read_text(encoding='utf-8')
        label='paxg-signal'
    else:
        import datetime as dt
        title='⚠ PAXG 日线信号失败｜'+dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d UTC')
        log=Path('run.log').read_text(encoding='utf-8',errors='replace')[-4500:] if Path('run.log').exists() else '(no log)'
        body=mention+'今天信号生成或账本提交失败，**请勿依据旧信号交易**。检查 Actions 每一步日志，尤其是账本提交步骤；下方是信号进程日志。\n\n```text\n'+log+'\n```'
        label='paxg-error'
    if '--dry-run' in sys.argv:
        print(title+'\n'+body[:900]);return
    gh('label','create',label,'--color',('B60205' if status!='success' else '0E8A16'),'--force')
    # Date + label idempotency, even if a same-day manual fill changed the headline.
    existing=json.loads(gh('issue','list','--state','all','--limit','500','--json','title,number,labels'))
    today_key='信号日 '+data['date'] if status=='success' else title
    if any(today_key in i['title'] and any(l['name']==label for l in i['labels']) for i in existing):
        print('Existing issue for today; skipping duplicate:',today_key);return
    Path('issue_body.md').write_text(body,encoding='utf-8')
    created=gh('issue','create','--title',title,'--body-file','issue_body.md','--label',label)
    print('Created:',created)
    if status=='success':
        for i in existing:
            if i['number'] and any(l['name']==label for l in i['labels']):
                # old closed issues may be in list; gh issue close is harmless but noisy
                subprocess.run(['gh','issue','close',str(i['number'])],capture_output=True,text=True)


if __name__=='__main__':
    main()
