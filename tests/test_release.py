import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from tools.package_release import package


class ReleaseTests(unittest.TestCase):
    def test_bundle_excludes_runtime_data_and_has_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'project';root.mkdir()
            (root/'README.md').write_text('fixture project')
            for name in ('thoth','web','tools','reference','deploy','tests','data','secrets','.git'):(root/name).mkdir()
            (root/'thoth'/'app.py').write_text('app fixture')
            (root/'data'/'paper.sqlite').write_text('private')
            (root/'secrets'/'key.txt').write_text('private fixture credential')
            (root/'.env').write_text('private fixture value')
            archive=Path(d)/'release.tar.gz';result=package(archive,root=root)
            self.assertEqual(result['files'],2)
            with tarfile.open(archive) as bundle:
                names=bundle.getnames()
                self.assertNotIn('THOTH/.env',names)
                self.assertFalse(any('/data/' in n or '/secrets/' in n or '/.git/' in n for n in names))
                manifest=json.load(bundle.extractfile('THOTH/release-manifest.json'))
                self.assertEqual(len(manifest['files']),2)
            with self.assertRaises(FileExistsError):package(archive,root=root)
