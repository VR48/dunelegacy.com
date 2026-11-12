#!/usr/bin/env python3
"""Exercise frozen staging delivery, isolation, provenance and unsafe-input boundaries."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('staging_artifact', ROOT / 'deploy/install-staging-artifact.py')
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)
SHA, CODE, CONTROL = 'a' * 40, 'b' * 40, 'c' * 40


class StagingArtifactTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        browser, service = self.root / 'browser', self.root / 'service'
        browser.mkdir()
        (service / 'public').mkdir(parents=True)
        (service / 'src').mkdir()
        policy = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
                  "connect-src 'self' https://dunelegacy.com https://api.github.com https://raw.githubusercontent.com;")
        (browser / 'index.html').write_text('<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" '
            'content="' + policy + '"></head><body><p>Staging 1.0.866dev+' + SHA[:12] + '</p>'
            '<script src="shell.js?v=staging"></script><script src="dunecity.js?v=staging"></script></body></html>')
        (browser / '.htaccess').write_text('Header always set Content-Security-Policy "' + policy +
            ' frame-ancestors \'none\';"\nHeader always set Cross-Origin-Opener-Policy "same-origin"\n'
            'Header always set Cross-Origin-Resource-Policy "same-origin"\nHeader always set Cache-Control "no-store"\n')
        for name in ('shell.js', 'shell.css', 'p2p-direct.js', 'dunecity.js', 'dunecity.wasm', 'dunecity.data'):
            (browser / name).write_bytes(('frozen runtime ' + name).encode())
        names = ['index.html', 'shell.js', 'shell.css', 'p2p-direct.js', 'dunecity.js', 'dunecity.wasm', 'dunecity.data']
        self.build = dict(version='1.0.866', sourceCommit=SHA, channel='staging', releaseBuild='OFF',
                          displayVersion='1.0.866dev+' + SHA[:12], cacheScope='/play-staging/',
                          artifacts=names, sha256={name: staging.digest(browser / name) for name in names})
        staging.write_json(browser / 'build.json', self.build)
        (service / 'public/index.php').write_text('<?php\nhttp_response_code(200);\n')
        (service / 'src/Config.php').write_text('<?php\nreturn [];\n')
        (service / 'public/.htaccess').write_text('Options -Indexes\nRewriteEngine On\nRewriteRule ^v1/ index.php [L,QSA]\n')
        staging.write_json(service / 'SOURCE.json', dict(commit=SHA, repository='https://github.com/VR48/dunecity',
            sha256={str(path.relative_to(service)): staging.digest(path) for path in service.rglob('*') if path.is_file()}))
        for source, name in ((browser, staging.BROWSER), (service, staging.SERVICE)):
            with tarfile.open(self.inputs / name, 'w:gz') as archive:
                archive.add(source, arcname='.')
        recipe = 'regression-fixture'
        identity = hashlib.sha256((SHA + '\n' + recipe).encode()).hexdigest()
        self.tag = 'staging-' + SHA + '-' + identity[:12]
        self.manifest = dict(schema=1, repository=staging.REPOSITORY, releaseBuild='OFF', recipe=recipe,
            inputIdentity=identity, tag=self.tag, sourceCommit=SHA, version='1.0.866',
            qualificationSourceCommit=SHA, qualificationRunId=11, qualificationRunAttempt=2,
            runId=41, runAttempt=1, producerSourceCommit=CONTROL, producerEvent='schedule',
            producerHeadBranch='main', producerWorkflowPath='.github/workflows/build.yml',
            sha256={name: staging.digest(self.inputs / name) for name in (staging.BROWSER, staging.SERVICE)})
        staging.write_json(self.inputs / staging.PUBLICATION, self.manifest)
        self.request = dict(schema=1, websiteCodeCommit=CODE, repository=staging.REPOSITORY,
            releaseTag=self.tag, sourceCommit=SHA, publicationManifestSha256=staging.digest(self.inputs / staging.PUBLICATION),
            browserArchiveSha256=staging.digest(self.inputs / staging.BROWSER),
            serviceArchiveSha256=staging.digest(self.inputs / staging.SERVICE))
        self.request_path = self.root / 'request.json'
        staging.write_json(self.request_path, self.request)
        staging.write_json(self.inputs / staging.REQUEST, self.request)
        self.website = self.root / 'staged/website'
        self.website.mkdir(parents=True)

    def test_preparation_changes_only_temporary_staging_and_preserves_frozen_runtime_and_manifests(self):
        for path in ('play/build.json', 'p2p/index.php', 'play-test-865-preserved/index.html'):
            target = self.website / path
            target.parent.mkdir(parents=True)
            target.write_bytes(b'unchanged production or immutable preview')
        result = staging.prepare(self.inputs, ROOT, self.website, CODE)
        play = self.website / 'play-staging'
        self.assertEqual(json.loads((play / 'source-build.json').read_text()), self.build)
        self.assertEqual((play / staging.PUBLICATION).read_bytes(), (self.inputs / staging.PUBLICATION).read_bytes())
        for name in self.build['sha256']:
            if name != 'index.html':
                self.assertEqual(staging.digest(play / name), self.build['sha256'][name])
        for path in ('play/build.json', 'p2p/index.php', 'play-test-865-preserved/index.html'):
            self.assertEqual((self.website / path).read_bytes(), b'unchanged production or immutable preview')
        self.assertEqual(result['delivery'], self.request)
        self.assertEqual(result['signalingSourceCommit'], SHA)
        self.assertEqual(result['signalingBaseUrl'], 'https://dunelegacy.com/play-staging/p2p')
        self.assertIn('connect-src ' + staging.CONNECTIONS + ';', (play / '.htaccess').read_text())
        self.assertNotIn("connect-src 'self'", (play / '.htaccess').read_text())
        self.assertIn('Staging ' + result['displayVersion'], (play / 'index.html').read_text())

    def test_wrong_code_stable_channel_unsafe_namespace_or_missing_hash_cannot_install(self):
        with self.assertRaisesRegex(ValueError, 'frozen website code SHA'):
            staging.verify_inputs(self.inputs, CONTROL)
        for field, value in (('releaseTag', 'v1.0.866'), ('sourceCommit', 'main'),
                             ('browserArchiveSha256', ''), ('repository', 'untrusted/game')):
            staging.write_json(self.request_path, dict(self.request, **{field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                staging.request_inputs(self.request_path, CODE)
        staging.write_json(self.request_path, dict(self.request, playRoot='play'))
        with self.assertRaisesRegex(ValueError, 'schema'):
            staging.request_inputs(self.request_path, CODE)
        self.assertFalse((self.website / 'play-staging').exists())

    def test_mutated_frozen_archive_is_rejected_before_any_browser_tree_is_created(self):
        (self.inputs / staging.BROWSER).write_bytes(b'not the frozen game')
        with self.assertRaisesRegex(ValueError, 'input hash mismatch'):
            staging.prepare(self.inputs, ROOT, self.website, CODE)
        self.assertFalse((self.website / 'play-staging').exists())

    def test_even_hash_consistent_archive_cannot_publish_unlisted_server_code(self):
        original = self.inputs / staging.BROWSER
        replacement = self.root / 'replacement.tar.gz'
        with tarfile.open(original) as source, tarfile.open(replacement, 'w:gz') as output:
            for member in source:
                output.addfile(member, source.extractfile(member) if member.isfile() else None)
            payload = b'<?php http_response_code(200);'
            extra = tarfile.TarInfo('undeclared.php')
            extra.size = len(payload)
            output.addfile(extra, io.BytesIO(payload))
        replacement.replace(original)
        self.manifest['sha256'][staging.BROWSER] = staging.digest(original)
        staging.write_json(self.inputs / staging.PUBLICATION, self.manifest)
        self.request['browserArchiveSha256'] = staging.digest(original)
        self.request['publicationManifestSha256'] = staging.digest(self.inputs / staging.PUBLICATION)
        staging.write_json(self.inputs / staging.REQUEST, self.request)
        with self.assertRaisesRegex(ValueError, 'unlisted runtime files'):
            staging.prepare(self.inputs, ROOT, self.website, CODE)
        self.assertFalse((self.website / 'play').exists())

    def test_archive_paths_links_duplicates_and_special_files_are_refused(self):
        for index, (name, kind) in enumerate((('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                                            ('link', tarfile.SYMTYPE), ('fifo', tarfile.FIFOTYPE))):
            with self.subTest(name=name):
                archive = self.root / 'unsafe.tar'
                with tarfile.open(archive, 'w') as output:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = '../escape'
                    output.addfile(member, io.BytesIO())
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    staging.extract(archive, self.root / ('unpacked-' + str(index)))
        archive = self.root / 'duplicate.tar'
        with tarfile.open(archive, 'w') as output:
            output.addfile(tarfile.TarInfo('same'), io.BytesIO())
            output.addfile(tarfile.TarInfo('same'), io.BytesIO())
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            staging.extract(archive, self.root / 'duplicate')

    def api_fixture(self, path):
        if path == 'releases/tags/' + self.tag:
            return dict(tag_name=self.tag, draft=False, prerelease=True, assets=[
                dict(name=name, size=(self.inputs / name).stat().st_size, digest='sha256:' + staging.digest(self.inputs / name))
                for name in (staging.PUBLICATION, staging.BROWSER, staging.SERVICE)])
        if path == 'git/ref/tags/' + self.tag:
            return {'object': {'type': 'commit', 'sha': SHA}}
        if path.startswith('compare/'):
            return {'status': 'ahead'}
        qualification = '/11/' in path
        if path.endswith('/jobs?per_page=100'):
            names = staging.CI_JOBS if qualification else staging.CI_JOBS | {'dev-release'}
            return {'jobs': [dict(name=name, conclusion='success') for name in names]}
        return dict(status='completed', conclusion='success', path='.github/workflows/build.yml',
                    run_attempt=2 if qualification else 1, repository={'full_name': staging.REPOSITORY},
                    event='push' if qualification else 'schedule', head_sha=SHA if qualification else CONTROL,
                    head_branch='integrate/feature-batch' if qualification else 'main')

    def test_verified_frozen_downloads_are_reused_with_source_and_producer_receipts(self):
        with patch.object(staging, 'api', side_effect=self.api_fixture), \
             patch.object(staging.urllib.request, 'urlopen') as download:
            staging.fetch(self.request_path, self.inputs, CODE)
        download.assert_not_called()
        self.assertTrue((self.inputs / 'release.json').exists())
        self.assertEqual(staging.verify_inputs(self.inputs, CODE), self.manifest)

    def test_partial_or_red_producer_cannot_be_called_green(self):
        def response(path):
            value = copy.deepcopy(self.api_fixture(path))
            if path.endswith('/jobs?per_page=100') and '/41/' in path:
                value['jobs'] = [job for job in value['jobs'] if job['name'] != 'build-macos']
            return value
        with patch.object(staging, 'api', side_effect=response), self.assertRaisesRegex(ValueError, 'missing or failed'):
            staging.fetch(self.request_path, self.inputs, CODE)

    @unittest.skipUnless(shutil.which('node'), 'Node is required to execute the browser bootstrap')
    def test_bootstrap_overrides_production_and_loopback_query_endpoints(self):
        staging.prepare(self.inputs, ROOT, self.website, CODE)
        script = self.website / 'play-staging/staging-bootstrap.js'
        program = "global.window={location:{href:'https://dunelegacy.com/play-staging/?relay=https://dunelegacy.com/p2p&relaydev=1&other=kept'},history:{replaceState:(a,b,u)=>console.log(String(u))}};require(process.argv[1]);"
        url = subprocess.check_output(['node', '-e', program, str(script)], text=True).strip()
        self.assertIn('relay=https%3A%2F%2Fdunelegacy.com%2Fplay-staging%2Fp2p', url)
        self.assertNotIn('relaydev', url)
        self.assertIn('other=kept', url)

    @unittest.skipUnless(shutil.which('php'), 'PHP is required to validate the installed private config')
    def test_staging_service_uses_separate_code_state_origin_and_protocol_config(self):
        staging.prepare(self.inputs, ROOT, self.website, CODE)
        private = self.root / 'private-staging'
        staging.install_service(self.website.parent / 'p2p-staging', ROOT, private)
        config = json.loads(subprocess.check_output(['php', '-r', 'echo json_encode(require $argv[1]);', str(private / 'config.php')], text=True))
        self.assertEqual(config['state_dir'], str(private / 'run/state'))
        self.assertEqual(config['base_path'], '/play-staging/p2p')
        self.assertEqual(config['public_base_url'], 'https://dunelegacy.com/play-staging/p2p')
        self.assertEqual(json.loads((private / 'current/SOURCE.json').read_text())['commit'], SHA)
        self.assertFalse((self.root / 'dunecity-p2p').exists())


if __name__ == '__main__':
    unittest.main()
