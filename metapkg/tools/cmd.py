import subprocess
import sys

from metapkg import prog


def cmd(*cmd, errors_are_fatal=True, **kwargs):
    default_kwargs = {
        'stderr': sys.stderr,
        'stdout': subprocess.PIPE,
        'universal_newlines': True,
    }

    default_kwargs.update(kwargs)

    try:
        p = subprocess.run(cmd, check=True, **default_kwargs)
    except subprocess.CalledProcessError as e:
        if errors_are_fatal:
            msg = '{} failed with exit code {}'.format(
                ' '.join(cmd), e.returncode)
            prog.die(msg)
        else:
            raise

    return p.stdout
