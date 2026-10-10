"""Year-scale isolated workload, fresh uploads and concurrent report requests.

python tools/benchmark_operational.py --employees 10000 --devices 20 --days 365 --output operational.json
The temporary database is removed on exit. No physical terminal is contacted.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--employees',type=int,default=10000)
    parser.add_argument('--devices',type=int,default=20)
    parser.add_argument('--days',type=int,default=365)
    parser.add_argument('--fresh-per-device',type=int,default=1000)
    parser.add_argument('--output',type=Path,default=Path('operational.json'))
    args=parser.parse_args()
    if not 1<=args.employees<=50000 or not 1<=args.devices<=min(100,args.employees) or not 1<=args.days<=400 or not 1<=args.fresh_per_device<=5000 or args.employees*args.days*2>20000000:
        parser.error('Use 1–50000 employees, 1–100 devices, 1–400 days, 1–5000 fresh records per device; max 20M base punches.')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    result={'status':'failed','version':'','environment':{'python':platform.python_version(),'platform':platform.platform(),'cpu_count':os.cpu_count()},
        'employees':args.employees,'devices':args.devices,'days':args.days,'base_punches':args.employees*args.days*2,
        'isolated_temporary_database':True,'physical_devices_verified':False,'transport':'concurrent ASGI HTTP requests',
        'started_at_utc':datetime.now(__import__('datetime').timezone.utc).isoformat()}
    overall=time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix='hader-operational-') as temporary:
            folder=Path(temporary)
            for name in list(os.environ):
                if name.startswith('HADER_'):del os.environ[name]
            os.environ.update({'HADER_DATA_DIR':str(folder),'HADER_CONFIG':str(folder/'none.ini'),
                'HADER_DATABASE_URL':f'sqlite:///{folder / "operational.db"}','HADER_HOST':'127.0.0.1',
                'HADER_PORTAL_PORT':'0','HADER_ADMS_PORTS':'','HADER_STORAGE_SYNCHRONOUS':'FULL'})
            import benchmark_scale as scale
            from hader.db import now, engine, session_scope
            from hader import models as m, store
            from hader.version import VERSION
            from hader.intake import IntakeBatch
            from sqlalchemy import func, select
            from hader.app import app
            from hader.security import hash_password
            from fastapi.testclient import TestClient
            result['version']=VERSION
            end=now().date()-timedelta(days=2);start=end-timedelta(days=args.days-1)
            tick=time.perf_counter();ids=scale.seed(args,start,end)
            result['seed_seconds']=round(time.perf_counter()-tick,6)
            result['date_range']={'start':str(start),'end':str(end)}
            with session_scope() as db:
                admin=db.scalar(select(m.User).where(m.User.username=='admin'))
                admin.password_hash=hash_password('isolated-operational-password');admin.must_change_password=False
                assert db.scalar(select(func.count()).select_from(m.Transaction))==result['base_punches']
            with TestClient(app) as client:
                assert client.post('/api/auth/login',json={'username':'admin','password':'isolated-operational-password'}).status_code==200
                checks=[]
                for key,expected in [('transactions',result['base_punches']),('daily',args.employees*args.days),('summary',args.employees)]:
                    tick=time.perf_counter();response=client.get(f'/api/reports/{key}',params={'start':str(start),'end':str(end),'lang':'en'})
                    assert response.status_code==200,response.text
                    report=response.json();assert report['total']==expected and len(report['rows'])==min(200,expected)
                    checks.append({'key':key,'seconds':round(time.perf_counter()-tick,6),'total':expected,'page_rows':len(report['rows']),'validation':'passed'})
                    if key=='summary':scale.validate_report(report,key,args,start,end,expected_total=expected)
                result['initial_reports']=checks
                stamp=now().replace(microsecond=0)
                payloads=[]
                for device in range(args.devices):
                    assigned=list(range(device+1,args.employees+1,args.devices))
                    lines=[]
                    for i in range(args.fresh_per_device):
                        code=assigned[i%len(assigned)]
                        when=stamp-timedelta(seconds=i//len(assigned))
                        if when.date()!=stamp.date():
                            raise ValueError('Run a few seconds after midnight so every new test record stays in today.')
                        lines.append(f'{code}\t{when:%Y-%m-%d %H:%M:%S}\t15\t{i%2}\t0\t0')
                    payloads.append(('\n'.join(lines)+'\n').encode())
                def upload(index):
                    tick=time.perf_counter();response=client.post('/iclock/cdata',params={'SN':scale.device_sn(index),'table':'ATTLOG','Stamp':'fresh-operational'},content=payloads[index])
                    assert response.status_code==200,response.text
                    return {'kind':'fresh_upload','device':scale.device_sn(index),'seconds':round(time.perf_counter()-tick,6),'status':200}
                def read(index):
                    key=('transactions','daily','summary')[index%3];tick=time.perf_counter()
                    response=client.get(f'/api/reports/{key}',params={'start':str(start),'end':str(end),'lang':'en'})
                    assert response.status_code==200
                    expected={'transactions':result['base_punches'],'daily':args.employees*args.days,'summary':args.employees}[key]
                    assert response.json()['total']==expected
                    return {'kind':'report','key':key,'seconds':round(time.perf_counter()-tick,6),'status':200}
                tick=time.perf_counter()
                with ThreadPoolExecutor(max_workers=args.devices+6) as pool:
                    futures=[pool.submit(upload,i) for i in range(args.devices)]+[pool.submit(read,i) for i in range(6)]
                    samples=[future.result() for future in futures]
                result['mixed_workload']={'seconds':round(time.perf_counter()-tick,6),'fresh_requests':args.devices,'report_requests':6,'samples':samples}
                expected_new=args.devices*args.fresh_per_device
                with session_scope() as db:
                    actual=db.scalar(select(func.count()).select_from(m.Transaction));assert actual==result['base_punches']+expected_new,(actual,expected_new)
                    batches=list(db.scalars(select(IntakeBatch)).all());assert len(batches)==args.devices
                    assert sum(b.accepted for b in batches)==expected_new and not sum(b.rejected for b in batches)
                    assert store.get(db,'tcp.write_back') is False
                tick=time.perf_counter()
                with ThreadPoolExecutor(max_workers=args.devices) as pool:list(pool.map(upload,range(args.devices)))
                with session_scope() as db:
                    assert db.scalar(select(func.count()).select_from(m.Transaction))==actual
                    assert sum(db.scalars(select(IntakeBatch.duplicates)).all())==expected_new
                result['fresh_ingestion']={'new_records':expected_new,'punches_after':actual,'reupload_records':expected_new,'reupload_seconds':round(time.perf_counter()-tick,6),'deduplication':'passed','raw_archiving':'passed','write_back':False}
                result['monitor']={'today':str(stamp.date()),'expected':expected_new}
                mon=client.get('/api/monitor').json();assert mon['date']==str(stamp.date()) and mon['total']==expected_new
                assert all(row['punch_time'][:10]==str(stamp.date()) for row in mon['rows'])
                result['storage_synchronous']=client.get('/api/operations/health').json()['storage']['active_synchronous']
                assert result['storage_synchronous']=='FULL'
            engine.dispose()
        result['temporary_database_removed']=not folder.exists();result['status']='passed'
    except Exception as error:
        result['error']=f'{type(error).__name__}: {error}'
    result['total_seconds']=round(time.perf_counter()-overall,6)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
