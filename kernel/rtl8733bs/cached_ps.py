"""Closed wire decoder for the driver's cached (not hardware) PS snapshot."""
import re
FIELDS=('mode','firmware_ps','rpwm','cpwm','rpwm_toggle','cpwm_toggle','leisure','idle_count','in_suspend','ips_processing','ps_processing','power_saving','deny','lps_deny_active','fw_ready','hw_ready','cmd_running','watchdog_timer','busy_traffic','low_power_count','mi_linked','mi_linking','group_key','scan_deny','cfg_pm')
BYTES={'mode','rpwm','cpwm','rpwm_toggle','cpwm_toggle','idle_count','mi_linked','mi_linking'}
WORDS={'deny','low_power_count'}
OPTIONAL={'scan_deny','cfg_pm'}

def parse(line):
    if line==' Cached PS: version=1 busy=1':return None
    prefix=' Cached PS: version=1 busy=0 '
    if not line.startswith(prefix):raise ValueError('exact numeric cached snapshot required')
    tokens=line[len(prefix):].split(' ')
    if len(tokens)!=len(FIELDS):raise ValueError('closed snapshot field inventory required')
    result={}
    for name,token in zip(FIELDS,tokens,strict=True):
        pattern='0x[0-9a-f]{8}' if name=='deny' else '[0-9]+'
        found=re.fullmatch(name+'=('+pattern+')',token)
        if not found:raise ValueError('known numeric snapshot field required')
        value=int(found[1],16 if name=='deny' else 10)
        limit=2**32-1 if name in WORDS else 255 if name in BYTES else 2 if name in OPTIONAL else 1
        if value>limit:raise ValueError('bounded cached field or feature enum required')
        result[name]=value
    return result


def only_snapshot_changed(before,after):
    """`after` is `before` plus the formatter and its one proc hook, nothing else."""
    from driver_source import function
    helper=function(after,'rtw_dump_cached_ps_state');start=after.index('/* Cached software state only.')
    end=after.index(helper,start)+len(helper)
    if after[end:end+2]!='\n\n':raise ValueError('closed new formatter block required')
    projected=after[:start]+after[end+2:];hook='\trtw_dump_cached_ps_state(m, padapter);\n'
    if projected.count(hook)!=1 or projected.replace(hook,'')!=before:raise ValueError('only new formatter and one proc hook may differ')
