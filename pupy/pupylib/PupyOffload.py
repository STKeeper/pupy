# -*- encoding: utf-8 -*-

import msgpack
import threading
import socket
import struct
import logging
import time
import errno
import ssl

class MsgPackMessages(object):
    def __init__(self, conn):
        self._conn = conn

    def recv(self):
        datalen_b = self._conn.recv(4)
        if datalen_b == '':
            raise EOFError

        datalen, = struct.unpack('>I', datalen_b)
        data = self._conn.recv(datalen)
        return msgpack.loads(data)

    def send(self, msg):
        data = msgpack.dumps(msg)
        datalen = len(data)
        datalen_b = struct.pack('>I', datalen)
        self._conn.sendall(datalen_b + data)

class PupyOffloadDNS(threading.Thread):
    def __init__(self, manager, handler, domain):
        threading.Thread.__init__(self)
        self.daemon = True
        self.active = True
        self.manager = manager
        self.handler = handler
        self.domain = domain
        self._conn = None

        self.cleaner = threading.Thread(target=handler.cleanup)
        self.cleaner.daemon = True

    def run(self):
        self.cleaner.start()

        while self.active:
            try:
                while self.active:
                    self._serve()

            except EOFError:
                time.sleep(1)
                continue

            except (socket.error, OSError), e:
                if e.errno == errno.ECONNREFUSED:
                    time.sleep(5)
                    continue
                else:
                    logging.exception(e)
                    self.active = False

            except Exception, e:
                logging.exception(e)
                self.active = False

    def _serve(self):
        self._conn = self.manager._connect(1, self.domain)
        conn = MsgPackMessages(self._conn)
        while self.active:
            request = conn.recv()
            if not request:
                return

            response = self.handler.process(request)
            conn.send(response)

    def stop(self):
        self.active = False
        if self._conn:
            self._conn.close()

        if self.handler:
            self.handler.finished.set()

class PupyOffloadSocket(object):
    def __init__(self, sock, lhost, lport, rhost, rport):
        self._sock = sock
        self._laddr = (lhost, lport)
        self._raddr = (rhost, rport)

    def getsockname(self):
        return self._laddr

    def getpeername(self):
        return self._raddr

    def __getattr__(self, attr):
        if attr in self.__dict__:
            return getattr(self, attr)
        return getattr(self._sock, attr)

class PupyOffloadAcceptor(object):
    def __init__(self, manager, proto, port=None, extra={}):
        self._manager = manager
        self._proto = proto
        self._host = None
        self._port = port
        self._conn = None
        self._extra = extra
        self.active = True

    def bind(self, addr):
        self._host, self._port = addr

    def listen(self, cnt):
        pass

    def settimeout(self, **args):
        pass

    def shutdown(self, arg):
        self.close()

    def close(self, **args):
        self.active = False
        if self._conn:
            self._conn.close()
            self._conn = None

    def accept(self):
        while self.active:
            try:
                self._conn = self._manager._connect(self._proto, self._port)

                m = MsgPackMessages(self._conn)
                conninfo = m.recv()

                if conninfo['extra']:
                    data = self._extra[conninfo['data']]
                    m.send(data)
                    conninfo = m.recv()

                return PupyOffloadSocket(
                    self._conn,
                    conninfo['lhost'], conninfo['lport'],
                    conninfo['rhost'], conninfo['rport']
                ), (conninfo['rhost'], conninfo['rport'])

            except (socket.error, OSError), e:
                if e.errno == errno.ECONNREFUSED:
                    time.sleep(5)
                    continue
                else:
                    raise

            except EOFError:
                time.sleep(1)
                continue

            except Exception, e:
                logging.exception(e)
                raise

class PupyOffloadManager(object):
    def __init__(self, server, ca, key, crt):
        if ':' in server:
            host, port = server.rsplit(':', 1)
            self._server = (host, int(port))
        else:
            self._server = server

        self._ca = ca
        self._key = key
        self._crt = crt
        self._external_ip = None
        self._ctx = ssl.create_default_context(
            purpose=ssl.Purpose.CLIENT_AUTH,
            cafile=self._ca
        )
        self._ctx.load_cert_chain(self._crt, self._key)
        self._ctx.set_alpn_protocols(['pp/1'])

    def dns(self, handler, domain):
        return PupyOffloadDNS(self, handler, domain)

    def tcp(self, port, extra={}):
        return PupyOffloadAcceptor(self, 2, port, extra)

    def kcp(self, port, extra={}):
        return PupyOffloadAcceptor(self, 3, port, extra)

    def ssl(self, port, extra={}):
        return PupyOffloadAcceptor(self, 4, port, extra)

    @property
    def external(self):
        if self._external_ip is None:
            c = self._connect(0, "")
            m = MsgPackMessages(c)
            self._external_ip = m.recv()['ip']

        return self._external_ip

    def _connect(self, conntype, bind, timeout=0):
        c = socket.create_connection(self._server)
        c = self._ctx.wrap_socket(c)

        m = MsgPackMessages(c)
        m.send({
            'prot': conntype,
            'bind': str(bind),
            'timeout': timeout,
        })
        return c