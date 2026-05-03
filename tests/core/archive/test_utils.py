"""archive.utils — small platform helpers used across archive-mode."""
from __future__ import annotations


def test_cache_dir_separate_from_config():
    """Cache dir must NOT live under config dir.  The whole point of
    the split is that wiping cache (downloads, sevenzip blob, etc.)
    doesn't lose credentials."""
    from src.core.archive import utils
    from src.core.archive import credentials as cm
    cache  = utils.cache_dir()
    config = cm.credentials_path().parent
    # On every supported platform the two should be disjoint paths.
    assert cache != config
    assert config not in cache.parents
    assert cache  not in config.parents
