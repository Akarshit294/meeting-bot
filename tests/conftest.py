import pytest


def pytest_collection_modifyitems(config, items):
    # Docker tests use one shared stack: run them in file order, one at a time.
    items.sort(key=lambda item: (0 if "docker" not in item.keywords else 1))
