"""coms: a lightweight coordination layer for parallel agents on one machine.

Agents take turns on risky operations (writing a shared git branch, shipping,
any named local resource) and can message each other. Built on POSIX flock(2)
and a JSON state file of leases.
"""

__version__ = "0.2.0"
