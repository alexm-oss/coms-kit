"""coms: a lightweight, decentralized coordination layer for parallel work.

Actors take turns on risky operations (writing a shared git branch, shipping)
against one repo on one machine, with no human in the loop. Built on POSIX
flock(2) plus git's native ref compare-and-swap.
"""

__version__ = "0.1.0"
