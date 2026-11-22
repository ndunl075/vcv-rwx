"""Loaded by every Python process whose PYTHONPATH holds this directory (pool workers included):
switches the slot tracer on when RWX_SLOT_OUT is set."""
import os

if os.environ.get("RWX_SLOT_OUT"):
    import rwx_slots  # noqa: F401
