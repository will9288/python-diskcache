import doctest

import diskcache.core
import diskcache.djangocache
import diskcache.fanout
import diskcache.persistent


def test_core():
    failures, _ = doctest.testmod(diskcache.core)
    assert failures == 0


def test_djangocache():
    failures, _ = doctest.testmod(diskcache.djangocache)
    assert failures == 0


def test_fanout():
    failures, _ = doctest.testmod(diskcache.fanout)
    assert failures == 0


def test_persistent():
    failures, _ = doctest.testmod(diskcache.persistent)
    assert failures == 0
