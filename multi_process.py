import os
import signal
import time

from multiprocessing import Process


def f(name):
    count = 0
    while True:
        count += 1
        print name + str(count)
        time.sleep(0.1)


def kill(pid):
    time.sleep(5)
    os.kill(pid, signal.SIGTERM)


def main():
    p1 = Process(target=f, args=('bob',))
    p1.start()

    p2 = Process(target=kill, args=(p1.ident,))
    p2.start()

if __name__ == '__main__':
    main()

