import os
import time
import signal


def myHandler(signum, frame):
    print("I recieved: ", signum)


"""
signal.signal(signal.SIGALRM, myHandler)
signal.alarm(5)
signal.setitimer(signal.ITIMER_REAL, 10, 3)
print signal.getitimer(signal.ITIMER_REAL)
signal.pause()
print("End of signal demo")
"""

def sigchld_test():
    signal.signal(signal.SIGCHLD, myHandler)

    pid = os.fork()
    if pid == 0:
        time.sleep(2)
        os._exit(0)
    else:
        os.waitpid(pid, 0)


def sigwinch_test():
    signal.signal(signal.SIGWINCH, myHandler)
    try:
        time.sleep(5)
    except Exception as e:
        print e
        

if __name__ == "__main__":
    sigwinch_test()

