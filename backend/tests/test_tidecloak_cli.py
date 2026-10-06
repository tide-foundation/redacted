"""Terminal discovery tolerates app restarts without replaying setup writes."""
import importlib.util
from pathlib import Path
import urllib.error
from types import SimpleNamespace

import pytest


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location('tidecloak_cli', Path(__file__).resolve().parents[2] / 'scripts/tidecloak.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discovery_recovers_after_connection_reset_and_temporary_unavailability(cli, monkeypatch, capsys):
    responses = iter([ConnectionResetError(104, 'Connection reset by peer'),
                      urllib.error.HTTPError('http://localhost:3001', 503, 'Starting', {}, None),
                      {'configured': False, 'reachable': True}])
    calls = []
    def call(origin, path, body=None, *, timeout=20):
        calls.append((path, body, timeout))
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(cli, 'call', call)
    monkeypatch.setattr(cli.time, 'sleep', lambda seconds: None)
    assert cli.wait_for_app('http://localhost:3001') == {'configured': False, 'reachable': True}
    assert len(calls) == 3
    assert all(path == 'tide/status' and body is None and timeout <= 5 for path, body, timeout in calls)
    assert capsys.readouterr().out.count('Waiting for Redacted') == 1


def test_discovery_stops_at_deadline_with_actionable_message(cli, monkeypatch):
    elapsed = [0]
    monkeypatch.setattr(cli.time, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(cli.time, 'sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    def offline(*args, **kwargs):
        raise urllib.error.URLError('Connection refused')
    monkeypatch.setattr(cli, 'call', offline)
    with pytest.raises(RuntimeError, match='docker compose up -d app'):
        cli.wait_for_app('http://localhost:3001', timeout=3)
    assert elapsed[0] == 3


def test_discovery_does_not_retry_permanent_http_errors(cli, monkeypatch):
    def forbidden(*args, **kwargs):
        raise urllib.error.HTTPError('http://localhost:3001', 403, 'Forbidden', {}, None)
    monkeypatch.setattr(cli, 'call', forbidden)
    monkeypatch.setattr(cli.time, 'sleep', lambda seconds: pytest.fail('Permanent errors must not be retried'))
    with pytest.raises(urllib.error.HTTPError) as error:
        cli.wait_for_app('http://localhost:3001')
    assert error.value.code == 403


def test_interrupted_handoff_is_not_replayed_and_removes_permit(cli, monkeypatch, tmp_path):
    calls = []
    def interrupted(origin, path, body=None):
        calls.append(path)
        assert (tmp_path / 'tide/launch-permit.json').exists()
        raise ConnectionResetError(104, 'Connection reset by peer')
    monkeypatch.setattr(cli, 'call', interrupted)
    with pytest.raises(ConnectionResetError):
        cli.handoff('http://localhost:3001', tmp_path, 'owner', 'test-password', open_browser=False)
    assert calls == ['tide/setup/v2/launch']
    assert not (tmp_path / 'tide/launch-permit.json').exists()


@pytest.mark.parametrize('platform,expected', [('darwin', 'open'), ('linux', '/usr/bin/xdg-open')])
def test_native_launch_passes_url_as_one_argument(cli, monkeypatch, platform, expected):
    monkeypatch.delenv('WSL_DISTRO_NAME', raising=False)
    monkeypatch.setattr(cli.Path, 'read_text', lambda self: 'generic')
    monkeypatch.setattr(cli.sys, 'platform', platform)
    monkeypatch.setattr(cli.shutil, 'which', lambda name: '/usr/bin/xdg-open' if name == 'xdg-open' else None)
    calls = []
    monkeypatch.setattr(cli.subprocess, 'run', lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(returncode=0))
    url = 'http://localhost:3001/secure-history/setup#setup=private&token'
    assert cli.open_setup_browser(url)
    assert calls[0][0] == [expected, url]
    assert not calls[0][1].get('shell')


def test_wsl_falls_back_to_windows_without_interpolating_private_url(cli, monkeypatch):
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Debian')
    monkeypatch.setattr(cli.shutil, 'which', lambda name: {'wslview': '/usr/bin/wslview', 'powershell.exe': '/windows/powershell.exe'}.get(name))
    calls = []
    def run(args, **kw):
        calls.append((args, kw))
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)
    monkeypatch.setattr(cli.subprocess, 'run', run)
    url = 'http://localhost:3001/secure-history/setup#setup=private&token'
    assert cli.open_setup_browser(url)
    assert calls[0][0] == ['/usr/bin/wslview', url]
    assert calls[1][0][0] == '/windows/powershell.exe'
    assert url not in ' '.join(calls[1][0])
    assert calls[1][1]['input'] == url
    assert not calls[1][1].get('shell')


@pytest.mark.parametrize('opened', [True, False])
def test_handoff_always_prints_complete_link_and_manual_instructions_on_failure(cli, monkeypatch, tmp_path, capsys, opened):
    url = 'http://localhost:3001/secure-history/setup#setup=private-test-link'
    monkeypatch.setattr(cli, 'call', lambda *a, **kw: {'url': url})
    monkeypatch.setattr(cli, 'open_setup_browser', lambda link: opened)
    assert cli.handoff('http://localhost:3001', tmp_path, 'owner', 'test-password') == url
    output = capsys.readouterr().out
    assert url in output and 'one hour' in output
    assert ('paste the complete private setup link' in output) is not opened
    assert 'test-password' not in output


def test_windows_uses_default_browser_and_reports_failure(cli, monkeypatch):
    monkeypatch.delenv('WSL_DISTRO_NAME', raising=False)
    monkeypatch.setattr(cli.Path, 'read_text', lambda self: 'generic')
    monkeypatch.setattr(cli.sys, 'platform', 'win32')
    urls = []
    monkeypatch.setattr(cli.webbrowser, 'open', lambda url: urls.append(url) or False)
    assert not cli.open_setup_browser('http://localhost:3001/')
    assert urls == ['http://localhost:3001/']


def test_launch_timeout_becomes_manual_fallback(cli, monkeypatch):
    monkeypatch.delenv('WSL_DISTRO_NAME', raising=False)
    monkeypatch.setattr(cli.Path, 'read_text', lambda self: 'generic')
    monkeypatch.setattr(cli.sys, 'platform', 'linux')
    monkeypatch.setattr(cli.shutil, 'which', lambda name: '/usr/bin/xdg-open')
    def timeout(args, **kw): raise cli.subprocess.TimeoutExpired(args, kw['timeout'])
    monkeypatch.setattr(cli.subprocess, 'run', timeout)
    assert not cli.open_setup_browser('http://localhost:3001/')


def test_container_handoff_prints_host_port_and_cleans_permit(cli, monkeypatch, tmp_path, capsys):
    def launch(origin, path, body=None):
        assert origin == 'http://127.0.0.1:8000'
        assert body['password'] == 'private-owner-password'
        assert (tmp_path / 'tide/launch-permit.json').exists()
        return {'url': origin + '/secure-history/setup#setup=private-token'}
    monkeypatch.setattr(cli, 'call', launch)
    link = cli.handoff('http://127.0.0.1:8000', tmp_path, 'owner',
                       'private-owner-password', open_browser=False,
                       public_origin='http://localhost:3099')
    assert link == 'http://localhost:3099/secure-history/setup#setup=private-token'
    output = capsys.readouterr().out
    assert link in output
    assert '8000' not in output
    assert 'private-owner-password' not in output
    assert not (tmp_path / 'tide/launch-permit.json').exists()


@pytest.mark.parametrize('input_format', ['env', 'container-env'])
def test_container_helper_reads_credentials_from_stdin(cli, monkeypatch, input_format, tmp_path):
    import io
    import json
    import sys
    monkeypatch.setitem(sys.modules, 'tidecloak', cli)
    spec = importlib.util.spec_from_file_location('container_cli', Path(__file__).resolve().parents[2] / 'scripts/tidecloak-container.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entries = ['KC_BOOTSTRAP_ADMIN_USERNAME=owner', 'KC_BOOTSTRAP_ADMIN_PASSWORD=secret=with=equals']
    source = '\n'.join(entries) if input_format == 'env' else json.dumps([{'Config': {'Env': entries}}])
    monkeypatch.setattr(sys, 'argv', ['helper', input_format])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(source))
    monkeypatch.setenv('REDACTED_PUBLIC_URL', 'http://localhost:3099')
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(module, 'wait_for_app', lambda origin: {'configured': False})
    calls = []
    monkeypatch.setattr(module, 'handoff', lambda *args, **kwargs: calls.append((args, kwargs)))
    module.main()
    assert calls == [(('http://127.0.0.1:8000', tmp_path, 'owner', 'secret=with=equals'),
                      {'open_browser': False, 'public_origin': 'http://localhost:3099'})]


@pytest.mark.parametrize('kind', ['managed', 'external', 'external-configured'])
def test_single_start_command_selects_existing_installation(tmp_path, kind):
    import os
    import shutil
    import subprocess
    scripts = tmp_path / 'scripts'; scripts.mkdir()
    shutil.copy(Path(__file__).resolve().parents[2] / 'scripts/tidecloak.sh', scripts / 'tidecloak.sh')
    private = tmp_path / '.tidecloak'; private.mkdir()
    (private / 'bootstrap.env').write_text('KC_BOOTSTRAP_ADMIN_USERNAME=owner\nKC_BOOTSTRAP_ADMIN_PASSWORD=fixture\n')
    binary = tmp_path / 'bin'; binary.mkdir()
    docker = binary / 'docker'
    docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_LOG"
case "$*" in
  *' status') printf '%s\\n' "$TEST_KIND" ;;
  *' env')
    if [ "$TEST_KIND" != external-configured ]; then
      body=$(cat)
      case "$body" in *KC_BOOTSTRAP_ADMIN_PASSWORD=fixture*) ;; *) exit 4 ;; esac
    fi
    printf 'http://localhost:3001/secure-history/setup\\n' ;;
esac
''')
    docker.chmod(0o755)
    log = tmp_path / 'commands'
    result = subprocess.run(['bash', str(scripts / 'tidecloak.sh'), 'start', '--no-browser'],
                            input='owner\nfixture\n', text=True, capture_output=True,
                            env={**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH'],
                                 'TEST_LOG': str(log), 'TEST_KIND': kind})
    assert result.returncode == 0, result.stderr
    assert ('up -d tidecloak' in log.read_text()) == (kind == 'managed')
    assert 'fixture' not in log.read_text() + result.stdout
    assert 'http://localhost:3001/secure-history/setup' in result.stdout
