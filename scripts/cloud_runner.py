#!/usr/bin/env python3
"""Trusted GitHub runner. No user programs, external URLs or publishing credentials."""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

HYPIT_SHA = '1efedb925d375686ab107b6e67f914dced7b102b'
FAMILIES = {'STORYTELLING', 'PODCAST', 'JJ'}
MAX_ASSET_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_DURATION = 180
SHA_RE = re.compile(r'^[a-f0-9]{64}$')
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
MEDIA_INPUT = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm,wav,mp3,ogg,flac']


def number(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError('Numeric bound violation')
    return value


def text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('Text bound violation')
    return value


def validate_job(job):
    if not isinstance(job, dict) or not ID_RE.fullmatch(str(job.get('id', ''))):
        raise ValueError('Invalid job ID')
    if job.get('phase') not in {'analysis', 'preview', 'final'} or job.get('family') not in FAMILIES:
        raise ValueError('Invalid phase/family')
    if type(job.get('revision')) is not int or job['revision'] < 1:
        raise ValueError('Invalid revision')
    assets = job.get('asset_ids')
    if not isinstance(assets, list) or not 1 <= len(assets) <= 16 or len(set(assets)) != len(assets) or any(not isinstance(a, str) or not SHA_RE.fullmatch(a) for a in assets):
        raise ValueError('Invalid asset list')
    edit = job.get('edit')
    if not isinstance(edit, dict) or set(edit) - {'template', 'clips', 'title', 'subtitles', 'music_asset_id'} or edit.get('template') != 'vertical-v1':
        raise ValueError('Only trusted vertical-v1 bounded parameters are accepted')
    clips = edit.get('clips')
    if not isinstance(clips, list) or not 1 <= len(clips) <= 24:
        raise ValueError('Invalid clips')
    duration = 0
    for clip in clips:
        if not isinstance(clip, dict) or set(clip) != {'asset_id', 'start_s', 'end_s'} or clip['asset_id'] not in assets:
            raise ValueError('Invalid clip asset/shape')
        start, end = number(clip['start_s'], 0, 7200), number(clip['end_s'], 0, 7200)
        if end <= start or end - start < 0.1:
            raise ValueError('Invalid clip range')
        duration += end - start
    number(duration, 0.1, MAX_DURATION)
    text(edit.get('title'), 160)
    subtitles = edit.get('subtitles')
    if not isinstance(subtitles, list) or len(subtitles) > 300:
        raise ValueError('Invalid subtitles')
    previous_end = 0
    for cue in subtitles:
        if not isinstance(cue, dict) or set(cue) != {'start_s', 'end_s', 'text'}:
            raise ValueError('Invalid cue shape')
        start, end = number(cue['start_s'], 0, duration), number(cue['end_s'], 0, duration)
        if start < previous_end or end <= start:
            raise ValueError('Overlapping/invalid subtitles')
        previous_end = end
        text(cue['text'], 240)
    if edit.get('music_asset_id') is not None and edit['music_asset_id'] not in assets:
        raise ValueError('Music must be a job asset')
    return job


def duration_of(job):
    return sum(c['end_s'] - c['start_s'] for c in job['edit']['clips'])


def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Authenticated redirects forbidden')


class RunnerClient:
    def __init__(self, origin, job_id):
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
            raise ValueError('Trusted API origin must be an HTTPS origin')
        if not ID_RE.fullmatch(job_id):
            raise ValueError('Invalid job id')
        self.origin, self.job_id = origin.rstrip('/'), job_id
        self.opener = urllib.request.build_opener(NoRedirect)

    def oidc(self):
        # Mint fresh short-lived identity for each request, including after long renders.
        url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https':
            raise ValueError('GitHub OIDC transport must be HTTPS')
        url += ('&' if parsed.query else '?') + 'audience=clipper-render'
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
        with self.opener.open(request, timeout=60) as response:
            token = json.load(response)['value']
        if not isinstance(token, str) or not token:
            raise ValueError('GitHub OIDC token missing')
        return token

    def request(self, path, *, method='GET', data=None, headers=None):
        request = urllib.request.Request(self.origin + path, method=method, data=data,
            headers={'Authorization': 'Bearer ' + self.oidc(), **(headers or {})})
        return self.opener.open(request, timeout=180)

    def job(self):
        with self.request('/runner/jobs/' + self.job_id) as response:
            payload = json.load(response)
        job = validate_job(payload['job'])
        if job['id'] != self.job_id or str(job.get('run_id')) != os.environ['GITHUB_RUN_ID']:
            raise ValueError('Exact dispatched run/job binding mismatch')
        assets = payload['assets']
        if not isinstance(assets, list) or len(assets) != len(job['asset_ids']):
            raise ValueError('Incomplete asset descriptors')
        seen, total = set(), 0
        for descriptor in assets:
            asset_id = descriptor.get('asset_id')
            if asset_id not in job['asset_ids'] or asset_id in seen or descriptor.get('sha256') != asset_id:
                raise ValueError('Asset descriptor mismatch')
            seen.add(asset_id)
            size = descriptor.get('size')
            if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
                raise ValueError('Source exceeds per-asset bound')
            total += size
            if descriptor.get('mime') not in {'video/mp4', 'video/quicktime', 'video/webm', 'video/x-matroska', 'audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/x-wav', 'audio/ogg', 'audio/flac'}:
                raise ValueError('Unsupported source MIME')
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Sources exceed total runner bound')
        return job, assets

    def download(self, descriptor, destination):
        path = '/runner/assets/' + descriptor['asset_id'] + '?job_id=' + self.job_id
        digest, size = hashlib.sha256(), 0
        try:
            with self.request(path) as response, destination.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > descriptor['size']:
                        raise ValueError('Source stream exceeds descriptor')
                    digest.update(chunk)
                    output.write(chunk)
            if size != descriptor['size'] or digest.hexdigest() != descriptor['sha256']:
                raise ValueError('Source integrity mismatch')
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def upload(self, path, kind):
        mime = 'video/mp4' if kind in {'preview', 'final'} else 'application/json'
        size = path.stat().st_size
        limits = {'final': 95_000_000, 'preview': 20_000_000, 'qa': 1_000_000, 'analysis': 1_000_000}
        if kind not in limits or not 0 < size <= limits[kind]:
            raise ValueError('Artifact size out of bounds')
        sha = hash_file(path)
        with path.open('rb') as content:
            with self.request(f'/runner/jobs/{self.job_id}/artifacts/{kind}', method='PUT', data=content,
                headers={'Content-Type': mime, 'Content-Length': str(size), 'X-Content-SHA256': sha}) as response:
                receipt = json.load(response)
        if any(receipt.get(k) != v for k, v in {'sha256': sha, 'size': size, 'kind': kind, 'mime': mime}.items()) or not receipt.get('object_key'):
            raise ValueError('Server receipt mismatch')
        return receipt

    def result(self, payload):
        data = json.dumps(payload, allow_nan=False).encode()
        with self.request('/runner/jobs/' + self.job_id + '/result', method='POST', data=data,
            headers={'Content-Type': 'application/json'}) as response:
            json.load(response)


def run(command, cwd=None, timeout=3600):
    # Deliberately do not echo potentially credential-bearing process environment/stderr.
    result = subprocess.run([str(v) for v in command], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Trusted media command failed: ' + Path(str(command[0])).name)
    return result.stdout, result.stderr


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', *MEDIA_INPUT, '-show_format', '-show_streams', '-of', 'json', path], timeout=60)[0])


def source_analysis(path):
    metadata = probe(path)
    duration = float(metadata['format']['duration'])
    number(duration, 0.1, 7200)
    evidence = {'duration_s': duration, 'streams': [{k: s[k] for k in ('codec_type', 'codec_name', 'width', 'height', 'sample_rate') if k in s} for s in metadata['streams']],
        'semantic_analysis': 'NOT_PERFORMED', 'transcription': 'NOT_PERFORMED'}
    if any(s.get('codec_type') == 'video' for s in metadata['streams']):
        _, log = run(['ffmpeg', '-hide_banner', '-nostdin', *MEDIA_INPUT, '-i', path, '-an', '-vf', "select='gt(scene,0.35)',showinfo", '-f', 'null', '-'])
        evidence['scene_change_times_s'] = [float(v) for v in re.findall(r'pts_time:([\d.]+)', log)][:1000]
    if any(s.get('codec_type') == 'audio' for s in metadata['streams']):
        _, log = run(['ffmpeg', '-hide_banner', '-nostdin', *MEDIA_INPUT, '-i', path, '-vn', '-af', 'silencedetect=noise=-35dB:d=0.4', '-f', 'null', '-'])
        evidence['silence_events'] = [{'event': event, 'time_s': float(t)} for event, t in re.findall(r'silence_(start|end): ([\d.]+)', log)][:2000]
    return evidence


def prepare_assets(job, directory):
    width, height = (540, 960) if job['phase'] == 'preview' else (1080, 1920)
    for index, clip in enumerate(job['edit']['clips']):
        source = directory / 'assets' / clip['asset_id']
        metadata = probe(source)
        duration = float(metadata['format']['duration'])
        if clip['end_s'] > duration + 0.05 or not any(s.get('codec_type') == 'video' for s in metadata['streams']):
            raise ValueError('Source range/video stream invalid')
        length = clip['end_s'] - clip['start_s']
        run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-ss', clip['start_s'], *MEDIA_INPUT, '-i', source, '-t', length,
             '-an', '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30',
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', directory / 'assets' / f'clip-{index}.mp4'])
        if any(s.get('codec_type') == 'audio' for s in metadata['streams']):
            run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-ss', clip['start_s'], *MEDIA_INPUT, '-i', source, '-t', length,
                 '-vn', '-af', f'aresample=48000,apad=whole_dur={length}', '-ac', '2', '-c:a', 'pcm_s16le', directory / 'assets' / f'clip-{index}.wav'])
        else:
            raise ValueError('Source clip has no audio; operator must provide an audible source')
    music = job['edit'].get('music_asset_id')
    if music:
        run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-stream_loop', '-1', *MEDIA_INPUT, '-i', directory / 'assets' / music,
             '-t', duration_of(job), '-vn', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', directory / 'assets/music.wav'])


def write_template(job, directory):
    """Only fixed native Hypit elements; parameters are text/numbers/verified assets."""
    validate_job(job)
    width, height = (540, 960) if job['phase'] == 'preview' else (1080, 1920)
    duration = duration_of(job)
    xml = ['<?svml using="@hypit/markup@1"?>', '<svml>']
    for name, package in [('time', 'timeline-author'), ('space', 'spatial'), ('media', 'media'), ('mediaop', 'media-operations'), ('visual', 'visual-track'), ('audio', 'audio-track'), ('fonts', 'fontsource'), ('typo', 'text-fine'), ('film', 'film'), ('html', 'html-video')]:
        xml.append(f'<import as="{name}" from="@hypit/{package}@1"/>')
    xml += ['<import as="styles" source="./styles.svs"/>',
        f'<space:Canvas id="canvas" width="{width}" height="{height}"/>',
        '<space:Frame id="full" within={canvas.bounds} left="0%" top="0%" right="100%" bottom="100%"/>',
        '<space:Frame id="title-frame" within={canvas.bounds} left="8%" top="12%" right="92%" bottom="30%"/>',
        '<space:Frame id="caption-frame" within={canvas.bounds} left="8%" top="67%" right="92%" bottom="82%"/>',
        '<time:Clock id="clock" frame-rate="30"/>',
        f'<time:Timeline id="timeline" clock={{clock}} end="{duration:.6f}s">']
    offset = 0
    for index, clip in enumerate(job['edit']['clips']):
        length = clip['end_s'] - clip['start_s']
        xml.append(f'<time:Window id="clip-{index}" from="{offset:.6f}s" for="{length:.6f}s"/>')
        offset += length
    xml.append(f'<time:Window id="title" from="0s" for="{min(duration,5):.6f}s"/>')
    for index, cue in enumerate(job['edit']['subtitles']):
        xml.append(f'<time:Window id="cue-{index}" from="{cue["start_s"]:.6f}s" for="{cue["end_s"]-cue["start_s"]:.6f}s"/>')
    xml.append('</time:Timeline>')
    for index, _ in enumerate(job['edit']['clips']):
        xml += [f'<media:Video id="video-{index}" src="./assets/clip-{index}.mp4"/>',
            f'<media:Audio id="sound-{index}" src="./assets/clip-{index}.wav"/>',
            f'<mediaop:Normalize id="picture-{index}" source={{video-{index}}} clock={{clock}} video="primary-moving" audio="none" span-authority="video"/>',
            f'<mediaop:Normalize id="dialogue-{index}" source={{sound-{index}}} clock={{clock}} video="none" audio="default" span-authority="audio"/>']
    xml += ['<visual:Track id="pictures" timeline={timeline.timeline}>']
    for index, _ in enumerate(job['edit']['clips']):
        xml.append(f'<visual:Clip id="shot-{index}" media={{picture-{index}.media}} during={{timeline.clip-{index}}} frame={{full}} fit="cover" z="1"/>')
    xml += ['</visual:Track>', '<audio:Track id="dialogue" timeline={timeline.timeline}>']
    for index, _ in enumerate(job['edit']['clips']):
        xml.append(f'<audio:Clip id="speech-{index}" source={{dialogue-{index}.media}} during={{timeline.clip-{index}}} gain="1"/>')
    xml.append('</audio:Track>')
    if job['edit'].get('music_asset_id'):
        xml += ['<media:Audio id="music-asset" src="./assets/music.wav"/>',
            '<mediaop:Normalize id="music-media" source={music-asset} clock={clock} video="none" audio="default" span-authority="audio"/>',
            '<audio:Track id="music" timeline={timeline.timeline}><audio:Clip id="bgm" source={music-media.media} during={timeline.window} gain="0.10" fade-in="6f" fade-out="12f"/></audio:Track>']
    xml += ['<fonts:Face id="font" package="@fontsource-variable/montserrat" weight="800" style="normal"/>',
        f'<typo:Style id="title-style" recipe={{styles.text.title}} font={{font}}><typo:Stroke color="#000000" width="{4*width/1080}" placement="outside"/></typo:Style>',
        f'<typo:Style id="caption-style" recipe={{styles.text.caption}} font={{font}}><typo:Stroke color="#000000" width="{3*width/1080}" placement="outside"/></typo:Style>']
    def escaped(value):
        return escape(value).replace('{', '&#123;').replace('}', '&#125;')
    xml.append(f'<typo:Flow id="headline" timeline={{timeline.timeline}} during={{timeline.title}} within={{title-frame}} style={{title-style}} z="40" align="center" block-align="center">{escaped(job["edit"]["title"])}</typo:Flow>')
    for index, cue in enumerate(job['edit']['subtitles']):
        xml.append(f'<typo:Flow id="subtitle-{index}" timeline={{timeline.timeline}} during={{timeline.cue-{index}}} within={{caption-frame}} style={{caption-style}} z="50" align="center" block-align="center">{escaped(cue["text"])}</typo:Flow>')
    xml += ['<film:Film id="main" canvas={canvas.canvas} timeline={timeline.timeline} appearance={styles.film.vertical}>',
            '<film:Track source={pictures.visual}/>', '<film:Track source={dialogue.audio}/>', '<film:Track source={headline.visual}/>']
    if job['edit'].get('music_asset_id'):
        xml.append('<film:Track source={music.audio}/>')
    for index, _ in enumerate(job['edit']['subtitles']):
        xml.append(f'<film:Track source={{subtitle-{index}.visual}}/>')
    xml += ['</film:Film>', '<html:Video id="final" composition={main.composition} timeline={timeline.timeline}/>', '</svml>']
    (directory / 'package.json').write_text(json.dumps({'name': 'clipper-trusted-job', 'private': True, 'type': 'module', 'hypit': {'project': True}}))
    (directory / 'project.svml').write_text('\n'.join(xml))
    scale = width / 1080
    color = {'STORYTELLING': '#FFF500', 'PODCAST': '#FFFFFF', 'JJ': '#FF6A00'}[job['family']]
    (directory / 'styles.svs').write_text(f'''<?svml using="@hypit/recipe@1"?>
<sheet version="1">
film.vertical {{ background: #000000; }}
text.title {{ size: {64*scale}; weight: 800; line-height: 1.2; fill: {color}; }}
text.caption {{ size: {48*scale}; weight: 800; line-height: 1.2; fill: #FFFFFF; }}
</sheet>''')
    (directory / 'build.svrun').write_text('<?svml using="@hypit/markup/run@1"?>\n<svrun version="1"><author source="./project.svml"/><target output="final.video"/></svrun>\n')
    runtime = {'format': 'hypit.runtime-local@1', 'dataRoot': '.hypit/runtimes/local', 'endpoints': {
        'media.local': {'use': '@hypit/media-local', 'config': {'defaultConcurrency': 1}},
        'html.local': {'use': '@hypit/provider-html-local', 'config': {'workers': 1, 'defaultConcurrency': 1, 'quality': 'standard'}}}}
    (directory / 'hypit.runtime.json').write_text(json.dumps(runtime))


def parse_build(stdout):
    payload = json.loads(stdout)
    build = payload.get('build', {})
    if payload.get('format') != 'hypit.cli-build@1' or not isinstance(build.get('id'), str) or not build['id'] or build.get('work', {}).get('outcome') != 'complete':
        raise ValueError('Exact submitted Hypit build did not complete')
    return build['id']


def validate_probe(metadata, phase, expected_duration):
    video = next((s for s in metadata['streams'] if s.get('codec_type') == 'video'), None)
    audio = next((s for s in metadata['streams'] if s.get('codec_type') == 'audio'), None)
    width, height = (540, 960) if phase == 'preview' else (1080, 1920)
    if not video or not audio or video.get('codec_name') != 'h264' or audio.get('codec_name') != 'aac' or (video.get('width'), video.get('height')) != (width, height):
        raise ValueError('Artifact codecs/dimensions/audio invalid')
    rate = video.get('r_frame_rate', '0/1').split('/')
    fps = float(rate[0]) / float(rate[1])
    duration = float(metadata['format']['duration'])
    if abs(fps - 30) > 0.01 or abs(duration - expected_duration) > 0.15:
        raise ValueError('Artifact frame rate/duration invalid')
    if 'duration' in audio and abs(float(audio['duration']) - expected_duration) > 0.15:
        raise ValueError('Artifact audio duration invalid')
    return {'width': width, 'height': height, 'codec': 'h264/aac', 'duration_s': duration, 'fps': fps, 'audio_present': True}


def parse_audio_metrics(log):
    integrated = re.findall(r'I:\s*([-+\w.]+) LUFS', log)
    peak = re.findall(r'Peak:\s*([-+\w.]+) dBFS', log)
    if not integrated or not peak:
        raise ValueError('Measured audio evidence missing')
    loudness, true_peak = float(integrated[-1]), float(peak[-1])
    number(loudness, -18, -12)
    number(true_peak, -90, -1)
    return loudness, true_peak


def master_audio(source, destination, phase='final'):
    if phase not in {'preview', 'final'}:
        raise ValueError('Invalid output phase')
    _, log = run(['ffmpeg', '-hide_banner', '-nostdin', *MEDIA_INPUT, '-i', source, '-vn', '-af', 'loudnorm=I=-15:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'])
    match = re.search(r'\{\s*"input_i".*?\}', log, re.S)
    if not match:
        raise ValueError('Loudness measurement missing')
    measured = json.loads(match.group())
    for key in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset'):
        if not math.isfinite(float(measured[key])):
            raise ValueError('Non-finite/silent source audio')
    audio_filter = ('loudnorm=I=-15:TP=-1.5:LRA=11:linear=true:print_format=json:'
        f'measured_I={measured["input_i"]}:measured_TP={measured["input_tp"]}:measured_LRA={measured["input_lra"]}:'
        f'measured_thresh={measured["input_thresh"]}:offset={measured["target_offset"]}')
    bitrate = '600k' if phase == 'preview' else '3800k'
    # Bounded encoder rate plus a pre-upload byte check keep the entire 180s
    # artifact below Free Worker ingress limits without truncating its timeline.
    run(['ffmpeg', '-hide_banner', '-nostdin', '-y', *MEDIA_INPUT, '-i', source, '-map', '0:v:0', '-map', '0:a:0',
        '-c:v', 'libx264', '-preset', 'medium', '-pix_fmt', 'yuv420p', '-b:v', bitrate, '-maxrate', bitrate,
        '-bufsize', '1200k' if phase == 'preview' else '7600k',
        '-af', audio_filter, '-ar', '48000', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', destination])


def artifact_qa(path, job):
    result = validate_probe(probe(path), job['phase'], duration_of(job))
    # One full decode covers BOTH streams; any decoder error is fatal.
    _, log = run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'info', '-xerror', *MEDIA_INPUT, '-i', path,
        '-vf', 'blackdetect=d=0.5:pix_th=0.10', '-af', 'ebur128=peak=true', '-f', 'null', '-'])
    loudness, peak = parse_audio_metrics(log)
    black = [float(v) for v in re.findall(r'black_duration:([\d.]+)', log)]
    if sum(black) >= duration_of(job) * 0.95:
        raise ValueError('Artifact is almost entirely black')
    result.update({'artifact_sha256': hash_file(path), 'actual_mp4': True, 'full_decode': True, 'phase': job['phase'],
        'integrated_lufs': loudness, 'true_peak_dbtp': peak, 'black_intervals_s': black, 'passed': True,
        'human_review': 'REQUIRED', 'editorial_quality': 'NOT_AUTOMATICALLY_CERTIFIED', 'framing': 'CENTER_CROP_REQUIRES_REVIEW'})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--api-origin', default=os.environ.get('CLIPPER_API_ORIGIN'))
    parser.add_argument('--hypit-root', default=os.environ.get('HYPIT_ROOT'))
    args = parser.parse_args()
    client = RunnerClient(args.api_origin or '', args.job_id)
    if args.preflight_only:
        client.job()
        print('Authorized exact persisted job; preflight passed')
        return
    build_id, receipts = None, []
    stage = 'fetch_job'
    directory = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / ('clipper-' + args.job_id)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / 'assets').mkdir()
    try:
        job, assets = client.job()
        stage = 'download_verified_sources'
        for descriptor in assets:
            client.download(descriptor, directory / 'assets' / descriptor['asset_id'])
        if job['phase'] == 'analysis':
            evidence = {'job_id': job['id'], 'revision': job['revision'], 'phase': 'analysis', 'assets': {}}
            for descriptor in assets:
                evidence['assets'][descriptor['asset_id']] = source_analysis(directory / 'assets' / descriptor['asset_id'])
            path = directory / 'analysis.json'
            path.write_text(json.dumps(evidence, allow_nan=False))
            receipts.append(client.upload(path, 'analysis'))
            stage = 'submit_result'
            client.result({'status': 'SUCCEEDED', 'build_id': None, 'artifacts': receipts})
            return
        engine = Path(args.hypit_root or '').resolve()
        if run(['git', '-C', engine, 'rev-parse', 'HEAD'])[0].strip() != HYPIT_SHA:
            raise ValueError('Untrusted Hypit engine revision')
        stage = 'prepare_bounded_media'
        prepare_assets(job, directory)
        write_template(job, directory)
        cli = ['node', engine / 'bin/hypit.mjs']
        stage = 'prepare_pinned_runtime'
        run(cli + ['programs', 'prepare', '--runtime', 'hypit.runtime.json'], cwd=directory)
        run(cli + ['check', 'build.svrun', '--runtime', 'hypit.runtime.json', '--asset-root', directory / 'assets', '--json'], cwd=directory)
        stage = 'hypit_composition'
        built = subprocess.run([str(v) for v in cli + ['build', 'build.svrun', '--runtime', 'hypit.runtime.json', '--asset-root', directory / 'assets', '--follow', '--json']],
            cwd=directory, capture_output=True, text=True, timeout=3600)
        (directory / 'build.json').write_text(built.stdout)
        payload = json.loads(built.stdout)
        if payload.get('format') == 'hypit.cli-build@1' and isinstance(payload.get('build', {}).get('id'), str):
            build_id = payload['build']['id']
        if built.returncode:
            raise RuntimeError('Exact Hypit build failed')
        build_id = parse_build(built.stdout)
        run(cli + ['get', build_id, '--output', 'final.video', '--to', directory / 'composed.mp4', '--json'], cwd=directory)
        artifact = directory / (job['phase'] + '.mp4')
        stage = 'audio_mastering'
        master_audio(directory / 'composed.mp4', artifact, phase=job['phase'])
        stage = 'actual_artifact_qa'
        qa = artifact_qa(artifact, job)
        qa.update({'build_id': build_id, 'hypit_sha': HYPIT_SHA, 'template': 'vertical-v1', 'template_source_sha256': hash_file(Path(__file__)), 'job_id': job['id'], 'revision': job['revision']})
        stage = 'upload_private_receipts'
        receipts.append(client.upload(artifact, job['phase']))
        path = directory / 'qa.json'
        path.write_text(json.dumps(qa, allow_nan=False))
        receipts.append(client.upload(path, 'qa'))
        stage = 'submit_result'
        client.result({'status': 'SUCCEEDED', 'build_id': build_id, 'artifacts': receipts, 'qa': qa})
    except Exception:
        if stage == 'submit_result':
            # The server may have committed success before the ACK was lost.
            # Do not contradict it with FAILED or blindly resubmit a callback.
            raise RuntimeError('RESULT_DELIVERY_UNKNOWN: reconcile exact run and stored receipts') from None
        # No exception strings, request URLs, OIDC tokens or raw subprocess logs leave runner.
        try:
            client.result({'status': 'FAILED', 'build_id': build_id, 'artifacts': receipts,
                'error': {'code': 'RUNNER_FAILED', 'stage': stage, 'message': 'Trusted runner failed; no artifact approved for publication.'}})
        except Exception:
            pass
        raise RuntimeError('RUNNER_FAILED: no publishable result') from None


if __name__ == '__main__':
    main()
