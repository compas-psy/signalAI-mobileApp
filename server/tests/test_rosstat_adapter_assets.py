from pathlib import Path

from app.research.adapters import rosstat_prices


def test_rosstat_trust_bundle_is_packaged_beside_adapter():
    adapter_dir = Path(rosstat_prices.__file__).parent

    assert rosstat_prices._CA_BUNDLE.is_file()
    assert rosstat_prices._CA_BUNDLE.parent.parent == adapter_dir
