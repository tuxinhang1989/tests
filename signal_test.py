import signal


def myHandler(signum, frame):
    print("I recieved: ", signum)
    print(frame)


signal.signal(signal.SIGTERM, myHandler)
signal.pause()
print("End of signal demo")

