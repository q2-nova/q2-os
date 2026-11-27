"""Opt-in, owned SSH reuse and numeric client timing for disposable guests."""
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid


def prepare(out,cycle):
    scope=out/('ssh-mux-'+str(cycle));control=scope/'s'
    if len(os.fsencode(control))>100:raise ValueError('SSH control socket path too long')
    scope.mkdir(mode=0o700)
    nonce=uuid.uuid4().hex
    with (scope/'owner').open('x') as stream:stream.write(nonce+'\n')
    return dict(scope=str(scope),control=str(control),nonce=nonce)


def arguments(base,owner):
    if base[:3]!=['ssh','-F','/dev/null'] or base[-1]!='root@127.0.0.1':raise ValueError('isolated generated SSH context required')
    return base[:-1]+['-o','ControlMaster=auto','-o','ControlPersist=60',
        '-o','ControlPath='+owner['control'],'-o','ServerAliveInterval=10',
        '-o','ServerAliveCountMax=3']+base[-1:]


def socket_present(control):
    try:
        mode = control.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISSOCK(mode):
        raise ValueError('unexpected SSH control inode')
    return True


def cleanup(owner,ssh):
    scope,control=Path(owner['scope']),Path(owner['control'])
    if scope.is_symlink() or not scope.is_dir() or (scope/'owner').is_symlink() or (scope/'owner').read_text()!=owner['nonce']+'\n':raise ValueError('owned SSH scope mismatch')
    if set(p.name for p in scope.iterdir())-{'owner','s'}:raise ValueError('unexpected SSH scope contents')
    present = socket_present(control)
    record=dict(passed=False,scope=str(scope),control_closed=False)
    if ssh is not None:
        if 'ControlPath='+owner['control'] not in ssh or ssh[-1]!='root@127.0.0.1':raise ValueError('SSH owner/control mismatch')
        record['exit_status']=subprocess.run(ssh[:-1]+['-O','exit']+ssh[-1:],capture_output=True,timeout=5).returncode
        check=subprocess.run(ssh[:-1]+['-O','check']+ssh[-1:],capture_output=True,timeout=5)
        if check.returncode==0:raise RuntimeError('owned SSH master remains alive')
    elif present:raise ValueError('SSH control exists without a launched context')
    # On exit OpenSSH may unlink its socket after the negative check.
    # The nonce and closed-master checks still gate stale-socket cleanup.
    if socket_present(control):
        control.unlink(missing_ok=True)
    (scope/'owner').unlink();scope.rmdir()
    record.update(passed=True,control_closed=True)
    return record


class Trace:
    """No command/payload/key contents: only durations, result and byte counts."""
    def __init__(self,record):self.rows=record.setdefault('ssh_runs',[])
    def __enter__(self):
        self.original=subprocess.run
        def run(command,*args,**kwargs):
            if not isinstance(command,(list,tuple)) or not command or command[0]!='ssh':return self.original(command,*args,**kwargs)
            row=dict(status='requested',control=('-O' in command),multiplex=('ControlMaster=auto' in command));self.rows.append(row);start=time.monotonic()
            try:
                result=self.original(command,*args,**kwargs)
                size=lambda v:len(v.encode()) if isinstance(v,str) else len(v or b'')
                row.update(status='returned',exit_status=result.returncode,stdout_bytes=size(result.stdout),stderr_bytes=size(result.stderr))
                return result
            except BaseException as error:
                row.update(status='failed',error_type=type(error).__name__);raise
            finally:row['seconds']=time.monotonic()-start
        subprocess.run=run
        return self
    def __exit__(self,*_):subprocess.run=self.original
