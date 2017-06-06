# encoding: utf-8
import socket
import select
import errno
import os
import sys
import time
import threading
import mimetools

DEFAULT_ERROR_MESSAGE = """\
<head>
<title>Error response</title>
</head>
<body>
<h1>Error response</h1>
<p>Error code %(code)d.</p>
<p>Message: %(message)s.</p>
<p>Error code explanation: %(code)s = %(explain)s.</p>
</body>
"""

DEFAULT_ERROR_CONTENT_TYPE = "text/html"

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
        while True:
            r, w, e = select.select([self], [], [], poll_interval)
            if self in r:
                self._handle_conn_noblock()

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
    server_version = "WSGIServer/1.0"
    sys_version = "Python/" + sys.version.split()[0]
    rbufsize = -1
    wbufsize = 0
    default_request_version = "HTTP/0.9"
    timeout = None
    protocol_version = "HTTP/1.1"
    MessageClass = mimetools.Message

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

    def parse_request(self):
        self.command = None
        self.request_version = version = self.default_request_version
        self.close_connection = 1
        requestline = self.raw_requestline
        requestline = requestline.rstrip('\r\n')
        self.requestline = requestline
        words = requestline.split()
        if len(words) == 3:
            command, path, version = words
            if version[:5] != "HTTP/":
                self.send_error(400, "Bad request version (%r)" % version)
                return False
            try:
                base_version_number = version.split('/', 1)[1]
                version_number = base_version_number.split(".")
                if len(version_number) != 2:
                    raise ValueError
                version_number = int(version_number[0]), int(version_number[1])
            except (ValueError, IndexError):
                self.send_error(400, "Bad request version(%r)" % version)
                return False
            if version_number >= (1, 1) and self.protocol_version >= "HTTP/1.1":
                self.close_connection = 0
            if version_number >= (2, 0):
                self.send_error(505, "Invalid HTTP Version (%s)" % base_version_number)
                return False
        elif len(words) == 2:
            command, path = words
            self.close_connection = 1
            if command != "GET":
                self.send_error(400, "Bad HTTP/0.9 request type (%r)" % command)
                return False
        elif not words:
            return False
        else:
            self.send_error(400, "Bad request syntax (%r)" % requestline)
            return False
        self.command, self.path, self.request_version = command, path, version

        self.headers = self.MessageClass(self.rfile, 0)

        conntype = self.headers.get('Connection', "")
        if conntype.lower() == "close":
            self.close_connection = 1
        elif conntype.lower() == 'keep-alive' and self.protocol_version >= "HTTP/1.1":
            self.close_connection = 0
        return True

    def handle_one_request(self):
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.send_error(414)
                return
            if not self.raw_requestline:
                self.close_connection = 1
                return
            if not self.parse_request():
                return
            mname = self.command.lower()
            if not hasattr(self, mname):
                self.send_error(501, "Unsupported method (%r)" % self.command)
                return
            method = getattr(self, mname)
            method()
            self.wfile.flush()
        except socket.timeout, e:
            self.log_error("Request timed out: %r", e)
            self.close_connection = 1
            return

    def handle(self):
        self.close_connection = 1
        self.handle_one_request()
        while not self.close_connection:
            self.handle_one_request()

    def get(self):
        content = "hello world"
        self.send_response(200)
        self.send_header('Content-Length', len(content))
        # self.send_header('Connection', 'keep-alive')
        self.end_headers()
        self.wfile.write(content)

    def send_response(self, code, message=None):
        self.log_request(code)
        if message is None:
            if code in self.responses:
                message = self.responses[code][0]
            else:
                message = ""
        if self.request_version != "HTTP/0.9":
            self.wfile.write("%s %d %s\r\n" % (self.protocol_version, code, message))
        self.send_header("Server", self.version_string())
        self.send_header("Date", self.date_time_string())

    def send_header(self, key, value):
        if self.request_version != 'HTTP/0.9':
            self.wfile.write("%s: %s\r\n" % (key, value))

        if key.lower() == "connection":
            if value.lower() == "close":
                self.close_connection = 1
            elif value.lower() == "keep-alive":
                self.close_connection = 0

    def end_headers(self):
        if self.request_version != "HTTP/0.9":
            self.wfile.write("\r\n")

    def send_error(self, code, message=None):
        try:
            short, long = self.responses[code]
        except KeyError:
            short, long = '???', '???'
        if message is None:
            message = short
        explain = long
        self.log_error("code %d, message %s", code, message)
        content = self.error_message_format % {"code": code, 
                "message": message, "explain": explain}
        self.send_response(code, message)
        self.send_header("Content-Type", self.error_content_type)
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and code >= 200 and code not in (204, 304):
            self.wfile.write(content)

    error_message_format = DEFAULT_ERROR_MESSAGE
    error_content_type = DEFAULT_ERROR_CONTENT_TYPE

    def log_request(self, code='-', size='-'):
        self.log_message('"%s" %s %s', self.requestline, str(code), str(size))

    def log_error(self, format, *args):
        self.log_message(format, *args)

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], 
                    self.log_date_time_string(), format % args))

    def version_string(self):
        return self.server_version + " " + self.sys_version

    def date_time_string(self, timestamp=None):
        if timestamp is None:
            timestamp = time.time()
        year, month, day, hh, mm, ss, wd, y, z = time.gmtime(timestamp)
        s = "%s, %02d %3s %4d %02d:%02d:%02d GMT" % (self.weekdayname[wd], 
                    day, self.monthname[month], year, hh, mm, ss)
        return s

    def log_date_time_string(self):
        now = time.time()
        year, month, day, hh, mm, ss, x, y, z = time.localtime(now)
        s = "%02d/%3s/%04d %02d:%02d:%02d" % (day, self.monthname[month], 
                year, hh, mm, ss)
        return s

    weekdayname = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    monthname = [None, "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", 
            "Aug", "Sep", "Oct", "Nov", "Dec"]

    def address_string(self):
        host, port = self.client_address[:2]
        return socket.getfqdn(host)

    def finish(self):
        if not self.wfile.closed:
            try:
                self.wfile.flush()
            except socket.error:
                pass

        print "connection closed"
        self.wfile.close()
        self.rfile.close()

    responses = {
        100: ('Continue', 'Request received, please continue'),
        101: ('Switching Protocols', 'Switching to new protocol; obey Upgrade header'),
        200: ('OK', 'Request fulfilled, document follows'),
        201: ('Created', 'Document created, URL follows'),
        202: ('Accepted', 'Request accepted, processing continues off-line'),
        203: ('Non-Authoritative Information', 'Reuest fulfilled from cache'),
        204: ('No Content', 'Request fulfilled, nothing follows'),
        205: ('Reset Content', 'Clear input form for further input.'),
        206: ('Partial Content', 'Partial Content follows.'),
        300: ('Multiple Choices', 'Object has several resources -- see URI list'),
        301: ('Moved Permanently', 'Object moved permanently -- see URI list'),
        302: ('Found', 'Object moved temporarily -- see URI list'),
        303: ('See Other', 'Object moved -- see Method and URL list'),
        304: ('Not Modified', 'Document has not changed since given time'),
        305: ('Use Proxy', 
              'You must use proxy specified in Location to access this resource'),
        307: ('Temporary Redirect', 'Object moved temporarily -- see URI list'),
        400: ('Bad Request', 'Bad request syntax or unsupported method'),
        401: ('Unauthorized', 'No permission -- see authorization schemes'),
        402: ('Payment Required', 'No payment -- see charging schemes'),
        403: ('Forbidden', 'Request forbidden -- authorization will not help'),
        404: ('Not Found', 'Nothing matches the gven URI'),
        405: ('Method not Allowed', 'Specified method is invalid for this resource.'),
        406: ('Not Acceptable', 'URI not available in preferred format.'),
        407: ('Proxy Authentication Required', 
              'You must authenticate with this proxy before proceeding.'),
        408: ('Request Timeout', 'Request timed out; try again later.'),
        409: ('Conflict', 'Request conflict.'),
        410: ('Gone', 'URI no longer exists and has been permanently removed.'),
        411: ('Length Required', 'Client must specify Content-Length.'),
        412: ('Precondition Failed', 'Precondition in headers is false'),
        413: ('Request Entity Too Large', 'Entity is too large.'),
        414: ('Request-URI Too Long', 'URI is too long.'),
        415: ('Unsupported Media Type', 'Entity body in unsupported format.'),
        416: ('Requested Range Not Satisfiable', 'cannot satisfy request range.'),
        417: ('Expectation Failed', 'Expect condition could not be satisfied.'),
        500: ('Internal Server Error', 'Server got itself in trouble'),
        501: ('Not Implemented', 'Server does not support this operation'),
        502: ('Bad Gateway', 'Invalid responses from another server/proxy.'),
        503: ('Service Unavailable', 
              'The server cannot process the request due to a high load'),
        504: ('Gateway Timeout', 'The gateway server did not receive a timely response'),
        505: ('HTTP Version Not Supported', 'Cannot fulfill request.'),
    }


if __name__ == "__main__":
    server_address = ("0.0.0.0", 8008)
    server = ThreadingTCPServer(server_address, RequestHandler)
    try:
        server.serve_forever()
    except (OSError, select.error) as e:
        if e.args[0] != errno.EINTR:
            raise

