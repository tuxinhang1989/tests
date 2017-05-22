import os
import time


def main():
    pid = os.fork()
    if pid > 0:  # parent
        pid, status = os.waitpid(pid, os.WNOHANG)
        print pid, status
    else:  # child
        time.sleep(5)

if __name__ == "__main__":
    main()

