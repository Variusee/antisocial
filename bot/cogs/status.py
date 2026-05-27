import sys
sys.path.insert(0, "/root/antisocial")
from shared.status_cog import setup as _setup


def setup(bot):
    _setup(bot)
