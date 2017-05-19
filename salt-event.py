# encoding: utf-8

import salt.utils.event


def main():
    event = salt.utils.event.MasterEvent('/var/run/salt/master')

    for data in event.iter_events(full=True):
        print data
        print "-----"


if __name__ == "__main__":
    main()

