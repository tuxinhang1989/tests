from __future__ import absolute_import

import fcntl
import os
import sys


def set_close_exec(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, flags|fcntl.FD_CLOEXEC)


def _set_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags|os.O_NONBLOCK)


def main():
    f = open('output.txt', 'a')

    os.dup2(f.fileno(), 1)
    f.write('aaa\n')
    sys.stdout.write('bbb\n')

if __name__ == "__main__":
    main()

