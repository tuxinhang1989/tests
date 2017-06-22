# encoding: utf-8
import fcntl
import os
import signal


def set_close_exec(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, flags|fcntl.FD_CLOEXEC)


def _set_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags|os.O_NONBLOCK)


class Waker(object):
    def __init__(self):
        r, w = os.pipe()
        set_close_exec(r)
        set_close_exec(w)
        _set_nonblocking(r)
        _set_nonblocking(w)
        self.reader = os.fdopen(r, 'rb', 0)
        self.writer = os.fdopen(w, 'wb', 0)

    def fileno(self):
        return self.reader.fileno()

    def write_fileno(self):
        return self.writer.fileno()

    def wake(self):
        try:
            self.writer.write('x')
        except (IOError, ValueError):
            pass

    def consume(self):
        try:
            while True:
                result = self.reader.read()
                if not result:
                    break
            return result
        except IOError:
            pass

    def close(self):
        self.reader.close()
        self.writer.close()


def handler(signum, frame):
    print signum
    print waker.reader.read()


waker = Waker()

if __name__ == "__main__":
    signal.set_wakeup_fd(waker.write_fileno())
    signal.signal(signal.SIGALRM, handler)
    signal.pause()

