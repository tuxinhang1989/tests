# encoding: utf-8
import socket
import select


s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("192.168.66.105", 8005))
s.listen(5)

while True:
    r, w, e = select.select([s], [], [], 1)
    print r
    if s in r:  # socket is ready
        conn, client_addr = s.accept()
        print "connected by", client_addr
        print conn.recv(1024)
        response = "HTTP/1.1 200 OK\r\nServer:tcp server\r\nContent-Length:11\r\n\r\nhello world"
        conn.sendall(response)

