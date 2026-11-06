"""Luckfox's PicoKVM microSD images: what's in them, taking a partition out, and making the spoke's
image from theirs.

    python3 tools/picokvm_image.py layout IMAGE                  the partitions, and where they are
    python3 tools/picokvm_image.py extract IMAGE NAME OUT        one partition (system_a, say) into OUT
    python3 tools/picokvm_image.py build IMAGE SPOKE OUT [--xz]  the spoke's image (SPOKE: its ARMv7 binary)

The image has no MBR or GPT: its partitions are in the U-Boot environment at its start (32 KiB, with
a CRC), as blkdevparts=mmcblk1:32K(env),512K@32K(idblock),... The spoke's image is theirs with:

- the spoke in its system partitions (both: A and B), started by Luckfox's launcher (RkLunch.sh) in
  place of their apps, once the drivers and the network are up
- 96K free after the security partition, holding the installer's settings block at 2880K (a 64 KiB
  boundary in the first 64 MiB, where the installer looks); the partitions after it move down to
  make room, and keep their numbers, which Luckfox's scripts use

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
# The card's last partition runs to its end: its filesystem grows to fill it, once
if [ ! -f "$data/.grown" ]; then
    resize2fs /dev/block/by-name/userdata && mkdir -p "$data" && touch "$data/.grown"
fi
while true; do
    /usr/bin/touchstream-spoke run -data "$data" 2>&1 | logger -t touchstream
    sleep 2
done
"""


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


def add_spoke(system, spoke, work):
    """Put the spoke in a system partition (an ext4 image), started in place of Luckfox's apps."""
    lunch = os.path.join(work, 'RkLunch.sh')
    debugfs(system, ['dump /oem/usr/bin/RkLunch.sh {w}/RkLunch.sh'], writable=False)
    with open(lunch) as f:
        script = f.read()
    if not LAUNCH.search(script):
        raise ValueError("Luckfox's launcher (RkLunch.sh) doesn't start their apps where it did: check it by hand")
    with open(lunch, 'w') as f:
        f.write(LAUNCH.sub('\n\t/usr/bin/touchstream-start &   # TouchStream, in place of Luckfox\'s apps\n', script, 1))
    start = os.path.join(work, 'touchstream-start')
    with open(start, 'w') as f:
        f.write(START)
    shutil.copy(spoke, os.path.join(work, 'touchstream-spoke'))
    commands = ['rm /oem/usr/bin/RkLunch.sh', 'write {w}/RkLunch.sh /oem/usr/bin/RkLunch.sh',
                'write {w}/touchstream-spoke /usr/bin/touchstream-spoke', 'write {w}/touchstream-start /usr/bin/touchstream-start']
    for dest in ('/oem/usr/bin/RkLunch.sh', '/usr/bin/touchstream-spoke', '/usr/bin/touchstream-start'):
        commands += [f'set_inode_field {dest} mode 0100755', f'set_inode_field {dest} uid 0', f'set_inode_field {dest} gid 0']
    debugfs(system, commands)


def build(source, spoke, out, compress=False):
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
    new_env = env.replace(b'blkdevparts=' + spec.encode(), b'blkdevparts=' + new_spec.encode(), 1)[:ENV_SIZE - 4]
    new_env = new_env.ljust(ENV_SIZE - 4, b'\0')
    moved = dict((p[0], p) for p in parse(new_spec, os.path.getsize(source) + GAP))

    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(out))) as work:
        system = os.path.join(work, 'system.img')
        extract(source, 'system_a', system)
        print('Putting the spoke in the system partition')
        add_spoke(system, spoke, work)
        print('Putting the image together')
        with open(source, 'rb') as src, open(out + '.part', 'wb') as dst:
            dst.write(struct.pack('<I', zlib.crc32(new_env)) + new_env)
            for name, at, size in parts[1:]:
                if name in ('system_a', 'system_b'):
                    with open(system, 'rb') as s:
                        copy(s, 0, size, dst, moved[name][1])
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
    elif args[:1] == ['build'] and len(args) in (4, 5) and args[4:] in ([], ['--xz']):
        build(args[1], args[2], args[3], compress=args[4:] == ['--xz'])
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, KeyError, RuntimeError, StopIteration) as e:
        sys.exit(f'Error: {e}')
