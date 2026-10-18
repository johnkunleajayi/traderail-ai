"""Inherited test-only subprocess protection; not on production PYTHONPATH."""
import os
import sys

os.environ["TRADERAIL_DATABASE_URL"] = "postgresql+psycopg://test_guard:test_guard@127.0.0.1:1/blocked_test_database"

def _deny_test_network(event, args):
    if event == "socket.connect":
        raise RuntimeError("TEST DATABASE SAFETY: physical network connections are forbidden; use isolated mocked sessions/transports.")

sys.addaudithook(_deny_test_network)

# libpq opens sockets in C, so socket audit events alone are insufficient.
# Guard the project's PostgreSQL driver lazily, preserving pure import tests.
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder


def _deny_driver(*args, **kwargs):
    raise RuntimeError("TEST DATABASE SAFETY: physical database connections are forbidden; use isolated mocked sessions.")


class _GuardedLoader:
    def __init__(self, original):
        self.original = original

    def create_module(self, spec):
        return self.original.create_module(spec)

    def exec_module(self, module):
        self.original.exec_module(module)
        module.connect = _deny_driver
        module.Connection.connect = classmethod(_deny_driver)
        module.AsyncConnection.connect = classmethod(_deny_driver)


class _DriverGuard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "psycopg":
            return None
        spec = PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is not None:
            spec.loader = _GuardedLoader(spec.loader)
        return spec


sys.meta_path.insert(0, _DriverGuard())
