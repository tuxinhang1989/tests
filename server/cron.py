from tornado.ioloop import PeriodicCallback, IOLoop


def callback():
    print("Hello world")


def main():
    pc = PeriodicCallback(callback, 2 * 1000)
    pc.start()
    IOLoop.instance().start()

if __name__ == "__main__":
    main()

