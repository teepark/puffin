# vim: fileencoding=utf8:et:sw=4:ts=8:sts=4

import errno
import fcntl
import os
import resource
import select
import socket

from penguin import fds

from greenlet import greenlet

getcurrent = greenlet.getcurrent

main_greenlet = getcurrent()
while main_greenlet.parent:
    main_greenlet = main_greenlet.parent


_paused = []
_sleeping = [None] * resource.getrlimit(resource.RLIMIT_NOFILE)[1]
_ep = None


def _getep():
    global _ep
    if _ep is None:
        _ep = select.epoll()
    return _ep


def schedule(target=None, args=(), kwargs=None):
    if target is None:
        return lambda f: schedule(f, args, kwargs)
    if isinstance(target, greenlet) or target is main_greenlet:
        glet = target
    else:
        glet = scheduled_greenlet(target, args, kwargs)

    _paused.append((glet, None, None))

    return target


def _timerfd_prefix(fd, events):
    os.close(fd)


def schedule_in(seconds, target=None, args=(), kwargs=None):
    if target is None:
        return lambda f: schedule_in(seconds, f, args, kwargs)
    if isinstance(target, greenlet) or target is main_greenlet:
        glet = target
    else:
        glet = scheduled_greenlet(target, args, kwargs, _timerfd_prefix)

    tfd = fds.timerfd_create(fds.CLOCK_MONOTONIC, fds.TFD_NONBLOCK)
    fds.timerfd_settime(tfd, seconds)

    _sleeping[tfd] = glet
    _getep().register(tfd, select.EPOLLIN)

    return target


def wait(fd, mask=select.EPOLLIN|select.EPOLLOUT):
    _sleeping[fd] = getcurrent()
    _getep().register(fd, mask)

    result_fd, events = mainloop.switch()

    return events


def wait_multi(fd_events_pairs):
    current = getcurrent()
    ep = _getep()
    reg = ep.register
    unreg = ep.unregister
    sl = _sleeping

    for fd, evs in fd_events_pairs:
        sl[fd] = current
        reg(fd, evs)

    result_fd, events = mainloop.switch()

    for fd, evs in fd_events_pairs:
        sl[fd] = None
        unreg(fd)

    return result_fd, events


def scheduled_greenlet(func, args=(), kwargs=None, prefix=None):
    def stack_top(fd, events):
        if prefix is not None:
            prefix(fd, events)
        func(*args, **(kwargs or {}))
    return greenlet(stack_top, parent=mainloop)


def pause():
    _paused.append((getcurrent(), None, None))
    mainloop.switch()


def pause_for(seconds):
    tfd = fds.timerfd_create(fds.CLOCK_MONOTONIC, fds.TFD_NONBLOCK)
    fds.timerfd_settime(tfd, seconds)
    _sleeping[tfd] = getcurrent()
    _getep().register(tfd, select.EPOLLIN)
    fd, events = mainloop.switch()
    os.close(fd)


@greenlet
def mainloop():
    global _paused
    ep = _getep()
    unreg = ep.unregister

    while 1:
        ready = _paused
        _paused = []

        if ready:
            timeout = 0
        else:
            timeout = -1

        for fd, events in _getep().poll(timeout):
            glet = _sleeping[fd]
            unreg(fd)
            if glet is not None:
                ready.append((glet, fd, events))

        for glet, fd, events in ready:
            glet.switch(fd, events)

# give mainloop a chance to get into its loop,
# quick before anything else can go wrong
pause()


class Socket(object):

    __slots__ = ('_sock', '_fileno')

    def _nonblock(self):
        fl = fcntl.fcntl(self._fileno, fcntl.F_GETFL)
        if fl & os.O_NONBLOCK == 0:
            fcntl.fcntl(self._fileno, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    @classmethod
    def fromsock(cls, sock):
        self = object.__new__(cls)
        self._sock = sock
        self._fileno = sock.fileno()
        self._nonblock()
        return self

    def __init__(self, family=socket.AF_INET, type_=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP):
        sock = self._sock = socket._realsocket(family, type_, proto)
        fileno = self._fileno = sock.fileno()
        self._nonblock()

    def accept(self):
        while 1:
            try:
                client, addr = self._sock.accept()
                return self.fromsock(client), addr
            except socket.error, exc:
                if exc.args[0] != errno.EAGAIN:
                    raise
                wait(self._fileno, select.EPOLLIN)

    def bind(self, addr):
        return self._sock.bind(addr)

    def listen(self, backlog):
        return self._sock.listen(backlog)

    def setsockopt(self, level, optname, value):
        return self._sock.setsockopt(level, optname, value)

    def close(self):
        os.close(self._fileno)

    def recv(self, length, flags=0):
        while 1:
            try:
                return self._sock.recv(length, flags)
            except socket.error, exc:
                if exc.args[0] != errno.EAGAIN:
                    raise
            wait(self._fileno, select.EPOLLIN)

    def send(self, data, flags=0):
        while 1:
            try:
                return self._sock.send(data)
            except socket.error, exc:
                if exc.args[0] != errno.EAGAIN:
                    raise
            wait(self._fileno, select.EPOLLOUT)

    def sendall(self, data, flags=0):
        length = len(data)
        sent = self.send(data, flags)
        while sent < length:
            sent += self.send(data[sent:], flags)
