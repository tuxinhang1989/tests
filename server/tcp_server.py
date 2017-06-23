# encoding: utf-8
import socket
import select
import os
import time
import threading

from request_handler import RequestHandler

from .posix import set_close_exec
from .iostream import IOStream, errno_from_exception, _ERRNO_WOULDBLOCK
from .ioloop import EPollIOLoop

_DEFAULT_BACKLOG = 128


def _quote_html(html):
    return html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TCPServer(object):
    address_family = socket.AF_INET

    socket_type = socket.SOCK_STREAM

    request_queue_size = 5

    allow_reuse_address = False

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        self.server_address = server_address
        self.RequestHandlerClass = RequestHandlerClass
        self.socket = socket.socket(self.address_family, self.socket_type)
        if bind_and_activate:
            self.server_bind()
            self.server_activate()

    def server_bind(self):
        if self.allow_reuse_address:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()

    def server_activate(self):
        self.socket.listen(self.request_queue_size)

    def serve_forever(self, poll_interval=0.5):
        impl = select.epoll()
        fd = self.fileno()
        handlers = {fd: (self, self._handle_conn_noblock)}
        impl.register(fd, 0x001)
        while True:
            #r, w, e = select.select([self], [], [], poll_interval)
            #if self in r:
            #    self._handle_conn_noblock()

            event_pairs = impl.poll(poll_interval)
            for fd, events in event_pairs:
                try:
                    obj, handler_func = handlers[fd]
                    handler_func()
                except:
                    pass

    def _handle_conn_noblock(self):
        try:
            conn, client_address = self.get_conn()
        except socket.error:
            return
        if self.verify_conn(conn, client_address):
            try:
                self.process_conn(conn, client_address)
            except:
                self.handle_error(conn, client_address)
                self.shutdown_conn(conn)

    def handle_error(self, conn, client_address):
        print '-' * 40
        print "Exception happend during processing of request from", client_address
        import traceback
        traceback.print_exc()
        print '-' * 40

    def verify_conn(self, conn, client_address):
        return True

    def process_conn(self, conn, client_address):
        self.finish_conn(conn, client_address)
        self.shutdown_conn(conn)

    def finish_conn(self, conn, client_address):
        self.RequestHandlerClass(conn, client_address, self)

    def server_close(self):
        self.socket.close()

    def fileno(self):
        return self.socket.fileno()

    def get_conn(self):
        return self.socket.accept()

    def shutdown_conn(self, conn):
        try:
            conn.shutdown(socket.SHUT_WR)
        except socket.error:
            pass
        self.close_conn(conn)

    def close_conn(self, conn):
        conn.close()

    def close(self):
        self.socket.close()


class ForkingMixIn(object):
    active_children = None
    max_children = 20

    def collect_children(self):
        if self.active_children is None:
            return
        while len(self.active_children) >= self.max_children:
            try:
                pid, status = os.waitpid(0, 0)
            except os.error:
                pid = None
            print pid, status
            if pid in self.active_children:
                self.active_children.remove(pid)
        for child in self.active_children:
            try:
                pid, status = os.waitpid(child, os.WNOHANG)
            except os.error:
                pid = None
            if not pid:
                continue
            try:
                self.active_children.remove(pid)
            except ValueError, e:
                raise ValueError("%s. x=%d and list=%r" % (e.message, pid, self.active_children))

    def process_conn(self, conn, client_address):
        self.collect_children()
        pid = os.fork()
        if pid:  # parent
            if self.active_children is None:
                self.active_children = []
            self.active_children.append(pid)
            # print "children:", len(self.active_children), self.active_children
            self.close_conn(conn)
            return
        else:
            try:
                self.finish_conn(conn, client_address)
                print "forked at:", time.time()
                self.shutdown_conn(conn)
                os._exit(0)
            except:
                try:
                    self.handle_error(conn, client_address)
                    self.shutdown_conn(conn)
                finally:
                    os._exit(1)


class ThreadingMixIn(object):
    daemon_threads = False

    def process_in_thread(self, conn, client_address):
        try:
            self.finish_conn(conn, client_address)
            self.shutdown_conn(conn)
        except:
            self.handle_error(conn, client_address)
            self.shutdown_conn(conn)

    def process_conn(self, conn, client_address):
        t = threading.Thread(target=self.process_in_thread, args=(conn, client_address))
        t.daemon = self.daemon_threads
        t.start()


class ForkingTCPServer(ForkingMixIn, TCPServer):
    pass


class ThreadingTCPServer(ThreadingMixIn, TCPServer):
    pass


class AsyncTCPServer(object):
    def __init__(self, io_loop, max_buffer_size=None, read_chunk_size=None):
        self.io_loop = io_loop
        self._started = False
        self._stopped = False
        self.max_buffer_size = max_buffer_size
        self.read_chunk_size = read_chunk_size

    def listen(self, port, address=""):
        s = self.bind_socket(port, address=address)
        self.add_socket(s)

    def bind_socket(self, port, address=""):
        self.sock = s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        set_close_exec(s.fileno())
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setblocking(False)
        s.bind((address, port))
        s.listen(_DEFAULT_BACKLOG)
        return s

    def add_socket(self, s):
        """ Add to ioloop 
        """
        def accept_handler(fd, events):
            for i in xrange(_DEFAULT_BACKLOG):
                try:
                    connection, address = s.accept()
                except socket.error as e:
                    if errno_from_exception(e) in _ERRNO_WOULDBLOCK:
                        return
                    raise
                self._handle_connection(connection, address)
        self.io_loop.add_handler(s, accept_handler, self.io_loop.READ)

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.io_loop.remove_handler(self.sock)
        self.sock.close()

    def handle_stream(self, stream, address):
        print("Hello world")

    def _handle_connection(self, connection, address):
        try:
            stream = IOStream(connection, io_loop=self.io_loop, 
                    max_buffer_size=self.max_buffer_size, read_chunk_size=self.read_chunk_size)
            future = self.handle_stream(stream, address)
            if future is not None:
                self.io_loop.add_future(future, lambda f: f.result())
        except Exception:
            import traceback
            traceback.print_exc()
            print("Error in connection callback")


if __name__ == "__main__":
    server_address = ("0.0.0.0", 8008)
    # server = ThreadingTCPServer(server_address, RequestHandler)
    io_loop = EPollIOLoop()
    server = AsyncTCPServer(io_loop)
    server.listen(8008, "0.0.0.0")
    io_loop.start()
    """
    try:
        server.serve_forever()
    except (OSError, select.error) as e:
        if e.args[0] != errno.EINTR:
            raise
    except KeyboardInterrupt:
        server.close()
    """
