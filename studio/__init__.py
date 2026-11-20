"""The old package name, kept so nothing that says `studio` stops working.

Everything lives in `anhy` now. This folder only points there, and it points at the very same modules: one copy of
each, so `studio.accounts` and `anhy.accounts` share their state rather than each having their own.

    python -m studio.server          still starts Anhy Clips   (the new way: python -m anhy.server)
    from studio import accounts      still works
    import studio.accounts           still works

New code should say `anhy`."""
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys

_NEW = "anhy"


class _SameModule(importlib.abc.Loader):
    """Hands back the module from `anhy` instead of running a second copy of it."""

    def __init__(self, real: str):
        self.real = real

    def create_module(self, spec):
        return importlib.import_module(self.real)

    def exec_module(self, module):
        pass                                    # it has already been run, under its new name


class _OldNames(importlib.abc.MetaPathFinder):
    """Any `studio.something` answers with `anhy.something`. The real files in this folder come first, so the four
    commands that can be run with -m are found the normal way."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(__name__ + "."):
            return None
        real = f"{_NEW}.{fullname.split('.', 1)[1]}"
        try:
            spec = importlib.util.find_spec(real)
        except (ImportError, ValueError):
            return None
        if spec is None:
            return None
        return importlib.machinery.ModuleSpec(fullname, _SameModule(real), origin=spec.origin)


sys.meta_path.append(_OldNames())


def __getattr__(name):
    """`studio.something` as an attribute, for anything the finder above did not cover."""
    if name.startswith("__"):
        raise AttributeError(name)
    try:
        return importlib.import_module(f"{_NEW}.{name}")
    except ModuleNotFoundError as e:
        if e.name and not e.name.startswith(_NEW):
            raise                               # something the module itself needs is missing: say so, don't hide it
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__():
    import pkgutil
    return sorted(m.name for m in pkgutil.iter_modules(importlib.import_module(_NEW).__path__))
