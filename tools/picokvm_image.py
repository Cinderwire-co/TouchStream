"""Luckfox's PicoKVM microSD images: what's in them, taking a partition out, and making the spoke's
image from theirs.

    python3 tools/picokvm_image.py layout IMAGE                  the partitions, and where they are
    python3 tools/picokvm_image.py extract IMAGE NAME OUT        one partition (system_a, say) into OUT
    python3 tools/picokvm_image.py build IMAGE SPOKE OUT [--capture HELPER] [--ssh-key KEY.pub] [--xz]
                                                                 the spoke's image (SPOKE: its ARMv7 binary;
                                                                 HELPER: touchstream-capture, for HDMI; KEY:
                                                                 a developer's SSH key, never in a release)

The image has no MBR or GPT: its partitions are in the U-Boot environment at its start (32 KiB, with
a CRC), as blkdevparts=mmcblk1:32K(env),512K@32K(idblock),... The spoke's image is theirs with:

- the spoke in its system partitions (both: A and B), started by Luckfox's launcher (RkLunch.sh) in
  place of their apps, once the drivers and the network are up
- the box's screen kept for the spoke (fbcon=map:1: the kernel's console, and its logo, stay off
  it), with TouchStream's splash from the first moment of startup (an init script, S00)
- no way in with Luckfox's root password (it's published): no telnet, and SSH by key only. A
  developer's image (--ssh-key) has their key in /userdata/openssh/.ssh/authorized_keys, where
  Luckfox's sshd looks; a release has none
- 96K free after the security partition, holding the installer's settings block at 2880K (a 64 KiB
  boundary in the first 64 MiB, where the installer looks); the partitions after it move down to
  make room, and keep their numbers, which Luckfox's scripts use
- the HDMI chip's reference clock at 27 MHz in the device tree in both boot partitions (Luckfox's
  26 MHz makes its driver set the chip's audio clock up for 42 MHz: the sound came out 1.58 times
  too fast); the boot partitions are FIT images, checked by SHA-256 (not signed), so the hash is
  updated too

Editing the system partitions needs debugfs (e2fsprogs); without it here, it runs in Docker.
See spoke/docs/picokvm.md.

Copyright (c) 2025 Will Reeves and TouchStream Contributors
Licensed under the MIT License - see LICENSE file for details
"""
import hashlib
import lzma
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

ENV_SIZE = 32 << 10
UNITS = {'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30}
MAGIC = b'TOUCHSTREAM-PROVISION-V1\n'   # as installer/internal/image and spoke/internal/provision
BLOCK_AT, BLOCK_SIZE = 2880 << 10, 64 << 10
GAP = 96 << 10                          # after security: boot_a moves from 2848K to 2944K

# Luckfox's launcher starts their apps once the drivers and network are up; the spoke goes there
LAUNCH = re.compile(r'\n\s*chmod \+x /userdata/picokvm/bin/kvm_app\n\s*/userdata/picokvm/bin/kvm_app [^\n]*&\n')
START = """#!/bin/sh
# TouchStream: started by RkLunch.sh in place of Luckfox's apps, once the drivers and the network
# are up. It starts the spoke again if it ever stops; its log is in syslog (logread).
data=/userdata/touchstream
media=$(sed -n 's/.*storagemedia=\\([a-z]*\\).*/\\1/p' /proc/cmdline)
# Started from the installer's card: the spoke goes onto the box itself (its eMMC), and the box
# starts again from there ($data/stay-on-card keeps it on the card, for trying things out)
if [ "$media" = sd ] && [ ! -f "$data/stay-on-card" ]; then
    mkdir -p "$data"
    /usr/bin/touchstream-spoke install -data "$data" > /tmp/install.log 2>&1
    done=$?
    logger -t touchstream < /tmp/install.log
    if [ $done = 0 ]; then
        sync
        reboot
        exit 0
    fi
fi
# The data partition runs to the end of its disk: its filesystem grows to fill it, once
if [ ! -f "$data/.grown" ]; then
    resize2fs /dev/block/by-name/userdata && mkdir -p "$data" && touch "$data/.grown"
fi
# Started from the eMMC: recordings go on the card (the installer's is made one for recordings first)
if [ "$media" = emmc ] && [ -b /dev/mmcblk1 ]; then
    /usr/bin/touchstream-spoke card -data "$data" 2>&1 | logger -t touchstream
    mkdir -p /mnt/card "$data/recordings"
    if mount -t exfat /dev/mmcblk1p1 /mnt/card 2>/dev/null || mount -t vfat /dev/mmcblk1p1 /mnt/card 2>/dev/null; then
        mkdir -p /mnt/card/TouchStream && mount --bind /mnt/card/TouchStream "$data/recordings"
    fi
fi
# $data/screen-web serves the screen on the network, to try things out (anyone on it could tap it)
web=""
[ -f "$data/screen-web" ] && web="-screen-web"
while true; do
    /usr/bin/touchstream-spoke run -data "$data" $web 2>&1 | logger -t touchstream
    sleep 2
done
"""
SPLASH = """#!/bin/sh
# TouchStream: its splash on the box's screen from the start, until the spoke's up and draws its own
case "$1" in
    start) /usr/bin/touchstream-spoke splash ;;
esac
"""
CONSOLE_OFF = 'fbcon=map:1'   # the kernel's console (and its logo) on a screen there isn't: the screen is the spoke's


def read_env(f):
    """The U-Boot environment's data (after its CRC), checked."""
    f.seek(0)
    env = f.read(ENV_SIZE)
    if zlib.crc32(env[4:]) != struct.unpack('<I', env[:4])[0]:
        raise ValueError("the image's environment (its partition table) is damaged")
    return env[4:]


def variables(env):
    return dict(kv.decode().split('=', 1) for kv in env.split(b'\0') if b'=' in kv)


def parse(spec, image_size):
    """(name, offset, size) for each partition of a blkdevparts spec; '-' runs to the image's end."""
    out, at = [], 0
    for part in spec.split(':', 1)[1].split(','):
        size, offset, name = re.fullmatch(r'(-|\d+[KMG])(?:@(\d+[KMG]))?\((\w+)\)', part).groups()
        if offset:
            at = int(offset[:-1]) * UNITS[offset[-1]]
        n = image_size - at if size == '-' else int(size[:-1]) * UNITS[size[-1]]
        out.append((name, at, n))
        at += n
    return out


def partitions(path):
    with open(path, 'rb') as f:
        return parse(variables(read_env(f))['blkdevparts'], os.path.getsize(path))


def copy(src, at, size, dst, to):
    """size bytes from src at `at` into dst at `to` (short if src ends first)."""
    src.seek(at)
    dst.seek(to)
    while size:
        chunk = src.read(min(size, 8 << 20))
        if not chunk:
            break
        dst.write(chunk)
        size -= len(chunk)


def extract(path, name, out):
    _, at, size = next(p for p in partitions(path) if p[0] == name)
    with open(path, 'rb') as f, open(out + '.part', 'wb') as o:
        copy(f, at, size, o, 0)
    os.replace(out + '.part', out)


def debugfs(image, commands, writable=True):
    """Run debugfs commands on an ext4 image (here if it's installed, else in Docker), then check
    the filesystem. {w} in a command is the image's folder, as debugfs sees it."""
    folder = os.path.dirname(os.path.abspath(image))
    script = os.path.join(folder, 'debugfs.cmds')
    local = shutil.which('debugfs') or shutil.which('/opt/homebrew/opt/e2fsprogs/sbin/debugfs')
    with open(script, 'w') as f:
        f.write('\n'.join(c.replace('{w}', folder if local else '/w') for c in commands) + '\n')
    flags = ['-w'] if writable else []
    try:
        if local:
            run = ['sh', '-c', f'"{local}" {" ".join(flags)} -f "{script}" "{image}" && e2fsck -fn "{image}" >/dev/null']
        else:
            run = ['docker', 'run', '--rm', '--platform', 'linux/arm64', '-v', f'{folder}:/w', 'alpine:3.22', 'sh', '-c',
                   'apk add -q e2fsprogs-extra >/dev/null && debugfs ' + ' '.join(flags) +
                   f' -f /w/debugfs.cmds /w/{os.path.basename(image)} && e2fsck -fn /w/{os.path.basename(image)} >/dev/null']
        result = subprocess.run(run, capture_output=True, text=True)
        # debugfs carries on past a failed command, saying so on stderr (after its banner)
        problems = [line for line in result.stderr.splitlines() if line.strip() and not line.startswith(('debugfs ', 'e2fsck '))]
        if result.returncode or problems:
            raise RuntimeError(f'debugfs: {(chr(10).join(problems) or result.stderr).strip()[-600:]}')
        return result.stdout
    finally:
        os.remove(script)


def fdt_props(tree):
    """(node path, property name, offset, size) for each property of a flattened device tree."""
    magic, _, structs, strings = struct.unpack_from('>IIII', tree)
    if magic != 0xD00DFEED:
        raise ValueError('not a device tree')
    path, at = [], structs
    while True:
        token, = struct.unpack_from('>I', tree, at)
        at += 4
        if token == 1:                                   # a node begins: its name
            end = tree.index(b'\0', at)
            path.append(tree[at:end].decode())
            at = (end + 4) & ~3
        elif token == 2:                                 # it ends
            path.pop()
        elif token == 3:                                 # a property: its size, and its name's place
            size, name = struct.unpack_from('>II', tree, at)
            at += 8
            yield '/'.join(path) or '/', tree[strings + name:tree.index(b'\0', strings + name)].decode(), at, size
            at = (at + size + 3) & ~3
        elif token == 9:
            return
        elif token != 4:
            raise ValueError(f'damaged device tree (token {token})')


def cells(data):
    return list(struct.unpack(f'>{len(data) // 4}I', data))


def hdmi_refclk_27mhz(boot):
    """A boot partition (a FIT image: kernel, device tree, resource), with the HDMI chip's reference
    clock at 27 MHz: what its driver sets the chip's audio clock up for (at Luckfox's 26 MHz, the
    sound came out 1.58 times too fast). The FIT checks its parts by SHA-256, so that's updated."""
    boot = bytearray(boot)
    fit = {(node, name): (at, size) for node, name, at, size in fdt_props(boot)}
    if ('/configurations/conf/signature', 'value') in fit:
        raise ValueError('the boot partition is signed: its device tree can\'t be changed')
    get = lambda key: bytes(boot[fit[key][0]:fit[key][0] + fit[key][1]])
    start, size = cells(get(('/images/fdt', 'data-position')))[0], cells(get(('/images/fdt', 'data-size')))[0]
    if get(('/images/fdt/hash', 'algo')) != b'sha256\0':
        raise ValueError("the boot partition's device tree isn't checked by SHA-256")
    tree = bytearray(boot[start:start + size])
    props = {(node, name): (at, n) for node, name, at, n in fdt_props(tree)}
    value = lambda key: bytes(tree[props[key][0]:props[key][0] + props[key][1]])
    chip = next(node for (node, name) in props if name == 'compatible' and value((node, name)) == b'toshiba,tc358743\0')
    clock = cells(value((chip, 'clocks')))[:2]           # <the clock controller, its clock>
    for (node, name) in props:
        if name == 'assigned-clocks':
            pairs = cells(value((node, name)))
            for i in range(0, len(pairs), 2):
                if pairs[i:i + 2] == clock:
                    at = props[(node, 'assigned-clock-rates')][0] + 4 * (i // 2)
                    rate, = struct.unpack_from('>I', tree, at)
                    if rate not in (26_000_000, 27_000_000):
                        raise ValueError(f"the HDMI chip's reference clock is {rate} Hz: check it by hand")
                    struct.pack_into('>I', tree, at, 27_000_000)
                    boot[start:start + size] = tree
                    hash_at = fit[('/images/fdt/hash', 'value')][0]
                    boot[hash_at:hash_at + 32] = hashlib.sha256(tree).digest()
                    return bytes(boot)
    raise ValueError("the device tree doesn't set the HDMI chip's reference clock: check it by hand")


# sshd: keys only, none of them root's password (Luckfox's is published)
SSHD_KEYS_ONLY = {'PermitRootLogin': 'prohibit-password', 'PasswordAuthentication': 'no', 'KbdInteractiveAuthentication': 'no'}


def harden(system, work):
    """No way into a system partition with a password: telnet's gone, and sshd takes keys only."""
    config = os.path.join(work, 'sshd_config')
    debugfs(system, [f'dump /etc/ssh/sshd_config {{w}}/sshd_config'], writable=False)
    with open(config) as f:
        lines = f.read().splitlines()
    keep = [line for line in lines if line.split()[:1] and line.split()[0] not in SSHD_KEYS_ONLY or not line.split()]
    keep += [f'{k} {v}' for k, v in SSHD_KEYS_ONLY.items()]
    with open(config, 'w') as f:
        f.write('\n'.join(keep) + '\n')
    debugfs(system, ['rm /etc/ssh/sshd_config', 'write {w}/sshd_config /etc/ssh/sshd_config',
                     'set_inode_field /etc/ssh/sshd_config mode 0100644', 'rm /etc/init.d/S50telnet'])


def add_key(userdata, key, work):
    """A developer's SSH key on the data partition, where Luckfox's sshd looks (never in a release)."""
    shutil.copy(key, os.path.join(work, 'authorized_keys'))
    debugfs(userdata, ['mkdir /openssh', 'mkdir /openssh/.ssh', 'write {w}/authorized_keys /openssh/.ssh/authorized_keys',
                       'set_inode_field /openssh mode 040700', 'set_inode_field /openssh/.ssh mode 040700',
                       'set_inode_field /openssh/.ssh/authorized_keys mode 0100600',
                       'set_inode_field /openssh uid 0', 'set_inode_field /openssh/.ssh uid 0',
                       'set_inode_field /openssh/.ssh/authorized_keys uid 0'])


def add_spoke(system, spoke, work, capture=None):
    """Put the spoke (and its capture helper) in a system partition (an ext4 image), started in place
    of Luckfox's apps."""
    lunch = os.path.join(work, 'RkLunch.sh')
    debugfs(system, ['dump /oem/usr/bin/RkLunch.sh {w}/RkLunch.sh'], writable=False)
    with open(lunch) as f:
        script = f.read()
    if not LAUNCH.search(script):
        raise ValueError("Luckfox's launcher (RkLunch.sh) doesn't start their apps where it did: check it by hand")
    with open(lunch, 'w') as f:
        f.write(LAUNCH.sub('\n\t/usr/bin/touchstream-start &   # TouchStream, in place of Luckfox\'s apps\n', script, 1))
    for name, script in (('touchstream-start', START), ('S00touchstream-splash', SPLASH)):
        with open(os.path.join(work, name), 'w') as f:
            f.write(script)
    shutil.copy(spoke, os.path.join(work, 'touchstream-spoke'))
    commands = ['rm /oem/usr/bin/RkLunch.sh', 'write {w}/RkLunch.sh /oem/usr/bin/RkLunch.sh',
                'write {w}/touchstream-spoke /usr/bin/touchstream-spoke', 'write {w}/touchstream-start /usr/bin/touchstream-start',
                'write {w}/S00touchstream-splash /etc/init.d/S00touchstream-splash']
    files = ['/oem/usr/bin/RkLunch.sh', '/usr/bin/touchstream-spoke', '/usr/bin/touchstream-start',
             '/etc/init.d/S00touchstream-splash']
    if capture:
        shutil.copy(capture, os.path.join(work, 'touchstream-capture'))
        commands.append('write {w}/touchstream-capture /usr/bin/touchstream-capture')
        files.append('/usr/bin/touchstream-capture')
    for dest in files:
        commands += [f'set_inode_field {dest} mode 0100755', f'set_inode_field {dest} uid 0', f'set_inode_field {dest} gid 0']
    debugfs(system, commands)


def build(source, spoke, out, compress=False, capture=None, ssh_key=None):
    with open(source, 'rb') as f:
        env = read_env(f)
    spec = variables(env)['blkdevparts']
    parts = parse(spec, os.path.getsize(source))
    names = [p[0] for p in parts]
    security = parts[names.index('security')]
    if security[1] + security[2] > BLOCK_AT or parts[names.index('security') + 1][0] != 'boot_a':
        raise ValueError("the partitions aren't where the spoke's image expects them")
    # boot_a, and everything after it, moves down 96K; the gap holds the settings block
    boot_at = security[1] + security[2] + GAP
    new_spec = re.sub(r'(?<=,)(\d+[KMG])\(boot_a\)', lambda m: f'{m.group(1)}@{boot_at >> 10}K(boot_a)', spec, count=1)
    if new_spec == spec:
        raise ValueError('boot_a has an offset of its own already')
    new_env = env.replace(b'blkdevparts=' + spec.encode(), b'blkdevparts=' + new_spec.encode(), 1)
    bootargs = variables(env).get('sys_bootargs')
    if bootargs is None:
        raise ValueError("the image's environment has no sys_bootargs (the kernel's command line)")
    if CONSOLE_OFF not in bootargs.split():
        new_env = new_env.replace(b'sys_bootargs=' + bootargs.encode(), b'sys_bootargs=' + f'{bootargs} {CONSOLE_OFF}'.encode(), 1)
    if b'\0\0' not in new_env[:ENV_SIZE - 4]:
        raise ValueError("the image's environment is full")
    new_env = new_env[:ENV_SIZE - 4]
    new_env = new_env.ljust(ENV_SIZE - 4, b'\0')
    moved = dict((p[0], p) for p in parse(new_spec, os.path.getsize(source) + GAP))

    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(out))) as work:
        system = os.path.join(work, 'system.img')
        extract(source, 'system_a', system)
        print('Putting the spoke in the system partition')
        add_spoke(system, spoke, work, capture)
        harden(system, work)
        userdata = None
        if ssh_key:
            print('Adding the SSH key (a developer\'s image: never release it)')
            userdata = os.path.join(work, 'userdata.img')
            extract(source, 'userdata', userdata)
            add_key(userdata, ssh_key, work)
        print('Putting the image together')
        with open(source, 'rb') as src, open(out + '.part', 'wb') as dst:
            dst.write(struct.pack('<I', zlib.crc32(new_env)) + new_env)
            for name, at, size in parts[1:]:
                if name in ('system_a', 'system_b'):
                    with open(system, 'rb') as s:
                        copy(s, 0, size, dst, moved[name][1])
                elif name == 'userdata' and userdata:
                    with open(userdata, 'rb') as u:
                        copy(u, 0, size, dst, moved[name][1])
                elif name in ('boot_a', 'boot_b'):
                    src.seek(at)
                    dst.seek(moved[name][1])
                    dst.write(hdmi_refclk_27mhz(src.read(size)))
                else:
                    copy(src, at, size, dst, moved[name][1])
            dst.seek(BLOCK_AT)
            dst.write(MAGIC.ljust(BLOCK_SIZE, b'\0'))
            dst.truncate(moved[names[-1]][1] + parts[-1][2])
    os.replace(out + '.part', out)
    files = [out]
    if compress:
        print('Compressing it')
        with open(out, 'rb') as src, lzma.open(out + '.xz', 'wb', preset=6) as dst:
            shutil.copyfileobj(src, dst, 8 << 20)
        files.append(out + '.xz')
    for path in files:
        digest = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8 << 20), b''):
                digest.update(chunk)
        with open(path + '.sha256', 'w') as f:
            f.write(f'{digest.hexdigest()}  {os.path.basename(path)}\n')
    print('Made', ', '.join(os.path.basename(p) for p in files))


def main(args):
    if args[:1] == ['layout'] and len(args) == 2:
        with open(args[1], 'rb') as f:
            env = variables(read_env(f))
        for name, at, size in parse(env['blkdevparts'], os.path.getsize(args[1])):
            print(f'{name:10} at {at >> 10:>8}K  {size >> 10:>8}K')
        print('root:', next((w for w in env.get('sys_bootargs', '').split() if w.startswith('root=')), '?'))
        with open(args[1], 'rb') as f:
            f.seek(BLOCK_AT)
            print('settings block at 2880K:', 'yes' if f.read(len(MAGIC)) == MAGIC else 'no')
    elif args[:1] == ['extract'] and len(args) == 4:
        extract(*args[1:])
    elif args[:1] == ['build'] and len(args) >= 4:
        rest, options = args[4:], {}
        for flag in ('--capture', '--ssh-key'):
            if flag in rest:
                at = rest.index(flag)
                options[flag[2:].replace('-', '_')], rest = rest[at + 1], rest[:at] + rest[at + 2:]
        if rest not in ([], ['--xz']):
            sys.exit(__doc__)
        build(args[1], args[2], args[3], compress=rest == ['--xz'], **options)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, KeyError, RuntimeError, StopIteration) as e:
        sys.exit(f'Error: {e}')
