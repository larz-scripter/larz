"""
larz.contrib — optional adapters that light up when a Larz Stack library is
installed. The framework core stays zero-dependency; these modules let you opt
into the stack for richer money, auth, and AI features.

    pip install larz            # zero-dependency core, as always
    pip install larz[money]     # + larzmoney, larzledger, larzpdf
    pip install larz[auth]      # + larzcrypt, larztotp, larzqr, larzsession
    pip install larz[ai]        # + larzagent
    pip install larz[full]      # the whole stack

Each adapter imports its backing library lazily, so importing larz.contrib
never forces a dependency — you only need the library when you actually use the
feature, and you get a clear install hint if it's missing.
"""

__all__ = ["require", "pdf", "ledger", "twofa_qr", "agents", "available"]


def require(module, extra):
    """Import a stack library or raise a clear, actionable ImportError."""
    try:
        return __import__(module)
    except ImportError:
        raise ImportError(
            "larz.contrib needs the '%s' library, which isn't installed.\n"
            "    pip install larz[%s]      # or:  pip install %s"
            % (module, extra, module))


def available(module):
    """True if a stack library is importable (for graceful feature-gating)."""
    try:
        __import__(module)
        return True
    except ImportError:
        return False
