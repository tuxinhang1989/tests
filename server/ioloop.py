# encoding: utf-8
import select
import time
import heapq
import collections
import itertools

from posix import set_close_exec, Waker


class EPollIOLoop(object):
    _EPOLLIN = 0x001
    _EPOLLPRI = 0x002
    _EPOLLOUT = 0x004
    _EPOLLERR = 0x008
    _EPOLLHUP = 0x010
    _EPOLLRDHUP = 0x2000
    _EPOLLONESHOT = (1 << 30)
    _EPOLLET = (1 << 31)

    NONE = 0
    READ = _EPOLLIN
    WRITE = _EPOLLOUT
    ERROR = _EPOLLERR | _EPOLLHUP

    def __init__(self):
        self._impl = select.epoll()
        set_close_exec(self._impl.fileno())
        self._handlers = {}
        self._events = {}
        self._callbacks = collections.deque()
        self._timeouts = []
        self._cancellations = 0
        self._running = False
        self._stopped = False
        self._closing = False
        self._timeout_counter = itertools.count()

        self._waker = Waker()
        self.add_handler(self._waker.fileno(), lambda fd, events: self._waker.consume(), self.READ)

    def close(self, all_fds=False):
        self._closing = True
        self.remove_handler(self._waker.fileno())
        if all_fds:
            for fd, handler in list(self._handlers.values()):
                self.close_fd(fd)
        self._waker.close()
        self._impl.close()
        self._callbacks = None
        self._timeouts = None

    def add_handler(self, fd, handler, events):
        fd, obj = self.split_fd(fd)
        self._handlers[fd] = (obj, handler)
        self._impl.register(fd, events|self.ERROR)

    def update_handler(self, fd, events):
        fd, obj = self.split_fd(fd)
        self._impl.modify(fd, events|self.ERROR)

    def remove_handler(self, fd):
        fd, obj = self.split_fd(fd)
        self._handlers.pop(fd, None)
        self._events.pop(fd, None)
        try:
            self._impl.unregister(fd)
        except:
            print "Error deleting fd from IOLoop"

    def start(self):
        if self._running:
            raise RuntimeError("IOLoop is already running")
        if self._stopped:
            self._stopped = False
            return
        self._running = True

        try:
            while True:
                ncallbacks = len(self._callbacks)

                due_timeouts = []
                if self._timeouts:
                    now = time.time()
                    while self._timeouts:
                        if self._timeouts[0].callback is None:
                            heapq.heappop(self._timeouts)
                            self._cancellations -= 1
                        elif self._timeouts[0].deadline <= now:
                            due_timeouts.append(heapq.heappop(self._timeouts))
                        else:
                            break
                    if (self._cancellations > 512 and self._cancellations > (len(self._timeouts) >> 1)):
                        self._cancellations = 0
                        self._timeouts = [x for x in self._timeouts if x.callback is not None]
                        heapq.heapify(self._timeouts)

                for i in range(ncallbacks):
                    self._run_callback(self._callbacks.popleft())
                for timeout in due_timeouts:
                    if timeout.callback is not None:
                        self._run_callback(timeout.callback)

                due_timeouts = timeout = None

                if self._callbacks:
                    poll_timeout = 0.0
                elif self._timeouts:
                    poll_timeout = self._timeouts[0].deadline - self.time()
                    poll_timeout = max(0, min(poll_timeout, _POLL_TIMEOUT))
                else:
                    poll_timeout = _POLL_TIMEOUT

                if not self._running:
                    break

                try:
                    event_pairs = self._impl.poll(poll_timeout)
                except Exception as e:
                    if errno_from_exception(e) == errno.EINTR:
                        continue
                    else:
                        raise

                self._events.update(event_pairs)
                while self._events:
                    fd, events = self._events.popitem()
                    try:
                        fd_obj, handler_func = self._handlers[fd]
                        handler_func(fd_obj, events)
                    except (OSError, IOError) as e:
                        if errno_from_exception(e) == errno.EPIPE:
                            pass
                        else:
                            self.handle_callback_exception(self._handlers.get(fd))
                    except Exception:
                        self.handle_callback_exception(self._handlers.get(fd))
                fd_obj = handler_func = None

        finally:
            self._stopped = False

    def stop(self):
        self._running = False
        self._stopped = True
        self._waker.wake()

    def add_callback(self, callback, *args, **kwargs):
        if self._closing:
            return
        self._callbacks.append(functools.partial(callback, *args, **kwargs))

    def close_fd(self, fd):
        try:
            try:
                fd.close()
            except AttributeError:
                os.close(fd)
        except OSError:
            pass

    def split_fd(self, fd):
        try:
            return fd.fileno(), fd
        except AttributeError:
            return fd, fd

    def handle_callback_exception(self, callback):
        print "Exception in callback %r" % callback

    def _run_callback(self, callback):
        try:
            ret = callback()
            if re
