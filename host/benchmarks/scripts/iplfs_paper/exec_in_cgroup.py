"""Record actual memory isolation immediately before executing a benchmark."""

import json
import os
from pathlib import Path
import sys


def main():
    """Validate cgroup membership and limit, then replace this process with the workload."""
    evidence, group, memory, *command = sys.argv[1:]
    membership = Path('/proc/self/cgroup').read_text()
    directory = Path('/sys/fs/cgroup') / group
    limit = (directory / 'memory.max').read_text().strip()
    if f'0::/{group}\n' not in membership or limit != memory or not command:
        raise RuntimeError('The benchmark is not in its required memory cgroup')
    with Path(evidence).open('x') as stream:
        json.dump({'membership': membership, 'memory_max': limit,
                   'swap_max': (directory / 'memory.swap.max').read_text().strip(),
                   'command': command}, stream, indent=2)
    os.execv(command[0], command)


if __name__ == '__main__':
    main()
