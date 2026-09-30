import importlib.util
import json
from pathlib import Path
import zipfile

import pytest
import local_updates
from test_local_updates import package, write_json

spec = importlib.util.spec_from_file_location('publish_update', Path(__file__).resolve().parents[1] / 'scripts/publish-update.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def test_published_channel_can_be_consumed_by_updater(tmp_path):
    source = tmp_path / 'release.zip'
    package(source)
    channel = tmp_path / 'channel'
    feed = publisher.publish(source, channel)
    assert (channel / feed['package']).read_bytes() == source.read_bytes()
    assert json.loads((channel / 'latest.json').read_text()) == feed
    root = tmp_path / 'old'
    write_json(root / 'local-manifest.json', {'version': '1.8.19', 'files': []})
    state = tmp_path / 'DocProntoLocal'
    local_updates.save_settings(state, str(channel), True)
    assert local_updates.check(state, root)['state'] == 'ready'


def test_bad_package_does_not_replace_published_feed(tmp_path):
    source = tmp_path / 'release.zip'
    package(source)
    channel = tmp_path / 'channel'
    publisher.publish(source, channel)
    before = (channel / 'latest.json').read_bytes()
    broken = tmp_path / 'bad.zip'
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(broken, 'w') as output:
        for entry in original.infolist():
            raw = original.read(entry)
            if entry.filename.endswith('/run.py'):
                raw = b'corrupted'
            output.writestr(entry, raw)
    with pytest.raises(ValueError):
        publisher.publish(broken, channel)
    assert (channel / 'latest.json').read_bytes() == before
