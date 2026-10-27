import copy
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_runner as runner

SHA = 'a' * 64

def job(phase='final'):
    return {'id': 'job_1', 'revision': 1, 'phase': phase, 'campaign_id': 'campaign',
            'family': 'PODCAST', 'run_id': '123', 'asset_ids': [SHA],
            'edit': {'template': 'vertical-v1', 'clips': [{'asset_id': SHA, 'start_s': 1, 'end_s': 4}],
                     'title': 'Safe title', 'subtitles': [{'start_s': 0, 'end_s': 2, 'text': 'Hello'}]}}

def test_job_valid_and_rejects_executable_unknown_inputs():
    assert runner.validate_job(job())['phase'] == 'final'
    for key, value in [('project', '../foo'), ('packages', ['evil']), ('code', 'process.exit()')]:
        data = job(); data['edit'][key] = value
        with pytest.raises(ValueError): runner.validate_job(data)
    for family in ['PRODUCT', 'unknown']:
        data = job(); data['family'] = family
        with pytest.raises(ValueError): runner.validate_job(data)

@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, -1, 7201])
def test_bounded_numbers(value):
    data = job(); data['edit']['clips'][0]['start_s'] = value
    with pytest.raises(ValueError): runner.validate_job(data)

def test_unknown_asset_and_oversized_timeline_fail_closed():
    data = job(); data['edit']['clips'][0]['asset_id'] = 'b' * 64
    with pytest.raises(ValueError): runner.validate_job(data)
    data = job(); data['edit']['clips'][0]['end_s'] = 200
    with pytest.raises(ValueError): runner.validate_job(data)


def test_template_escapes_markup_and_uses_only_trusted_local_assets(tmp_path):
    data = job(); data['edit']['title'] = '<import from="evil"/> {evil}'
    runner.write_template(data, tmp_path)
    xml = (tmp_path / 'project.svml').read_text()
    assert '<import from="evil"' not in xml
    assert '&lt;import' in xml
    assert '<html:Video id="final"' in xml
    assert '<visual:Clip' in xml and '<audio:Clip' in xml and '<typo:Flow' in xml
    assert '<target output="final.video"' in (tmp_path / 'build.svrun').read_text()


def test_actual_build_id_requires_complete_exact_cli_result():
    actual = {'format': 'hypit.cli-build@1', 'build': {'id': 'build-actual', 'work': {'state': 'done', 'outcome': 'complete'}}}
    assert runner.parse_build(json.dumps(actual)) == 'build-actual'
    actual['build']['work']['outcome'] = 'failed'
    with pytest.raises(ValueError): runner.parse_build(json.dumps(actual))
    with pytest.raises(ValueError): runner.parse_build('{"builds":[{"id":"latest"}]}')


def test_qa_rejects_silent_audio_or_wrong_dimensions():
    probe = {'format': {'duration': '3'}, 'streams': [
        {'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920, 'r_frame_rate': '30/1'},
        {'codec_type': 'audio', 'codec_name': 'aac', 'duration': '3'}]}
    assert runner.validate_probe(probe, 'final', 3)['audio_present']
    probe['streams'][0]['width'] = 540
    with pytest.raises(ValueError): runner.validate_probe(probe, 'final', 3)
    with pytest.raises(ValueError): runner.parse_audio_metrics('I: -inf LUFS\nPeak: -inf dBFS')
    assert runner.parse_audio_metrics('I: -15.0 LUFS\nPeak: -1.5 dBFS') == (-15.0, -1.5)


def test_source_hash_validation_streaming(tmp_path):
    target = tmp_path / 'asset'
    target.write_bytes(b'abc')
    assert runner.hash_file(target) == hashlib.sha256(b'abc').hexdigest()


def test_workflow_has_no_untrusted_code_or_release_or_write_permissions():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/render.yml').read_text()
    assert 'id-token: write' in workflow
    assert 'contents: read' in workflow and 'contents: write' not in workflow
    assert 'release_tag' not in workflow and 'inputs.project' not in workflow
    assert '--frozen-lockfile' in workflow
    assert runner.HYPIT_SHA in workflow
    assert 'cancel-in-progress: false' in workflow


def test_dispatch_uses_exact_200_and_no_latest_fallback():
    import render_cloud
    from types import SimpleNamespace
    payload = {'workflow_run_id': 123, 'run_url': 'https://api.github.com/repos/owner/repo/actions/runs/123',
               'html_url': 'https://github.com/owner/repo/actions/runs/123'}
    result = SimpleNamespace(returncode=0, stdout='HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n' + json.dumps(payload))
    with patch('render_cloud.subprocess.run', return_value=result) as call:
        assert render_cloud.dispatch_job('owner/repo', 'refs/heads/approved', 'job_1') == payload
    command = call.call_args.args[0]
    assert 'X-GitHub-Api-Version: 2026-03-10' in command
    assert json.loads(call.call_args.kwargs['input']) == {'ref': 'refs/heads/approved', 'inputs': {'job_id': 'job_1'}}
    for output in ['HTTP/2.0 204 No Content\n\n', 'HTTP/2.0 200 OK\n\n{}']:
        with patch('render_cloud.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=output)):
            with pytest.raises(RuntimeError, match='DISPATCH_UNKNOWN'):
                render_cloud.dispatch_job('owner/repo', 'refs/heads/approved', 'job_1')


def test_authenticated_origin_and_redirects_fail_closed():
    for origin in ['http://worker.example', 'https://u:p@worker.example', 'https://worker.example/evil', 'https://worker.example?a=b']:
        with pytest.raises(ValueError): runner.RunnerClient(origin, 'job_1')
    with pytest.raises(ValueError): runner.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')


def test_source_descriptor_and_exact_run_binding_fail_closed(monkeypatch):
    import io
    client = runner.RunnerClient('https://worker.example', 'job_1')
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    payload = {'job': job(), 'assets': [{'asset_id': SHA, 'sha256': SHA, 'size': 10, 'mime': 'video/mp4'}]}
    with patch.object(client, 'request', return_value=io.BytesIO(json.dumps(payload).encode())):
        assert client.job()[0]['id'] == 'job_1'
    payload['job']['run_id'] = '999'
    with patch.object(client, 'request', return_value=io.BytesIO(json.dumps(payload).encode())):
        with pytest.raises(ValueError, match='binding mismatch'): client.job()


def test_legacy_script_contains_no_mutating_git_or_releases():
    source = (Path(__file__).resolve().parents[1] / 'scripts/render_cloud.py').read_text()
    for forbidden in ['"git", "add"', '"git", "push"', '"git", "commit"', '"gh", "release", "create"', '"gh", "run", "list"']:
        assert forbidden not in source


@pytest.mark.parametrize('content,size,sha', [(b'overrun', 1, SHA), (b'a', 2, SHA), (b'wrong', 5, SHA)])
def test_download_verifies_size_and_sha_and_removes_failed_partial(tmp_path, content, size, sha):
    import io
    client = runner.RunnerClient('https://worker.example', 'job_1')
    target = tmp_path / 'source'
    with patch.object(client, 'request', return_value=io.BytesIO(content)):
        with pytest.raises(ValueError): client.download({'asset_id': SHA, 'sha256': sha, 'size': size}, target)
    assert not target.exists()


def test_upload_uses_stream_hash_and_checks_server_receipt(tmp_path):
    import io
    client = runner.RunnerClient('https://worker.example', 'job_1')
    artifact = tmp_path / 'final.mp4'
    artifact.write_bytes(b'abc')
    receipt = {'object_key': 'jobs/job_1/final', 'sha256': runner.hash_file(artifact), 'size': 3, 'kind': 'final', 'mime': 'video/mp4'}
    with patch.object(client, 'request', return_value=io.BytesIO(json.dumps(receipt).encode())) as request:
        assert client.upload(artifact, 'final') == receipt
    assert request.call_args.kwargs['headers']['X-Content-SHA256'] == receipt['sha256']
    assert request.call_args.kwargs['headers']['Content-Length'] == '3'
    receipt['sha256'] = SHA
    with patch.object(client, 'request', return_value=io.BytesIO(json.dumps(receipt).encode())):
        with pytest.raises(ValueError): client.upload(artifact, 'final')


def test_actual_qa_runs_full_decode_and_binds_artifact_hash(tmp_path):
    target = tmp_path / 'final.mp4'; target.write_bytes(b'media')
    metadata = {'format': {'duration': '3'}, 'streams': [
        {'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920, 'r_frame_rate': '30/1'},
        {'codec_type': 'audio', 'codec_name': 'aac', 'duration': '3'}]}
    with patch.object(runner, 'probe', return_value=metadata), patch.object(runner, 'run', return_value=('', 'I: -15.0 LUFS\nPeak: -1.5 dBFS')) as command:
        qa = runner.artifact_qa(target, job())
    assert qa['passed'] and qa['full_decode'] and qa['artifact_sha256'] == runner.hash_file(target)
    assert '-xerror' in command.call_args.args[0]
    assert '-t' not in command.call_args.args[0]
    assert '-protocol_whitelist' in command.call_args.args[0]
    with patch.object(runner, 'probe', return_value=metadata), patch.object(runner, 'run', return_value=('', 'I: -15.0 LUFS\nPeak: -0.5 dBFS')):
        with pytest.raises(ValueError): runner.artifact_qa(target, job())


def test_native_hypit_template_compiles_when_explicitly_enabled(tmp_path):
    import os
    import subprocess
    engine = os.environ.get('HYPIT_TEMPLATE_CHECK_ROOT')
    if not engine:
        pytest.skip('Set HYPIT_TEMPLATE_CHECK_ROOT for compiler-only validation, never a render')
    data = job(); data['edit']['clips'].append({'asset_id': SHA, 'start_s': 5, 'end_s': 7})
    data['edit']['music_asset_id'] = SHA
    data['edit']['title'] = '<import from="evil"/> {not-a-reference}'
    runner.write_template(data, tmp_path)
    (tmp_path / 'assets').mkdir()
    for name in ('clip-0.mp4', 'clip-0.wav', 'clip-1.mp4', 'clip-1.wav', 'music.wav'):
        (tmp_path / 'assets' / name).touch()
    result = subprocess.run(['node', str(Path(engine) / 'bin/hypit.mjs'), 'check', 'project.svml', '--asset-root', str(tmp_path / 'assets'), '--json'],
        cwd=tmp_path, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)['ok'] is True


def test_dispatch_timeout_is_unknown_and_not_retried():
    import render_cloud
    import subprocess
    with patch('render_cloud.subprocess.run', side_effect=subprocess.TimeoutExpired('gh', 60)) as request:
        with pytest.raises(RuntimeError, match='DISPATCH_UNKNOWN'):
            render_cloud.dispatch_job('owner/repo', 'refs/heads/approved', 'job_1')
    assert request.call_count == 1


def test_success_ack_timeout_never_emits_conflicting_failure(tmp_path, monkeypatch):
    data = job(); data['phase'] = 'analysis'
    delivered = []
    class Client:
        def __init__(self, *args): pass
        def job(self): return data, []
        def upload(self, path, kind): return {'kind': kind}
        def result(self, payload):
            delivered.append(payload['status'])
            raise TimeoutError('ACK lost after accepting success')
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    monkeypatch.setattr(runner, 'RunnerClient', Client)
    monkeypatch.setattr(sys, 'argv', ['runner', '--job-id', 'job_1'])
    with pytest.raises(RuntimeError, match='RESULT_DELIVERY_UNKNOWN'):
        runner.main()
    assert delivered == ['SUCCEEDED']


@pytest.mark.parametrize('kind,limit', [('final',95_000_000),('preview',20_000_000),('qa',1_000_000),('analysis',1_000_000)])
def test_artifact_exceeding_free_ingress_bound_is_rejected_before_hash_or_request(tmp_path, kind, limit):
    from types import SimpleNamespace
    artifact = tmp_path / 'artifact'; artifact.write_bytes(b'a')
    client = runner.RunnerClient('https://worker.example', 'job_1')
    with patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=limit+1)), patch.object(runner,'hash_file') as hashed, patch.object(client,'request') as request:
        with pytest.raises(ValueError, match='Artifact size'):
            client.upload(artifact,kind)
    hashed.assert_not_called(); request.assert_not_called()


@pytest.mark.parametrize('phase,bitrate',[('preview','600k'),('final','3800k')])
def test_master_encoder_bounds_transport_size_without_truncating_duration(tmp_path,phase,bitrate):
    measured = json.dumps({'input_i':-15,'input_tp':-2,'input_lra':5,'input_thresh':-25,'target_offset':0})
    with patch.object(runner,'run',return_value=('',measured)) as command:
        runner.master_audio(tmp_path/'source',tmp_path/'dest',phase=phase)
    args=command.call_args.args[0]
    assert args[args.index('-c:v')+1]=='libx264'
    assert args[args.index('-b:v')+1]==bitrate
    assert '-maxrate' in args and '-bufsize' in args
    assert '-fs' not in args and '-t' not in args
