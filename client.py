# encoding: utf-8
import time

from tornado.httpclient import AsyncHTTPClient
from tornado import gen
from tornado import ioloop


@gen.coroutine
def visit():
    client = AsyncHTTPClient()
    resp = yield client.fetch('http://192.168.66.105:8005')
    print resp.body, time.time()


@gen.coroutine
def main():
    futures = []
    for i in range(25):
        futures.append(visit())
    yield futures

if __name__ == "__main__":
    io_loop = ioloop.IOLoop.instance()
    io_loop.run_sync(main)

