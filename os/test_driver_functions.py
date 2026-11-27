#!/usr/bin/env python3
"""Production Q2 driver functions, compiled on the host against stubs.

Each harness in os/driver-functions/ takes functions, tables and constants
verbatim from the production Linux tree, compiles them with the harness's
models of the kernel services they call (registers, I2C, regulators,
locks) and runs them over generated register states and faults. Each
harness also runs with mutated copies of the production code that must
fail: they show the checks detect the defects they target.

The source tree is the `linux_production` commit of os/sources.json, made
by os/install/prepare.py. A function, table or mutation site missing from
the tree fails the run: the driver changed and its harness must follow.
Without a tree every harness test skips and only the extractor tests run.
docs/kernel-faults.md lists every check.

  python3 os/install/prepare.py --dest build/os/fault-kernel-src
  python3 os/test_driver_functions.py --kernel-source build/os/fault-kernel-src [-v] [TEST...]

Q2_KERNEL_SOURCE names the tree when --kernel-source is absent. Generated
sources, binaries and output go to build/os/driver-functions/.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESSES = ROOT / 'os/driver-functions'
OUT = ROOT / 'build/os/driver-functions'
MARKER = '/* PRODUCTION SOURCE */'
CFLAGS = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
          '-Wno-unused-function', '-Wno-pointer-sign', '-Wno-sign-compare']
SANITIZERS = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
SOURCE = None
_COMPILER = []


def compiler():
    """$CC, cc or clang: the first that links a program with ASan and UBSan."""
    if not _COMPILER:
        OUT.mkdir(parents=True, exist_ok=True)
        probe = OUT / 'sanitizer-probe.c'
        probe.write_text('int main(void) { return 0; }\n')
        for candidate in dict.fromkeys(filter(None, (os.environ.get('CC'), 'cc', 'clang'))):
            if shutil.which(candidate) and not subprocess.run(
                    [candidate, *SANITIZERS, str(probe), '-o', str(OUT / 'sanitizer-probe')],
                    capture_output=True, timeout=60).returncode:
                _COMPILER.append(candidate)
                break
        else:
            _COMPILER.append(None)
    return _COMPILER[0]


class Missing(AssertionError):
    """The production tree lacks something a harness takes from it."""


def _close(text, start, opening, closing):
    """Index just past the bracket matching text[start] (an opening bracket)."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if not depth:
                return i + 1
    raise Missing(f'unbalanced {opening}{closing}')


def function(text, name, where='source'):
    """The one definition of function NAME, starting at its return type."""
    found = []
    for match in re.finditer(r'^[A-Za-z_][^;{}()\n]*\b' + re.escape(name) + r'\(', text, re.M):
        after = _close(text, match.end() - 1, '(', ')')
        rest = text[after:].lstrip()
        if rest.startswith('{'):
            body = after + (len(text[after:]) - len(rest))
            found.append(text[match.start():_close(text, body, '{', '}')])
    if len(found) != 1:
        raise Missing(f'{where}: {len(found)} definitions of {name}()')
    return found[0] + '\n'


def initialized(text, name, where='source'):
    """A definition `... NAME[] = {...};` or `... NAME = {...};`, or `struct NAME {...};`."""
    if name.startswith('struct '):
        pattern = r'^' + re.escape(name) + r' \{'
    else:
        pattern = r'^[A-Za-z_][^;{}=\n]*\b' + re.escape(name) + r'\b[^;{}=\n]*=\s*\{'
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) != 1:
        raise Missing(f'{where}: {len(matches)} definitions of {name}')
    match = matches[0]
    end = _close(text, match.end() - 1, '{', '}')
    if not text[end:].startswith(';'):
        raise Missing(f'{where}: {name} is not followed by ;')
    return text[match.start():end + 1] + '\n'


def defines(text, prefix, where='source'):
    """Every one-line #define whose name starts with PREFIX."""
    lines = [line for line in text.splitlines()
             if re.match(r'#define\s+' + re.escape(prefix) + r'\w*[\s(]', line) and not line.endswith('\\')]
    if not lines:
        raise Missing(f'{where}: no #define {prefix}*')
    return '\n'.join(lines) + '\n'


def define(text, name, where='source'):
    lines = [line for line in text.splitlines() if re.match(r'#define\s+' + re.escape(name) + r'[\s(]', line)]
    if len(lines) != 1:
        raise Missing(f'{where}: {len(lines)} definitions of #define {name}')
    return lines[0] + '\n'


def whole(text, name, where='source'):
    """The whole file without its #include lines; the harness declares what it uses."""
    return ''.join(line for line in text.splitlines(True) if not line.startswith('#include'))


EXTRACT = {'function': function, 'initialized': initialized, 'defines': defines,
           'define': define, 'whole': whole}

# Per harness: (file in the kernel tree, kind, name) in output order.
PARTS = {
    'axp2101-charge': [
        ('include/linux/regmap.h', 'initialized', 'struct regmap_range'),
        ('include/linux/regmap.h', 'initialized', 'struct regmap_access_table'),
        ('include/linux/regmap.h', 'define', 'regmap_reg_range'),
        ('include/linux/regmap.h', 'function', 'regmap_reg_in_range'),
        ('include/linux/mfd/axp20x.h', 'defines', 'AXP2101_'),
        ('drivers/mfd/axp20x.c', 'initialized', 'axp2101_writeable_ranges'),
        ('drivers/mfd/axp20x.c', 'initialized', 'axp2101_volatile_ranges'),
        ('drivers/mfd/axp20x.c', 'initialized', 'axp2101_writeable_table'),
        ('drivers/mfd/axp20x.c', 'initialized', 'axp2101_volatile_table'),
        ('drivers/base/regmap/regmap.c', 'function', 'regmap_reg_in_ranges'),
        ('drivers/base/regmap/regmap.c', 'function', 'regmap_check_range_table'),
        ('drivers/base/regmap/regmap.c', 'function', 'regmap_volatile'),
        ('drivers/base/regmap/regmap.c', 'function', '_regmap_map_get_context'),
        ('drivers/base/regmap/regcache.c', 'function', 'regcache_read'),
        ('drivers/base/regmap/regcache.c', 'function', 'regcache_write'),
        ('drivers/base/regmap/regmap.c', 'function', '_regmap_read'),
        ('drivers/base/regmap/regmap.c', 'function', '_regmap_write'),
        ('drivers/base/regmap/regmap.c', 'function', '_regmap_update_bits'),
        ('drivers/power/supply/axp20x_battery.c', 'defines', 'AXP2101_'),
        ('drivers/power/supply/axp20x_battery.c', 'initialized', 'struct axp20x_batt_ps'),
        ('drivers/power/supply/axp20x_battery.c', 'initialized', 'struct axp2101_faults'),
        *[('drivers/power/supply/axp20x_battery.c', 'function', name) for name in (
            'axp2101_ccc_selector', 'axp2101_read_faults', 'axp2101_health',
            'axp2101_get_charge_behaviour', 'axp2101_set_control_locked', 'axp2101_policy_init',
            'axp2101_policy_ready', 'axp2101_policy_advance_locked', 'axp2101_battery_set_prop',
            'axp2101_policy_set', 'axp2101_battery_prop_writeable')],
    ],
    'axp2101-powerkey': [
        ('include/linux/mfd/axp20x.h', 'initialized', 'struct axp20x_dev'),
        ('include/linux/mfd/axp20x.h', 'define', 'AXP2101_IRQ_LEVEL_OFF_ON'),
        ('drivers/mfd/axp20x.c', 'function', 'axp2101_q2_powerkey_init'),
    ],
    'wheel': [
        ('drivers/input/misc/shanling-q2-wheel.c', 'initialized', 'struct q2_wheel'),
        *[('drivers/input/misc/shanling-q2-wheel.c', 'function', name) for name in (
            'q2_wheel_read_reg', 'q2_wheel_normal_mode', 'q2_wheel_release', 'q2_wheel_irq')],
    ],
    'wifi-pwrseq': [
        ('drivers/mmc/core/pwrseq_q2.c', 'whole', 'pwrseq_q2.c'),
    ],
    'backlight': [
        ('drivers/video/backlight/pwm_bl.c', 'function', 'compute_duty_cycle'),
        ('drivers/pwm/pwm-ingenic-x2000.c', 'defines', 'PWM_'),
        ('drivers/pwm/pwm-ingenic-x2000.c', 'initialized', 'struct x2000_pwm'),
        *[('drivers/pwm/pwm-ingenic-x2000.c', 'function', name) for name in (
            'x2000_pwm_update', 'x2000_pwm_clk_on', 'x2000_pwm_clk_get', 'x2000_pwm_clk_put',
            'x2000_pwm_set_running', 'x2000_pwm_dma_wait_stopped', 'x2000_pwm_program',
            'x2000_pwm_apply', 'x2000_pwm_get_state')],
    ],
}

# Mutations of the extracted production code, each with the assertion text
# its failure must print. The original must occur exactly once.
MUTATIONS = {
    'axp2101-charge': {
        'charge enable on the wrong bit': (
            'AXP2101_CELL_CHARGE_ENABLE\tBIT(1)', 'AXP2101_CELL_CHARGE_ENABLE\tBIT(2)', 'check_behaviour'),
        'auto accepted with a fault': (
            'if (axp2101_health(&faults) != POWER_SUPPLY_HEALTH_GOOD)\n\t\t\treturn -EPERM;',
            'if (axp2101_health(&faults) != POWER_SUPPLY_HEALTH_GOOD)\n\t\t\t(void)0;', 'check_faults'),
        'manual write keeps policy authority': (
            '\taxp2101_policy_advance_locked(batt);\n\tret = axp2101_set_control_locked',
            '\tret = axp2101_set_control_locked', 'check_behaviour'),
        'charge enable register cached': (
            'regmap_reg_range(AXP2101_MODULE_EN, AXP2101_MODULE_EN),', '', 'check_bus_faults'),
        'stale policy generation accepted': (
            'if (epoch != batt->policy_epoch || generation != batt->policy_generation) {',
            'if (epoch != batt->policy_epoch) {', 'check_policy'),
        'charge current truncated': (
            'return ua % AXP2101_CURRENT_STEP ? -EINVAL : ua / AXP2101_CURRENT_STEP;',
            'return ua / AXP2101_CURRENT_STEP;', 'check_current'),
    },
    'axp2101-powerkey': {
        'readback through the cache': (
            'regmap_read_bypassed(axp20x->regmap', 'regmap_read(axp20x->regmap', 'cached'),
        'reserved bits overwritten': ('GENMASK(5, 0), 0x1d', 'GENMASK(7, 0), 0x1d', 'registers[0x27]'),
    },
    'wheel': {
        'no retry of a failed read': ('attempt < 2', 'attempt < 1', '== 42'),
        'register pointer not held': (
            '\ti2c_lock_bus(client->adapter, I2C_LOCK_SEGMENT);\n', '\tlocked = 1;\n', 'lock_calls'),
        'out-of-range position reported': ('if (pos > 200) {', 'if (pos > 255) {', 'value <= 200'),
    },
    'wifi-pwrseq': {
        'published without the supplies': (
            'ret = devm_regulator_bulk_get_enable(dev, ARRAY_SIZE(supplies), supplies);',
            'ret = 0; (void)devm_regulator_bulk_get_enable;', 'acquired'),
        'missing supply property accepted': (
            'return dev_err_probe(dev, -EINVAL, "missing %s\\n", property);', '(void)0;', '-EINVAL'),
    },
    'backlight': {
        'zero low phase at full duty': (
            '\tif (duty >= period) {\n\t\tif (period < 2)\n\t\t\treturn -ERANGE;\n\t\tduty = period - 1;\n\t}\n',
            '', 'two_phases'),
        'other channels\' prescalers cleared': (
            'x2000_pwm_update(p, PWM_CCFG(n), 0xf << shift, scale << shift);',
            'writel(scale << shift, p->base + PWM_CCFG(n));', 'CCFG'),
    },
}

# x2000_pwm_program writes `0xf << shift`, a signed shift into bit 31 for
# channels 7 and 15. GCC defines it, as the kernel's build relies on; UBSan's
# shift-base check would stop at it before any assertion.
EXTRA_CFLAGS = {'backlight': ['-fno-sanitize=shift-base']}

DTS = 'arch/mips/boot/dts/ingenic/shanling-q2.dts'


def backlight_levels(source):
    """brightness-levels, default level and pwms of the production DT's backlight node."""
    text = (source / DTS).read_text()
    node = re.search(r'\n\tbacklight: backlight \{\n(.*?)\n\t\};', text, re.S)
    if not node:
        raise Missing(f'{DTS}: no backlight node')
    body = node.group(1)
    def cells(name):
        match = re.search(r'\b' + re.escape(name) + r' = <([^>]*)>;', body)
        if not match:
            raise Missing(f'{DTS}: backlight has no {name}')
        return match.group(1).split()
    if 'compatible = "pwm-backlight";' not in body:
        raise Missing(f'{DTS}: backlight is not pwm-backlight')
    return ([int(n) for n in cells('brightness-levels')], [int(n) for n in cells('default-brightness-level')],
            cells('pwms'))


def generate(name, source, mutation=None):
    """The harness's C text with the production parts in place."""
    parts = []
    for path, kind, item in PARTS[name]:
        file = source / path
        if not file.is_file():
            raise Missing(f'{path} is not in the kernel source')
        parts.append(f'/* {path}: {item} */\n' + EXTRACT[kind](file.read_text(), item, path))
    production = '\n'.join(parts)
    if name == 'backlight':
        levels, default, pwms = backlight_levels(source)
        production += (f'\nstatic const unsigned int dt_levels[] = {{{", ".join(map(str, levels))}}};\n'
                       f'static const unsigned int dt_default = {default[0]};\n'
                       f'static const unsigned long long dt_period = {int(pwms[2])};\n'
                       f'static const unsigned int dt_channel = {int(pwms[1])};\n')
    if mutation:
        original, replacement, _ = MUTATIONS[name][mutation]
        count = production.count(original)
        if count != 1:
            raise Missing(f'{name}: mutation site for {mutation!r} occurs {count} times')
        production = production.replace(original, replacement)
    harness = (HARNESSES / f'{name}.c').read_text()
    if harness.count(MARKER) != 1:
        raise ValueError(f'{name}.c needs one {MARKER} line')
    return harness.replace(MARKER, production)


def build_and_run(name, source, mutation=None):
    label = name + ('' if mutation is None else '-' + re.sub(r'[^a-z0-9]+', '-', mutation.lower()).strip('-'))
    out = OUT / label
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / 'check.c').write_text(generate(name, source, mutation))
    compiled = subprocess.run([compiler(), *CFLAGS, *SANITIZERS, *EXTRA_CFLAGS.get(name, ()), str(out / 'check.c'),
                               '-o', str(out / 'check')], capture_output=True, text=True, timeout=120)
    (out / 'compile.log').write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        raise AssertionError(f'{label} did not compile:\n{compiled.stderr[-3000:]}')
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:abort_on_error=0',
               UBSAN_OPTIONS='print_stacktrace=1:halt_on_error=1')
    result = subprocess.run([str(out / 'check')], capture_output=True, text=True, timeout=300, env=env, cwd=out)
    (out / 'run.log').write_text(result.stdout + result.stderr)
    return result


class Extract(unittest.TestCase):
    """The extractor on generated text; needs no kernel tree."""
    text = ('static const int table[] = {\n\t1, 2,\n};\n'
            'static int helper(int a);\n'
            'static int helper(int a)\n{\n\tif (a) {\n\t\treturn 1;\n\t}\n\treturn 0;\n}\n'
            'struct thing {\n\tint a;\n};\n'
            '#define Q2_A 1\n#define Q2_B(x) ((x) + 1)\n#define Q2_LONG \\\n\t2\n#define OTHER 3\n')

    def test_definition_not_prototype(self):
        self.assertEqual(function(self.text, 'helper'),
                         'static int helper(int a)\n{\n\tif (a) {\n\t\treturn 1;\n\t}\n\treturn 0;\n}\n')

    def test_missing_or_duplicate_definition_fails(self):
        with self.assertRaisesRegex(Missing, '0 definitions of absent'):
            function(self.text, 'absent')
        with self.assertRaisesRegex(Missing, '2 definitions'):
            function(self.text + self.text, 'helper')

    def test_tables_structs_and_defines(self):
        self.assertEqual(initialized(self.text, 'table'), 'static const int table[] = {\n\t1, 2,\n};\n')
        self.assertEqual(initialized(self.text, 'struct thing'), 'struct thing {\n\tint a;\n};\n')
        self.assertEqual(defines(self.text, 'Q2_'), '#define Q2_A 1\n#define Q2_B(x) ((x) + 1)\n')
        with self.assertRaises(Missing):
            defines(self.text, 'NONE_')

    def test_tree_without_the_driver_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tree:
            for name in PARTS:
                with self.assertRaisesRegex(Missing, 'is not in the kernel source'):
                    generate(name, Path(tree))

    def test_every_harness_has_parts_mutations_and_one_marker(self):
        self.assertEqual(set(PARTS), set(MUTATIONS))
        for name in PARTS:
            self.assertEqual((HARNESSES / f'{name}.c').read_text().count(MARKER), 1, name)
            self.assertTrue(MUTATIONS[name], name)


class Drivers(unittest.TestCase):
    """Each harness passes on the production tree and fails on each mutation."""

    def setUp(self):
        if SOURCE is None:
            self.skipTest('no kernel source: give --kernel-source or Q2_KERNEL_SOURCE '
                          '(python3 os/install/prepare.py --dest build/os/fault-kernel-src)')
        if compiler() is None:
            self.skipTest('no C compiler here links ASan and UBSan (set CC, or install libasan and libubsan)')

    def check(self, name):
        result = build_and_run(name, SOURCE)
        self.assertEqual(result.returncode, 0, f'{name}:\n{result.stdout}{result.stderr[-3000:]}')
        self.assertIn('PASS', result.stdout)
        for mutation, (_, _, expected) in MUTATIONS[name].items():
            with self.subTest(mutation=mutation):
                failed = build_and_run(name, SOURCE, mutation)
                self.assertNotEqual(failed.returncode, 0, f'{name} passed with {mutation!r}')
                self.assertIn(expected, failed.stderr, f'{name}, {mutation!r}:\n{failed.stderr[-3000:]}')

    def test_axp2101_charge_control(self):
        self.check('axp2101-charge')

    def test_axp2101_power_key_timing(self):
        self.check('axp2101-powerkey')

    def test_wheel_protocol(self):
        self.check('wheel')

    def test_wifi_power_prerequisites(self):
        self.check('wifi-pwrseq')

    def test_backlight_levels_and_full_duty(self):
        self.check('backlight')


def main():
    global SOURCE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kernel-source', type=Path, help='production Linux tree (default: Q2_KERNEL_SOURCE)')
    args, rest = parser.parse_known_args()
    tree = args.kernel_source or (Path(os.environ['Q2_KERNEL_SOURCE']) if os.environ.get('Q2_KERNEL_SOURCE') else None)
    if tree is not None:
        tree = tree.resolve()
        if not (tree / 'Makefile').is_file() or not (tree / 'drivers').is_dir():
            parser.error(f'{tree} is not a Linux source tree')
        SOURCE = tree
    unittest.main(argv=[sys.argv[0], *rest])


if __name__ == '__main__':
    main()
