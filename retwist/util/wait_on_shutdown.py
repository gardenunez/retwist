import logging
from typing import Any, Callable, Set, Union

from twisted.internet.defer import Deferred, DeferredList
from twisted.internet.interfaces import IListeningPort, IReactorCore
from twisted.web.http import Request
from twisted.web.server import Site


def wait_on_shutdown(reactor, site, port, timeout):
    # type: (IReactorCore, Site, IListeningPort, float) -> None
    """
    Change a running site to wait for pending requests on shutdown.

    When the reactor is to be shut down (e.g. on SIGINT), incoming connections are no longer accepted, but pending
    requests are allowed to execute until a timeout is hit.

    :param reactor: Reactor that is running your site, likely twisted.internet.reactor
    :param site: twisted.web.server.Site instance which is serving requests
    :param port: IListeningPort instance, e.g. as returned by reactor.listenTCP
    :param timeout: Timeout in seconds after which pending requests are canceled and shutdown is forced
    """
    running_reqs = set()  # type: Set[int]
    wrapped_request_factory = site.requestFactory  # type: Union[Callable, type]

    def request_factory(*args, **kwargs):
        # type: (*Any, **Any) -> Request
        """
        Request factor for a Twisted connection factory. During their execution, requests are kept in a set.
        """
        def remove_req(_, req_hash):
            running_reqs.remove(req_hash)

        req = wrapped_request_factory(*args, **kwargs)

        req_hash = id(req)
        running_reqs.add(req_hash)
        req.notifyFinish().addBoth(remove_req, req_hash)

        return req

    def shutdown():
        # type: () -> Deferred
        """
        Shutdown handler for the Twisted reactor. Stop accepting incoming requests. Then, delay the shutdown of the
        reactor up to a certain timeout if there are still requests running.
        """
        logging.info("Shutdown requested")

        # force shutdown after timeout
        timeout_deferred = Deferred()

        def kill_timeout():
            logging.warning("Timeout reached, canceling %s requests", len(running_reqs))
            timeout_deferred.callback(True)
        reactor.callLater(timeout, kill_timeout)

        # stop listening to new requests
        close_conn_deferred = port.stopListening()

        def kill_if_requests_done():
            """
            Call the timeout if all requests are done; otherwise wait a few more seconds.
            """
            if not running_reqs:
                logging.info("No pending requests, shutting down!")
                timeout_deferred.callback(True)
            else:
                logging.info("%s requests still running, waiting ...", len(running_reqs))
                reactor.callLater(5, kill_if_requests_done)
        kill_if_requests_done()

        return DeferredList([timeout_deferred, close_conn_deferred])

    site.requestFactory = request_factory
    reactor.addSystemEventTrigger("before", "shutdown", shutdown)
