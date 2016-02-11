DiskCache: Disk and File-based Cache
====================================

Rationale: file-based cache in Django is essentially broken. Culling files is
too costly. Large caches are forced to scan lots of files and do lots of
deletes on some operations. Takes too long in the request/response cycle.

Solution: Each operation, "set" and "del" should delete at most two to ten
expired keys.

Each "get" needs only check expiry and delete if needed. Make it speedy.

If only we had some kind of file-based database... we do! It's called
SQLite. For metadata and small stuff, use SQLite and for bigger things use
files.

Features
--------

- Pure-Python
- Developed on Python 2.7
- Tested on CPython 2.6, 2.7, 3.3, 3.4, 3.5 and PyPy, PyPy3
- Get full_path reference to value.
- Allow storing raw data.
- Small values stored in database.
- Leverages SQLite native types: int, float, unicode, blob.
- Thread-safe and process-safe.
- Multiple eviction policies

  - Least-Recently-Stored
  - Least-Recently-Used
  - Least-Frequently-Used

- Stampede barrier decorator.
- Metadata support for "tag" to evict a group of keys at once.

Quickstart
----------

Installing DiskCache is simple with
`pip <http://www.pip-installer.org/>`_::

  $ pip install diskcache

You can access documentation in the interpreter with Python's built-in help
function::

  >>> from diskcache import DjangoCache
  >>> help(DjangoCache)

Caveats
-------

* Types matter in key equality comparisons. Comparisons like ``1 == 1.0`` and
  ``b'abc' == u'abc'`` return False.

Tutorial
--------

TODO

TODO
----

1. Improve stress_test_core.

   - Support different key sizes / constraints.
   - Support different value sizes / constraints.
   - Test eviction policies.

2. Create and test Django interface.
3. Create and test CLI interface.

   - get, set, store, delete, expire, clear, evict, path, check, stats, show

4. Document SQLite database restore trick using dump command and cache.check(fix=True).
5. Test and document stampede_barrier.
6. Benchmark BerkeleyDB backend using APSW.
7. Use SQLAlchemy as interface to database.
8. Support metadata (store_time, expire_time, tag) in ``get``

Reference and Indices
---------------------

.. toctree::

   api

* `DiskCache Documentation`_
* `DiskCache at PyPI`_
* `DiskCache at GitHub`_
* `DiskCache Issue Tracker`_
* :ref:`search`
* :ref:`genindex`

.. _`DiskCache Documentation`: http://www.grantjenks.com/docs/diskcache/
.. _`DiskCache at PyPI`: https://pypi.python.org/pypi/diskcache/
.. _`DiskCache at GitHub`: https://github.com/grantjenks/python-diskcache/
.. _`DiskCache Issue Tracker`: https://github.com/grantjenks/python-diskcache/issues/

License
-------

.. include:: ../LICENSE
