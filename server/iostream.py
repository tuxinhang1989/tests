# encoding: utf-8

from __future__ import unicode_literals, print_function

import socket
import os
import re
import sys
import collections
import errno
import numbers

from tornado.concurrent import TracebackFuture

_ERRNO_WOULDBLOCK = (errno.EWOULDBLOCK, errno.EAGAIN)

_ERRNO_CONNRESET = (errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE, errno.ETIMEDOUT)

_ERRNO_INPROGRESS = (errno.EINPROGRESS,)


class StreamClosedError(IOError):
    def __init__(self, real_error=None):
        super(StreamClosedError, self).__init__("Stream is closed")
        self.real_error = real_error


class UnsatisfiableReadError(Exception):
    pass


class StreamBufferFullError(Exception):
    pass


def errno_from_exception(e):
    if hasattr(e, 'errno'):
        return e.errno
    elif e.args:
        return e.args[0]
    else:
        return None


class IOStream(object):
    def __init__(self, socket, io_loop=None, max_buffer_size=None,
                 read_chunk_size=None, max_write_buffer_size=None):
        self.socket = socket
        self.socket.setblocking(False)
        self.io_loop = io_loop
        self.max_buffer_size = max_buffer_size or 104857600
        self.read_chunk_size = min(read_chunk_size or 65536, self.max_buffer_size // 2)
        self.max_write_buffer_size = max_write_buffer_size
        self.error = None
        self._read_buffer = bytearray()
        self._read_buffer_pos = 0
        self._read_buffer_size = 0
        self._write_buffer = bytearray()
        self._write_buffer_pos = 0
        self._write_buffer_size = 0
        self._write_buffer_frozen = False
        self._total_write_index = 0
        self._total_write_done_index = 0
        self._pending_writes_while_frozen = []
        self._read_delimiter = None
        self._read_regex = None
        self._read_max_bytes = None
        self._read_bytes = None
        self._read_partial = False
        self._read_until_close = False
        # self._read_callback = None
        self._read_future = None
        # self._streaming_callback = None
        # self._write_callback = None
        self._write_futures = collections.deque()
        self._close_callback = None
        # self._connect_callback = None
        self._connect_future = None
        self._connecting = False
        self._state = None
        self._pending_callbacks = 0
        self._closed = False

    def fileno(self):
        return self.socket

    def close_fd(self):
        self.socket.close()
        self.socket = None

    def get_fd_error(self):
        errno = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        return socket.error(errno, os.strerror(errno))

    def read_from_fd(self):
        try:
            chunk = self.socket.recv(self.read_chunk_size)
        except socket.error as e:
            if e.args[0] in _ERRNO_WOULDBLOCK:
                return None
            else:
                raise
        if not chunk:
            self.close()
            return None
        return chunk

    def write_to_fd(self, data):
        return self.socket.send(data)

    def connect(self, address, server_hostname=None):
        self._connecting = True
        future = self._connect_future = TracebackFuture()
        try:
            self.socket.connect(address)
        except socket.error as e:
            if (errno_from_exception(e) not in _ERRNO_INPROGRESS and
                    errno_from_exception(e) not in _ERRNO_WOULDBLOCK):
                self.close()
                return future
        self._add_io_state(self.io_loop.WRITE)
        return future

    def _handle_connect(self):
        err = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if err != 0:
            self.error = socket.error(err, os.strerror(err))
            self.close()
            return
        future = self._connect_future
        self._connect_future = None
        future.set_result(self)
        self._connecting = False

    def set_nodelay(self, value):
        if (self.socket is not None and
                self.socket.family in (socket.AF_INET, socket.AF_INET6)):
            try:
                self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1 if value else 0)
            except socket.error as e:
                if e.errno != errno.EINVAL and not self._is_connreset(e):
                    raise

    def _is_connreset(self, exc):
        return (isinstance(exc, (socket.error, IOError)) and
                errno_from_exception(exc) in _ERRNO_CONNRESET)

    def _add_io_state(self, state):
        if self.closed():
            return
        if self._state is None:
            self._state = self.io_loop.ERROR | state
            self.io_loop.add_handler(self.fileno(), self._handle_events, self._state)
        elif not self._state & state:
            self._state = self._state | state
            self.io_loop.update_handler(self.fileno(), self._state)

    def closed(self):
        return self._closed

    def close(self):
        if not self.closed():
            exc_info = sys.exc_info()
            if any(exc_info):
                self.error = exc_info[1]
            if self._read_until_close:
                self._read_until_close = False
                self._run_read_callback(self._read_buffer_size, False)
            if self._state is not None:
                self.io_loop.remove_handler(self.fileno())
                self._state = None
            self.close_fd()
            self._closed = True
        self._maybe_run_close_callback()

    def _maybe_run_close_callback(self):
        if self.closed() and self._pending_callbacks == 0:
            futures = []
            if self._read_future is not None:
                futures.append(self._read_future)
                self._read_future = None
            futures += [future for _, future in self._write_futures]
            self._write_futures.clear()
            if self._connect_future is not None:
                futures.append(self._connect_future)
                self._connect_future = None
            for future in futures:
                future.set_exception(StreamClosedError(real_error=self.error))
            self._write_buffer = None
            self._write_buffer_size = 0

    def reading(self):
        return self._read_future is not None

    def writing(self):
        return self._write_buffer_size > 0

    def _handle_events(self, fd, events):
        if self.closed():
            print("Got events for closed stream %s" % fd)
            return
        try:
            if self._connecting:
                self._handle_connect()
            if self.closed():
                return
            if events & self.io_loop.READ:
                self._handle_read()
            if self._closed():
                return
            if events & self.io_loop.WRITE:
                self._handle_write()
            if self.closed():
                return
            if events & self.io_loop.ERROR:
                self.error = self.get_fd_error()
                self.io_loop.add_callback(self.close)
                return
            state = self.io_loop.ERROR
            if self.reading():
                state |= self.io_loop.READ
            if self.writing():
                state |= self.io_loop.WRITE
            if state == self.io_loop.ERROR and self._read_buffer_size == 0:
                state |= self.io_loop.READ
            if state != self._state:
                assert self._state is not None, "shouldn't happen: _handle_events without self._state"
                self._state = state
                self.io_loop.update_handler(self.fileno(), self._state)
        except UnsatisfiableReadError as e:
            print("Unsatisfiable read, closing connection: %s" % e)
            self.close()
        except Exception:
            print("Uncaught exception, closing connection.")
            self.close()
            raise

    def _handle_read(self):
        try:
            pos = self._read_to_buffer_loop()
        except UnsatisfiableReadError:
            raise
        except Exception as e:
            print("error on read: %s" % e)
            self.close()
            return
        if pos is not None: # read success
            self._read_from_buffer(pos)
            return
        else:
            self._maybe_run_close_callback()

    def _read_to_buffer_loop(self):
        try:
            if self._read_bytes is not None:
                target_bytes = self._read_bytes
            elif self._read_max_bytes is not None:
                target_bytes = self._read_max_bytes
            elif self.reading():
                target_bytes = None
            else:
                target_bytes = 0
            next_find_pos = 0
            self._pending_callbacks += 1
            while not self.closed():
                if self._read_to_buffer() == 0:
                    break

                if (target_bytes is not None and self._read_buffer_size >= target_bytes):
                    break

                if self._read_buffer_size >= next_find_pos:
                    pos = self._find_read_pos()
                    if pos is not None:
                        return pos
                    next_find_pos = self._read_buffer_size * 2
            return self._find_read_pos()
        finally:
            self._pending_callbacks -= 1

    def _read_to_buffer(self):
        while True:
            try:
                chunk = self.read_from_fd()
            except (socket.error, IOError, OSError) as e:
                if errno_from_exception(e) == errno.EINTR:
                    continue
                if self._is_connreset(e):
                    self.close()
                    return
                self.close()
                raise
            break
        if chunk is None:
            return 0
        self._read_buffer += chunk
        self._read_buffer_size += len(chunk)
        if self._read_buffer_size > self.max_buffer_size:
            print("Reached maximum read buffer size")
            self.close()
            raise StreamBufferFullError("Reached maximum read buffer size")
        return len(chunk)

    def _find_read_pos(self):
        if (self._read_bytes is not None and
                (self._read_buffer_size >= self._read_bytes or
                    (self._read_partial and self._read_buffer_size > 0))):
            num_bytes = min(self._read_bytes, self._read_buffer_size)
            return num_bytes
        elif self._read_delimiter is not None:
            if self._read_buffer:
                loc = self._read_buffer.find(self._read_delimiter, self._read_buffer_pos)
                if loc != -1:
                    loc -= self._read_buffer_pos
                    delimiter_len = len(self._read_delimiter)
                    self._check_max_bytes(self._read_delimiter, loc + delimiter_len)
                    return loc + delimiter_len
                self._check_max_bytes(self._read_delimiter, self._read_buffer_size)
        elif self._read_regex is not None:
            if self._read_buffer:
                m = self._read_regex.search(self._read_buffer, self._read_buffer_pos)
                if m is not None:
                    loc = m.end() - self._read_buffer_pos
                    self._check_max_bytes(self._read_regex, loc)
                    return loc
                self._check_max_bytes(self._read_regex, self._read_buffer_size)
        return None

    def _check_max_bytes(self, delimiter, size):
        if (self._read_max_bytes is not None and
                size > self._read_max_bytes):
            raise UnsatisfiableReadError("delimiter %r not found within %d bytes" % (
                delimiter, self._read_max_bytes))

    def _read_from_buffer(self, pos):
        self._read_bytes = self._read_delimiter = self._read_regex = None
        self._read_partial = False
        self._run_read_callback(pos, False)

    def _run_read_callback(self, size, streaming):
        future = self._read_future
        self._read_future = None
        future.set_result(self._consume(size))
        self._maybe_add_error_listener()

    def _consume(self, loc):
        if loc == 0:
            return b""
        assert loc <= self._read_buffer_size
        b = (memoryview(self._read_buffer)[self._read_buffer_pos:self._read_buffer_pos + loc]).tobytes()
        self._read_buffer_pos += loc
        self._read_buffer_size -= loc
        if self._read_buffer_pos > self._read_buffer_size:
            del self._read_buffer[:self._read_buffer_pos]
            self._read_buffer_pos = 0
        return b

    def _check_closed(self):
        if self.closed():
            raise StreamClosedError(real_error=self.error)

    def _maybe_add_error_listener(self):
        if self._pending_callbacks != 0:
            return
        if self._state is None or self._state == self.io_loop.ERROR:
            if self.closed():
                self._maybe_run_close_callback()
            elif (self._read_buffer_size == 0 and self._close_callback is not None):
                self._add_io_state(self.io_loop.READ)

    def read_until(self, delimiter, max_bytes=None):
        future = self._set_read_future()
        self._read_delimiter = delimiter
        self._read_max_bytes = max_bytes
        try:
            self._try_inline_read()
        except UnsatisfiableReadError as e:
            print("Unsatisfiable read, closing connection: %s" % e)
            self.close()
            return future
        except:
            if future is not None:
                future.add_done_callback(lambda f: f.exception())
            raise
        return future

    def _set_read_future(self):
        assert self._read_future is None, "Already reading"
        self._read_future = TracebackFuture()
        return self._read_future

    def _try_inline_read(self):
        pos = self._find_read_pos()
        if pos is not None:
            self._read_from_buffer(pos)
            return
        self._check_closed()
        try:
            pos = self._read_to_buffer_loop()
        except Exception:
            self._maybe_run_close_callback()
            raise
        if pos is not None:
            self._read_from_buffer(pos)
            return
        # close the stream or listen for new data.
        if self.closed():
            self._maybe_run_close_callback()
        else:
            self._add_io_state(self.io_loop.READ)

    def read_until_close(self):
        future = self._set_read_future()
        if self.closed():
            self._run_read_callback(self._read_buffer_size, False)
            return future
        self._read_until_close = True
        try:
            self._try_inline_read()
        except:
            if future is not None:
                future.add_done_callback(lambda f: f.exception())
            raise
        return future

    def read_bytes(self, num_bytes, partial=False):
        future = self._set_read_future()
        assert isinstance(num_bytes, numbers.Integral)
        self._read_bytes = num_bytes
        self._read_partial = partial
        try:
            self._try_inline_read()
        except:
            if future is not None:
                future.add_done_callback(lambda f: f.exception())
            raise
        return future

    def read_until_regex(self, regex, max_bytes=None):
        future = self._set_read_future()
        self._read_regex = re.compile(regex)
        self._read_max_bytes = max_bytes
        try:
            self._try_inline_read()
        except UnsatisfiableReadError as e:
            print("Unsatisfiable read, closing connection: %s" % e)
            self.close()
            return future
        except:
            if future is not None:
                future.add_done_callback(lambda f: f.exception())
            raise
        return future

    def write(self, data):
        self._check_closed()
        if data:
            if (self.max_write_buffer_size is not None and
                    self._write_buffer_size + len(data) > self.max_write_buffer_size):
                raise StreamBufferFullError("Reached maximum write buffer size")
            if self._write_buffer_frozen:
                self._pending_writes_while_frozen.append(data)
            else:
                self._write_buffer += data
                self._write_buffer_size += len(data)
            self._total_write_index += len(data)
        future = TracebackFuture()
        future.add_done_callback(lambda f: f.exception())
        self._write_futures.append((self._total_write_index, future))
        if not self._connecting:
            self._handle_write()
            if self._write_buffer_size:
                self._add_io_state(self.io_loop.WRITE)
            self._maybe_add_error_listener()
        return future

    def _handle_write(self):
        while self._write_buffer_size:
            assert self._write_buffer_size >= 0
            try:
                start = self._write_buffer_pos
                if self._write_buffer_frozen:
                    size = self._write_buffer_frozen
                else:
                    size = self._write_buffer_size
                num_bytes = self.write_to_fd(memoryview(self._write_buffer)[start:start+size])
                if num_bytes == 0:
                    self._got_empty_write(size)
                    break
                self._write_buffer_pos += num_bytes
                self._write_buffer_size -= num_bytes
                if self._write_buffer_pos > self._write_buffer_size:
                    del self._write_buffer[:self._write_buffer_pos]
                    self._write_buffer_pos = 0
                if self._write_buffer_frozen:
                    self._unfreeze_write_buffer()
                self._total_write_done_index += num_bytes
            except (socket.error, IOError, OSError) as e:
                if e.args[0] in _ERRNO_WOULDBLOCK:
                    self._got_empty_write(size)
                    break
                else:
                    if not self._is_connreset(e):
                        print("Write error on %s: %s" % (self.fileno(), e))
                    self.close()
                    return

        while self._write_futures:
            index, future = self._write_futures[0]
            if index > self._total_write_done_index:
                break
            self._write_futures.popleft()
            future.set_result(None)

    def _freeze_write_buffer(self, size):
        self._write_buffer_frozen = size

    def _unfreeze_write_buffer(self):
        self._write_buffer_frozen = False
        self._write_buffer += b''.join(self._pending_writes_while_frozen)
        self._write_buffer_size += sum(map(len, self._pending_writes_while_frozen))
        self._pending_writes_while_frozen[:] = []

    def _got_empty_write(size):
        pass

    def set_close_callback(self, callback):
        self._close_callback = callback
        self._maybe_add_error_listener()

