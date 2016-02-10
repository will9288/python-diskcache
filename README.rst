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

TODO
----

7. Improve stress_test_core.
   - Support different key sizes / constraints.
   - Support different value sizes / constraints.
   - Test eviction policies.
8. Create and test Django interface.
9. Create and test CLI interface.
   - get, set, store, delete, expire, clear, evict, path, check, stats, show
10. Run pylint, check 10.0/10.0
10. Document SQLite database restore trick using dump command and cache.check(fix=True).
10. Test and document stampede_barrier.
10. Benchmark BerkeleyDB backend using APSW.
10. Use SQLAlchemy as interface to database.

Features
--------

- Pure-Python
- Developed on Python 2.7
- Tested on CPython 2.6, 2.7, 3.2, 3.3, 3.4, 3.5 and PyPy 2.5+, PyPy3 2.4+
- Get full_path reference to value.
- Allow storing raw data.
- Small values stored in database.
- Leverages SQLite native types: int, float, unicode, blob.
- Thread-safe and process-safe.
- Multiple eviction policies
  - Least-Recently-Store
  - Least-Recently-Used
  - Least-Frequently-Used
- Stampede barrier decorator.
- Metadata support for "tag" to evict a group of keys at once.

- TODO Support pickle alternatives: json, msgpack, pickle with compression
- TODO Write-through cache with writer in separate thread
  - Return version, value and cache key, version in dict.

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

Reference and Indices
---------------------

* `DiskCache Documentation`_
* `DiskCache at PyPI`_
* `DiskCache at GitHub`_
* `DiskCache Issue Tracker`_

.. _`DiskCache Documentation`: http://www.grantjenks.com/docs/diskcache/
.. _`DiskCache at PyPI`: https://pypi.python.org/pypi/diskcache/
.. _`DiskCache at GitHub`: https://github.com/grantjenks/python-diskcache/
.. _`DiskCache Issue Tracker`: https://github.com/grantjenks/python-diskcache/issues/

License
-------

Copyright 2016 Grant Jenks

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
