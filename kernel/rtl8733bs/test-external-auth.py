#!/usr/bin/env python3
"""Test the actual RTL normalization function and real cfg80211/vendor layouts.

All data is generated. No module load, radio access, keys or owner state reads.
Optional SDK/qemu arguments repeat the same checks using the real MIPS o32 ABI.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PREFIX = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef uint8_t u8;
typedef uint16_t u16;
#define ETH_ALEN 6
#define IEEE80211_MAX_SSID_LEN 32
#define PMKID_LEN 16
#define __aligned(n) __attribute__((aligned(n)))
#define _rtw_memset memset
#define _rtw_memcpy memcpy
#define KERNEL_VERSION(a,b,c) (((a)<<16)|((b)<<8)|(c))
#ifdef OLD_ARRAY
#define LINUX_VERSION_CODE KERNEL_VERSION(4,19,0)
#else
#define LINUX_VERSION_CODE KERNEL_VERSION(7,3,0)
#endif
enum external_auth_action { EXTERNAL_AUTH_START, EXTERNAL_AUTH_ABORT };
enum nl80211_external_auth_action { NL80211_EXTERNAL_AUTH_START, NL80211_EXTERNAL_AUTH_ABORT };
'''
SUFFIX = r'''
int main(void) {
    struct cfg80211_external_auth_params source;
    struct { unsigned guard1; struct rtw_external_auth_params value; unsigned guard2; } target;
    u8 key[PMKID_LEN];
    memset(&source, 0xa5, sizeof(source));
    source.action=NL80211_EXTERNAL_AUTH_ABORT;
    for (unsigned i=0;i<ETH_ALEN;i++) source.bssid[i]=(u8)(i+7);
    for (unsigned i=0;i<32;i++) source.ssid.ssid[i]=(u8)('A'+i%20);
    source.ssid.ssid_len=32;
    source.key_mgmt_suite=0x01020304;
    source.status=77;
    for (unsigned i=0;i<PMKID_LEN;i++) key[i]=(u8)(0xf0-i);
#ifdef OLD_ARRAY
    memcpy(source.pmkid,key,sizeof key);
#else
    source.pmkid=key;
#endif
    memset(&target,0x5a,sizeof target);
    rtw_external_auth_from_cfg80211(&target.value,&source);
    assert(target.guard1==0x5a5a5a5a && target.guard2==0x5a5a5a5a);
    assert(target.value.action==EXTERNAL_AUTH_ABORT);
    assert(memcmp(target.value.bssid,source.bssid,ETH_ALEN)==0);
    assert(memcmp(&target.value.ssid,&source.ssid,sizeof source.ssid)==0);
    assert(target.value.key_mgmt_suite==source.key_mgmt_suite && target.value.status==77);
    assert(target.value.pmkid_valid==1 && memcmp(target.value.pmkid,key,sizeof key)==0);
#ifndef OLD_ARRAY
    /* The previous cast read array bytes starting at a different offset. */
    assert(offsetof(struct cfg80211_external_auth_params,pmkid)!=offsetof(struct rtw_external_auth_params,pmkid));
    source.pmkid=NULL;
    rtw_external_auth_from_cfg80211(&target.value,&source);
    assert(!target.value.pmkid_valid);
    for (unsigned i=0;i<PMKID_LEN;i++) assert(target.value.pmkid[i]==0);
    assert(target.value.status==77 && target.guard2==0x5a5a5a5a);
#endif
    printf("PASS: fields, 32-byte SSID, exact PMKID and NULL; pointer_bytes=%zu cfg_offset=%zu vendor_offset=%zu\n",
        sizeof(void*),offsetof(struct cfg80211_external_auth_params,pmkid),offsetof(struct rtw_external_auth_params,pmkid));
}
'''


def block(text, name):
    match = re.search(r'struct ' + name + r' \{.*?\n\};', text, re.S)
    if not match:
        raise RuntimeError('actual structure not found: ' + name)
    return match[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-source', type=Path,
                        help='production Linux source (python3 os/install/prepare.py --dest build/...)')
    parser.add_argument('--driver-source', type=Path,
                        help='driver tree with patch 0004 (default: prepared sae variant)')
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--qemu', type=Path)
    args = parser.parse_args()
    if bool(args.sdk) != bool(args.qemu):
        parser.error('--sdk and --qemu must be supplied together')
    if args.kernel_source is None:
        print('SKIP: needs --kernel-source, a production Linux source tree for include/net/cfg80211.h')
        return
    if args.driver_source is None:
        import driver_source
        args.driver_source = driver_source.tree('sae')
    linux = (args.kernel_source / 'include/net/cfg80211.h').read_text()
    vendor = (args.driver_source / 'os_dep/linux/ioctl_cfg80211.h').read_text()
    driver = (args.driver_source / 'os_dep/linux/ioctl_cfg80211.c').read_text()
    function = re.search(r'static void rtw_external_auth_from_cfg80211\(.*?\n\}', driver, re.S)
    if not function:
        raise RuntimeError('actual normalization function is missing')
    structures = '\n'.join([block(linux,'cfg80211_ssid'),
        block(linux,'cfg80211_external_auth_params'),block(vendor,'rtw_external_auth_params')])
    out = ROOT / 'build/rtl-external-auth-test'
    out.mkdir(parents=True, exist_ok=True)
    source = out / 'test.c'
    source.write_text(PREFIX + structures + '\n' + function[0] + '\n' + SUFFIX)
    compilers = [('native',['cc'],[])]
    if args.sdk:
        compilers.append(('mips',[str(args.sdk.resolve()/'bin/mipsel-buildroot-linux-gnu-gcc')],
            [str(args.qemu.resolve()),'-L',str(args.sdk.resolve()/'mipsel-buildroot-linux-gnu/sysroot')]))
    results = []
    for name, compiler, runner in compilers:
        for legacy in (False, True):
            fixture = source
            if legacy:
                fixture = out / 'old-array.c'
                fixture.write_text(source.read_text().replace('const u8 *pmkid;', 'u8 pmkid[PMKID_LEN];'))
            binary = out / (name + ('-old-array' if legacy else '-pointer'))
            subprocess.run(compiler + ['-std=c11','-Wall','-Wextra','-Werror'] +
                (['-DOLD_ARRAY'] if legacy else []) + [str(fixture),'-o',str(binary)],check=True)
            result = subprocess.run(runner + [str(binary)],text=True,capture_output=True,check=True)
            print(name, 'legacy-array fixture' if legacy else 'actual modern layout', result.stdout.strip())
            results.append({'target':name,'legacy_array_fixture':legacy,'passed':True,'stdout':result.stdout})
    (out/'result.json').write_text(json.dumps({'results':results,'hardware_qualified':False,
        'function_sha256':hashlib.sha256(function[0].encode()).hexdigest()},indent=2)+'\n')


if __name__ == '__main__':
    main()
