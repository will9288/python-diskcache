from __future__ import print_function

import os
import subprocess as sp


def percentile(sequence, percent):
    if not sequence:
        return None

    values = sorted(sequence)

    if percent == 0:
        return values[0]

    pos = int(len(values) * percent) - 1

    return values[pos]


def secs(value):
    units = ['s ', 'ms', 'us', 'ns']
    pos = 0

    if value == 0:
        return '  0.000ns'
    else:
        for unit in units:
            if value > 1:
                return '%7.3f' % value + unit
            else:
                value *= 1000


def run(*args):
    "Run command, print output, and return output."
    print('utils$', *args)
    result = sp.check_output(args)
    print(result)
    return result.strip()


def mount_ramdisk(size, path):
    "Mount RAM disk at `path` with `size` in bytes."
    sectors = size / 512

    os.makedirs(path)

    dev_path = run('hdid', '-nomount', 'ram://%d' % sectors)
    run('newfs_hfs', '-v', 'RAMdisk', dev_path)
    run('mount', '-o', 'noatime', '-t', 'hfs', dev_path, path)

    return dev_path


def unmount_ramdisk(dev_path, path):
    "Unmount RAM disk with `dev_path` and `path`."
    run('umount', path)
    run('diskutil', 'eject', dev_path)
    run('rm', '-r', path)


def retry(sql, query):
    pause = 0.001
    error = sqlite3.OperationalError

    for _ in range(int(LIMITS[u'timeout'] / pause)):
        try:
            sql(query).fetchone()
        except sqlite3.OperationalError as exc:
            error = exc
            time.sleep(pause)
        else:
            break
    else:
        raise error

    del error

