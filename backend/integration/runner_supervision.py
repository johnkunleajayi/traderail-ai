"""Finite verification process supervision only. Never a trading retry policy."""
from dataclasses import dataclass
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import time


class SupervisionError(RuntimeError):pass


class WorkerCleanupError(SupervisionError):
    def __init__(self,primary=None):
        super().__init__('WORKER_CLEANUP_FAILED')
        self.primary=primary


@dataclass(frozen=True)
class WorkerLimits:
    startup: float=120
    coordination: float=30
    execution: float=180
    cleanup: float=20
    lock_observation: float=8

    def __post_init__(self):
        if any(not 0<value<=300 for value in (
            self.startup,self.coordination,self.execution,self.cleanup,self.lock_observation)):
            raise ValueError('Finite verification deadlines required')


class Deadline:
    def __init__(self,seconds,*,clock=None):
        self.clock=clock or time.monotonic
        self.end=self.clock()+seconds

    def remaining(self,code='WORKER_DEADLINE_EXCEEDED'):
        value=self.end-self.clock()
        if value<=0:raise SupervisionError(code)
        return value


def wait_gate(gate,deadline,code):
    while not gate.wait(min(.2,deadline.remaining(code))):pass
    deadline.remaining(code)


class EvidenceLog:
    """Fresh append-only JSONL, fsynced before authorizing mutations.

    Callers supply fixed event codes and allowlisted metadata, never raw errors.
    Each worker has a different sidecar; no cross-process shared file handle.
    """
    def __init__(self,path=None):
        self.path=Path(path) if path is not None else None
        self.rows=[]
        if self.path is not None:
            with self.path.open('x',encoding='utf-8'):pass

    def record(self,event,**fields):
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',event):raise SupervisionError('INVALID_EVENT_CODE')
        allowed={'worker','source','category','sqlstate','backend_pid','transaction_outcome',
            'confirmation','phase','schema','worker_pid','exitcode','state','outcome','persisted',
            'operation','constraint','reused','primary_failure','cleanup_failure','expected_event','durable_state'}
        if set(fields)-allowed:raise SupervisionError('INVALID_EVENT_FIELDS')
        row={'event':event,'operational_time_utc':datetime.now(timezone.utc).isoformat(),
            'operational_monotonic_ns':time.monotonic_ns(),**fields}
        encoded=json.dumps(row,sort_keys=True)
        if self.path is not None:
            with self.path.open('a',encoding='utf-8') as stream:
                stream.write(encoded+'\n');stream.flush();os.fsync(stream.fileno())
        self.rows.append(row)

    def worker_path(self,name):
        if name not in ('A','B','OBSERVER','INSPECT1','INSPECT2'):raise SupervisionError('INVALID_WORKER_NAME')
        return str(self.path.with_name(self.path.stem+'-'+name+'.jsonl')) if self.path else None


def stop_owned(process,deadline,log,worker):
    """Terminate only the supplied spawned child. Never infer DB rollback."""
    failure=False
    try:
        if process.pid is not None:
            if process.is_alive():process.terminate()
            process.join(deadline.remaining('WORKER_CLEANUP_DEADLINE'))
            if process.is_alive():failure=True
    except BaseException:failure=True
    try:
        log.record('FAILED' if failure else 'NOT_STARTED' if process.pid is None else 'TERMINATED',worker=worker,source='SUPERVISOR',
            transaction_outcome='UNCONFIRMED_REQUIRES_DURABLE_INSPECTION',
            worker_pid=process.pid,exitcode=process.exitcode)
    except BaseException:failure=True
    if failure:raise WorkerCleanupError()


def join_owned(process,deadline,log,worker):
    process.join(deadline.remaining('WORKER_EXECUTION_DEADLINE'))
    deadline.remaining('WORKER_EXECUTION_DEADLINE')
    if process.is_alive() or process.exitcode!=0:raise SupervisionError('WORKER_DID_NOT_EXIT_CLEANLY')
    log.record('TERMINATED',worker=worker,source='SUPERVISOR',worker_pid=process.pid,
        exitcode=process.exitcode,transaction_outcome='UNCONFIRMED_REQUIRES_DURABLE_INSPECTION')
