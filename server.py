#!/usr/bin/env python
# vim: fileencoding=utf8:et:sw=4:ts=8:sts=4

import errno
import os
import select
import socket
import sys

ispypy = sys.subversion[0] == 'PyPy'

if not ispypy:
    import puffin

import greenhouse

if not ispypy:
    import gevent
    import gevent.socket

import vanilla


if not ispypy:
    def puffin_serve():
        servsock = puffin.Socket()
        servsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        servsock.bind(("127.0.0.1", 8000))
        servsock.listen(socket.SOMAXCONN)

        while 1:
            client, addr = servsock.accept()
            puffin.schedule(puffin_handler, (client,))
            del client

    def puffin_handler(sock):
        recv_request(sock)
        sock.sendall("HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 14\r\n\r\nHello, World 1")


def greenhouse_serve():
    servsock = greenhouse.Socket()
    servsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servsock.bind(("127.0.0.1", 8000))
    servsock.listen(socket.SOMAXCONN)

    while 1:
        client, addr = servsock.accept()
        greenhouse.schedule(greenhouse_handler, (client,))

def greenhouse_handler(sock):
    recv_request(sock)
    sock.sendall("HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 14\r\n\r\nHello, World 2")
    sock.shutdown(socket.SHUT_RDWR)


if not ispypy:
    def gevent_serve():
        servsock = gevent.socket.socket()
        servsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        servsock.bind(("127.0.0.1", 8000))
        servsock.listen(socket.SOMAXCONN)

        while 1:
            client, addr = servsock.accept()
            gevent.spawn(gevent_handler, client)
            del client

    def gevent_handler(sock):
        recv_request(sock)
        sock.sendall("HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 14\r\n\r\nHello, World 3")
        sock.shutdown(socket.SHUT_RDWR)


def vanilla_serve():
    hub = vanilla.Hub()
    listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen.bind(("127.0.0.1", 8000))
    listen.listen(socket.SOMAXCONN)
    listen.setblocking(0)
    events = hub.register(listen.fileno(),
            select.EPOLLIN|select.EPOLLHUP|select.EPOLLERR)

    while 1:
        events.recv()
        conn, addr = listen.accept()
        hub.spawn(vanilla_handler, conn, hub)
        del conn

def vanilla_handler(conn, hub):
    cfd = conn.fileno()
    events = hub.register(cfd,
            select.EPOLLIN|select.EPOLLHUP|select.EPOLLERR)
    while 1:
        events.recv()
        incoming = conn.recv(16384)
        if not incoming:
            return
        if '\r\n\r\n' in incoming:
            break

    hub.unregister(cfd)

    resp = "HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 14\r\n\r\nHello, World 4"

    sent, events = 0, None
    while 1:
        try:
            sent += conn.send(resp[sent:])
        except socket.error, exc:
            if exc.args[0] != errno.EAGAIN:
                raise

        if sent == len(resp):
            break

        if events is None:
            events = hub.register(cfd,
                    select.EPOLLOUT|select.EPOLLHUP|select.EPOLLERR)
            events.recv()

    hub.unregister(cfd)
    conn.shutdown(socket.SHUT_RDWR)


def recv_request(sock):
    while 1:
        incoming = sock.recv(16384)
        if not incoming:
            return False
        if '\r\n\r\n' in incoming:
            return True


def main(env, argv):
    if argv[1] == 'vanilla':
        vanilla_serve()
    elif argv[1] == 'greenhouse':
        greenhouse_serve()
    elif not ispypy:
        if argv[1] == 'gevent':
            gevent_serve()
        elif argv[1] == 'puffin':
            puffin_serve()
    return 0


if __name__ == '__main__':
    exit(main(os.environ, sys.argv))
