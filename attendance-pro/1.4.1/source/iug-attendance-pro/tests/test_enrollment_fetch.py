from datetime import datetime
from sqlalchemy import event, func, select

from hader import models as m, store
from hader.adms import sync
from hader.db import engine, session_scope


def test_attendance_only_does_not_request_known_employee_biometrics(client):
    with session_scope() as db:
        device=m.Device(sn='READ',alias='READ',enabled=True);db.add(device)
        employee=m.Employee(emp_code='8901',first_name='Known');db.add(employee);db.flush()
        assert sync.ask_for_missing(db,device,{'8901'})==0
        assert db.scalar(select(func.count()).select_from(m.DeviceCommand).where(m.DeviceCommand.device_sn=='READ'))==0
        employee.first_name='';employee.last_name=''
        assert sync.ask_for_missing(db,device,{'8901'})==1
        command=db.scalar(select(m.DeviceCommand).where(m.DeviceCommand.device_sn=='READ'))
        assert 'tablename=user' in command.content


def test_biometric_fetch_is_batched_deduplicated_and_recoverable_after_rollback(client):
    statements=[]
    def record(_conn,_cursor,sql,_parameters,_context,_many):statements.append(sql)
    event.listen(engine,'before_cursor_execute',record)
    try:
        with session_scope() as db:
            device=m.Device(sn='BULKREAD',alias='BULKREAD',push_ver='2.4.1',enabled=True);db.add(device)
            for i in range(120):db.add(m.Employee(emp_code=str(9000+i),first_name='Known'))
            store.set_(db,'adms.read_bio_on_punch',True)
        with session_scope() as db:
            device=db.scalar(select(m.Device).where(m.Device.sn=='BULKREAD'));statements.clear()
            pins={str(9000+i) for i in range(120)}
            count=sync.ask_for_missing(db,device,pins)
            assert count==480
            assert len(statements)<40, f'Per-person SQL returned: {len(statements)} statements'
            assert sync.ask_for_missing(db,device,pins)==0
            db.rollback()
        with session_scope() as db:
            device=db.scalar(select(m.Device).where(m.Device.sn=='BULKREAD'))
            assert sync.ask_for_missing(db,device,pins)==480
            assert db.scalar(select(func.count()).select_from(m.DeviceCommand).where(m.DeviceCommand.device_sn=='BULKREAD'))==480
    finally:event.remove(engine,'before_cursor_execute',record)
