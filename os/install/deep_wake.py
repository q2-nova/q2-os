"""Time a real generated player's Power wake in the model's virtual clock."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import re
from threading import Event


def present_interval(edge, anchor, uptime, metric_us):
    """Map post-wake CLOCK_BOOTTIME into virtual time using a UART bracket.

    /proc/uptime truncates to centiseconds. Both guest readings are taken
    after resume so the retained interval's RTC injection cancels out.
    """
    guest_ns = int(Decimal(uptime) * 10**9)
    present_ns = int(metric_us) * 1000
    if not (edge[0] <= edge[1] <= anchor[0] <= anchor[1]):
        raise ValueError('Power edge and clock anchor out of order')
    if not 0 <= guest_ns + 10_000_000 - present_ns <= anchor[1] - edge[0] + 10_000_000:
        raise ValueError('resume metric outside this Power wake')
    return [(anchor[0] + present_ns - guest_ns - 10_000_000 - edge[1]) / 1e6,
            (anchor[1] + present_ns - guest_ns - edge[0]) / 1e6]


def precise_present(edge, anchors):
    """Intersect eight post-return UART brackets in the guest BOOTTIME domain."""
    if len(anchors) != 8:
        raise ValueError('eight wake clock anchors required')
    metric = anchors[0]['metric_us']
    offset_low = max(a['open_ns'] - a['guest_ns'] for a in anchors)
    offset_high = min(a['close_ns'] - a['guest_ns'] for a in anchors)
    previous = metric * 1000
    for a in anchors:
        if (a['metric_us'] != metric or a['guest_ns'] <= previous or
                not edge[1] <= a['open_ns'] <= a['close_ns']):
            raise ValueError('inconsistent high-resolution wake clock')
        previous = a['guest_ns']
    low = metric * 1000 + offset_low - edge[1]
    high = metric * 1000 + 1000 + offset_high - edge[0]
    if offset_high < offset_low or low < 0:
        raise ValueError('no common wake clock interval')
    return [low / 1e6, high / 1e6]


def observe(control, socket, stop, console, path):
    pressed = False
    first = edge = last_dark = lit_window = dpu_at_edge = None
    dsi_at_edge = dsi_at_light = None
    anchor_start = None
    text = b''
    ns_open = {}
    anchors = []
    previous_read = None
    with path.open('x') as log, console.open('rb') as serial:
        serial.seek(0, 2)
        previous = None
        try:
            while not stop.is_set():
                clock = control(socket, 'clock-json')
                power = control(socket, 'power-json')
                state = {key: power['components'][key]['state']
                         for key in ('cpu0', 'cpu1', 'panel', 'backlight')}
                t = power['time_ns']
                if not clock['time_ns'] <= t or previous is not None and clock['time_ns'] < previous:
                    raise ValueError('model clock discontinuity')
                previous = t
                ddr = clock['ddr_controller']
                asleep = (ddr['self_refresh'] and ddr['phy_pll_down'] and not ddr['available']
                          and state == dict(cpu0='wait', cpu1='off', panel='off', backlight='off'))
                if edge is None:
                    if not asleep:
                        first = None
                    elif first is None:
                        first = t
                    if first is not None and t - first >= 1_000_000_000:
                        dpu_at_edge = control(socket, 'dpu')
                        dsi_at_edge = control(socket, 'dsi')
                        key_before = control(socket, 'clock-json')['time_ns']
                        control(socket, 'key power down')
                        pressed = True
                        edge = [key_before, control(socket, 'clock-json')['time_ns']]
                        if not t <= edge[0] <= edge[1]:
                            raise ValueError('Power key clock discontinuity')
                if pressed and t - edge[1] >= 40_000_000:
                    control(socket, 'key power up')
                    pressed = False
                lit = state['panel'] == 'on' and state['backlight'] == 'on'
                entry = dict(time_ns=t, state=state, retained=asleep, power_edge_ns=edge,
                             panel_state_ns=power['components']['panel']['state_ns'])
                if edge is not None and lit_window is None:
                    if not lit:
                        last_dark = t
                    else:
                        dpu = control(socket, 'dpu')
                        if dpu['running'] and dpu['frames'] > dpu_at_edge['frames'] and dpu['errors'] == dpu_at_edge['errors']:
                            end = control(socket, 'clock-json')['time_ns']
                            lit_window = [max(0, last_dark - edge[1]) / 1e6, (end - edge[0]) / 1e6]
                            dsi_at_light = control(socket, 'dsi')
                            entry.update(lit_wake_interval_ms=lit_window, dpu=dpu)
                log.write(json.dumps(entry) + '\n')
                log.flush()
                if edge is not None:
                    text = (text + serial.read())[-65536:]
                    if previous_read is not None:
                        for number in re.findall(rb'Q2_WAKE_NS_OPEN (\d+)\r?\n', text):
                            ns_open.setdefault(int(number), previous_read)
                        completed = re.findall(rb'Q2_WAKE_NS_CLOSE (\d+) (\d+) (\d+)\r?\n', text)
                        if len(completed) > len(anchors):
                            close = control(socket, 'clock-json')['time_ns']
                            for seq, stamp, metric in completed[len(anchors):]:
                                i = len(anchors)
                                if int(seq) != i or i not in ns_open or i >= 8:
                                    raise ValueError('invalid wake clock marker sequence')
                                anchors.append(dict(open_ns=ns_open[i], close_ns=close,
                                                    guest_ns=int(stamp), metric_us=int(metric)))
                    previous_read = t
                    if len(anchors) == 8 and lit_window and not pressed:
                        present = precise_present(edge, anchors)
                        return dict(edge_ns=edge, lit_ms=lit_window, clock_anchors=anchors,
                                    dsi_at_edge=dsi_at_edge, dsi_at_light=dsi_at_light,
                                    present_ms=present, usable_ms=list(map(max, zip(present, lit_window))))
                    if b'Q2_WAKE_CLOCK_OPEN' not in text:
                        anchor_start = t
                    match = re.search(rb'Q2_WAKE_CLOCK_CLOSE (\d+\.\d{2}) (\d+)\r?\n', text)
                    if match and anchor_start is not None and lit_window and not pressed:
                        anchor = [anchor_start, control(socket, 'clock-json')['time_ns']]
                        present = present_interval(edge, anchor, match[1].decode(), match[2])
                        return dict(edge_ns=edge, lit_ms=lit_window, anchor_ns=anchor,
                                    dsi_at_edge=dsi_at_edge, dsi_at_light=dsi_at_light,
                                    present_ms=present, usable_ms=list(map(max, zip(present, lit_window))))
                if not edge:
                    stop.wait(.05)
                else:
                    stop.wait(.001 if state['panel'] == 'on' else .012)
        finally:
            if pressed:
                control(socket, 'key power up')
    raise RuntimeError('guest ended before retained Power wake, lit scanout and fresh presentation')


@contextmanager
def measure(control, out, row):
    stop = Event()
    path = out / ('deep-wake-' + row['id'] + '.jsonl')
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(observe, control, out / 'ctl.sock', stop,
                             out / f"console-{row['cycle']}.log", path)
        try:
            yield
        finally:
            stop.set()
            worker_done = worker.exception()
            row['deep_wake_trace'] = dict(path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        if worker_done:
            raise worker_done
        row['deep_wake'] = worker.result()
