# encoding: utf-8
import socket
import select
import errno
import os
import time
import threading


class TCPServer(object):
    address_family = socket.AF_INET

    socket_type = socket.SOCK_STREAM

    request_queue_size = 5

    allow_reuse_address = False

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        self.server_address = server_address
        self.RequestHandlerClass = RequestHandlerClass
        self.__is_shut_down = threading.Event()
        self.__shutdown_request = False
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
        self.__is_shut_down.clear()
        try:
            while not self.__shutdown_request:
                r, w, e = select.select([self], [], [], poll_interval)
                if self in r:
                    self._handle_conn_noblock()
        finally:
            self.__shutdown_request = False
            self.__is_shut_down.set()

    def shutdown(self):
        self.__shutdown_request = True
        self.__is_shut_down.wait()

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


class RequestHandler(object):
    rbufsize = -1
    wbufsize = 0
    timeout = None

    disable_nagle_algorithm = False

    def __init__(self, conn, client_address, server):
        self.conn = conn
        self.client_address = client_address
        self.server = server
        self.setup()
        try:
            self.handle()
        finally:
            self.finish()

    def setup(self):
        if self.timeout is not None:
            self.conn.settimeout(self.timeout)
        if self.disable_nagle_algorithm:
            self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, True)
        self.rfile = self.conn.makefile('rb', self.rbufsize)
        self.wfile = self.conn.makefile('wb', self.wbufsize)

    def finish(self):
        if not self.wfile.closed:
            try:
                self.wfile.flush()
            except socket.error:
                pass

        self.wfile.close()
        self.rfile.close()

    def process(self):
        print self.conn.recv(1024)
        response = "HTTP/1.1 200 OK\r\nServer:tcp server\r\nContent-Length:11\r\n\r\nhello world"
        self.conn.sendall(response)


def shutdown(server):
    time.sleep(5)
    server.shutdown()


if __name__ == "__main__":
    server_address = ("0.0.0.0", 8005)
    server = ThreadingTCPServer(server_address, RequestHandler)
    # t = threading.Thread(target=shutdown, args=(server,))
    # t.start()
    try:
        server.serve_forever()
    except (OSError, select.error) as e:
        if e.args[0] != errno.EINTR:
            raise

