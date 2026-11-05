"""Fixed Linux sandbox fixture. Input is a canonical owned run UUID, never paths/commands."""
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from uuid import UUID

SAFE = b'SAFE_INPUT'
CANARY = b'SYNTHETIC_W08_OUTSIDE_CANARY'


def prepare(run_id):
    identifier = str(UUID(run_id))
    if identifier != run_id or os.name != 'posix':
        raise ValueError('Canonical run and real Linux sandbox required')
    workspace = Path('/workspace')
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError('Actual non-aliased workspace required')
    allowed = workspace/'allowed'
    allowed.mkdir(mode=0o700,exist_ok=False)
    os.chown(allowed,1000,1000)
    input_file = allowed/'input.txt'
    with input_file.open('xb') as stream: stream.write(SAFE)
    os.chmod(input_file,0o600)
    os.chown(input_file,1000,1000)
    outside = Path('/tmp')/('w08-'+identifier+'.canary')
    with outside.open('xb') as stream: stream.write(CANARY)
    # Deliberately readable: a broken following read must leak the synthetic canary,
    # rather than be accidentally hidden by OS permissions. This is never host user data.
    os.chmod(outside,0o644)
    link = allowed/'private-link.txt'
    link.symlink_to(outside)
    if not stat.S_ISLNK(link.lstat().st_mode) or link.readlink() != outside or outside.parent == workspace:
        raise ValueError('Physical symlink/outside fixture not established')
    return {'run_id':identifier,'platform':'linux','input_sha256':hashlib.sha256(input_file.read_bytes()).hexdigest(),
            'outside_sha256':hashlib.sha256(outside.read_bytes()).hexdigest(),'symlink':True,
            'outside_virtual_path':str(outside),'symlink_virtual_path':'/allowed/private-link.txt'}


if __name__ == '__main__':
    print(json.dumps(prepare(sys.argv[1])))
