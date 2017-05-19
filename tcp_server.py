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
            # conn, addr = self.socket.accept()
            # print "connected by", addr
            while not self.__shutdown_request:
                r, w, e = select.select([self], [], [], poll_interval)
                print r
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
        time.sleep(2)
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
    active_children = []

    def process_conn(self, conn, client_address):
        print "conn: ", id(conn)
        pid = os.fork()
        if pid:  # parent
            print "conn in parent: ", id(conn)
            self.active_children.append(pid)
            self.close_conn(conn)
            return
        else:
            print "conn in child: ", id(conn)
            try:
                self.finish_conn(conn, client_address)
                self.shutdown_conn(conn)
                os._exit(0)
            except:
                try:
                    self.handle_error(conn, client_address)
                    self.shutdown_conn(conn)
                finally:
                    os._exit(1)


class Server(TCPServer):
    pass


class RequestHandler(object):
    def __init__(self, conn, client_address, server):
        self.conn = conn
        self.client_address = client_address
        self.server = server
        self.process()

    def process(self):
        print "connected by", self.client_address
        print self.conn.recv(1024)
        response = "HTTP/1.1 200 OK\r\nServer:tcp server\r\nContent-Length:11\r\n\r\nhello world"
        self.conn.sendall(response)


if __name__ == "__main__":
    server_address = ("0.0.0.0", 8005)
    server = Server(server_address, RequestHandler)
    try:
        server.serve_forever()
    except (OSError, select.error) as e:
        if e.args[0] != errno.EINTR:
            raise

