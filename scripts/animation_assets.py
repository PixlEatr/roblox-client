"""Fetch public animation assets and decode clip data in a bounded subprocess.

This does not execute scripts, create tracks, send animation updates, or claim
playback. The live runtime must consume the returned keyframes explicitly.
"""
import gzip
import collections
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CODEC = ROOT / 'animation-codec/target/release/animation-codec'
MAX_INPUT = 4 * 1024 * 1024
CODEC_ERRORS = frozenset('''unsupported_container truncated_header instance_limit
expansion_limit truncated_chunk trailing_data missing_end_chunk invalid_numeric_property
nonfinite_property invalid_enum_property unsupported_rig_data_children
unsupported_rig_data_field rig_data_size_limit pose_limit missing_instance
unsupported_pose_child pose_name_limit invalid_pose_frame nonfinite_pose_frame
missing_pose_child missing_number_pose unsupported_face_controls_child model_decode_failed
expected_one_animation_clip missing_clip unsupported_animation_clip invalid_loop_property
missing_keyframe unsupported_clip_child keyframe_time_limit missing_keyframe_child
invalid_marker_value marker_size_limit unsupported_keyframe_child empty_keyframe_sequence
input_read_failed output_limit'''.split())
ASSET_ERRORS = frozenset('''invalid_animation_reference animation_asset_size_limit
invalid_animation_compression animation_asset_expansion_limit unsupported_animation_container
animation_decode_timeout animation_decoder_unavailable animation_decode_failed
invalid_animation_decoder_result unsupported_asset_redirect animation_asset_fetch_failed
animation_timeline_size_limit'''.split())


class AnimationAssetError(ValueError):
    pass


def animation_error_code(error):
    """Only fixed local reasons and numeric HTTP statuses can reach diagnostics."""
    code = str(error)
    if (code in ASSET_ERRORS
            or (code.startswith('animation_decode:') and code.removeprefix('animation_decode:') in CODEC_ERRORS)
            or re.fullmatch(r'animation_asset_http_[1-5][0-9]{2}', code)):
        return code
    return 'animation_asset_unavailable'


def animation_asset_id(reference):
    if not isinstance(reference, str) or len(reference) > 512:
        raise AnimationAssetError('invalid_animation_reference')
    match = re.fullmatch(r'rbxassetid://([0-9]{1,20})', reference)
    if match:
        value = match[1]
    else:
        try:
            parsed = urllib.parse.urlsplit(reference)
        except ValueError:
            raise AnimationAssetError('invalid_animation_reference') from None
        if (parsed.scheme not in ('http', 'https')
                or parsed.netloc not in ('www.roblox.com', 'roblox.com')
                or parsed.path not in ('/asset', '/asset/') or parsed.fragment):
            raise AnimationAssetError('invalid_animation_reference')
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {'id'} or len(query['id']) != 1:
            raise AnimationAssetError('invalid_animation_reference')
        value = query['id'][0]
    if not re.fullmatch(r'[0-9]{1,20}', value) or not 0 < int(value) < 2**64:
        raise AnimationAssetError('invalid_animation_reference')
    return str(int(value))


def decode_animation_asset(raw, *, decoder=CODEC):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_INPUT:
        raise AnimationAssetError('animation_asset_size_limit')
    data = raw
    if data.startswith(b'\x1f\x8b'):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(MAX_INPUT + 1)
        except (OSError, EOFError):
            raise AnimationAssetError('invalid_animation_compression') from None
        if len(data) > MAX_INPUT:
            raise AnimationAssetError('animation_asset_expansion_limit')
    if not data.startswith(b'<roblox!\x89\xff\r\n\x1a\n'):
        raise AnimationAssetError('unsupported_animation_container')
    try:
        result = subprocess.run([str(decoder)], input=data, capture_output=True,
                                timeout=5, check=False)
    except subprocess.TimeoutExpired:
        raise AnimationAssetError('animation_decode_timeout') from None
    except OSError:
        raise AnimationAssetError('animation_decoder_unavailable') from None
    if result.returncode != 0:
        code = result.stderr.decode('ascii', errors='replace').strip()
        if code in CODEC_ERRORS:
            raise AnimationAssetError('animation_decode:' + code)
        raise AnimationAssetError('animation_decode_failed')
    if len(result.stdout) > 8 * 1024 * 1024:
        raise AnimationAssetError('animation_decode_failed')
    try:
        clip = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise AnimationAssetError('invalid_animation_decoder_result') from None
    if not isinstance(clip, dict) or clip.get('version') != 1 or clip.get('kind') != 'KeyframeSequence':
        raise AnimationAssetError('invalid_animation_decoder_result')
    return {**clip, 'source_sha256': hashlib.sha256(data).hexdigest()}


class _AssetRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        try:
            target = urllib.parse.urlsplit(newurl)
            host = target.hostname or ''
            port = target.port
        except ValueError:
            raise AnimationAssetError('unsupported_asset_redirect') from None
        allowed = host == 'assetdelivery.roblox.com' or bool(
            re.fullmatch(r'(?:[a-z0-9-]+\.)*rbxcdn\.com', host))
        if (target.scheme != 'https' or not allowed or target.username
                or target.password or port not in (None, 443)):
            raise AnimationAssetError('unsupported_asset_redirect')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def fetch_animation_clip(reference, *, decoder=CODEC):
    asset_id = animation_asset_id(reference)
    request = urllib.request.Request(
        'https://assetdelivery.roblox.com/v1/asset/?id=' + asset_id,
        headers={'User-Agent': 'RobloxRuntimeResearch/0.1', 'Accept-Encoding': 'identity'})
    # No account cookie is attached or forwarded, even across CDN redirects.
    opener = urllib.request.build_opener(_AssetRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read(MAX_INPUT + 1)
    except urllib.error.HTTPError as error:
        if type(error.code) is int and 100 <= error.code <= 599:
            raise AnimationAssetError('animation_asset_http_' + str(error.code)) from None
        raise AnimationAssetError('animation_asset_fetch_failed') from None
    except (urllib.error.URLError, OSError):
        raise AnimationAssetError('animation_asset_fetch_failed') from None
    return {'asset_id': asset_id, **decode_animation_asset(raw, decoder=decoder)}


def animation_timeline(clip):
    """Small explicit timeline projection; this does not claim joint evaluation."""
    result = {key: clip[key] for key in ('version', 'kind', 'length', 'looped', 'priority')}
    result['keyframes'] = [{key: frame[key] for key in ('time', 'name', 'markers')}
                          for frame in clip['keyframes']]
    result['source_sha256'] = clip['source_sha256']
    if len(json.dumps(result, allow_nan=False).encode()) > 1024 * 1024:
        raise AnimationAssetError('animation_timeline_size_limit')
    return result


class AnimationAssetQueue:
    """Owner-thread API with bounded background fetch/decode and ID deduplication.

    No network call, decoder invocation, or unfinished future wait runs in
    submit/drain. Errors are returned to the VM as failures, never fake clips.
    """
    def __init__(self, *, fetcher=fetch_animation_clip, max_assets=128, workers=2,
                 pose_cache_limit_bytes=32 * 1024 * 1024):
        if type(pose_cache_limit_bytes) is not int or not 0 <= pose_cache_limit_bytes <= 128 * 1024 * 1024:
            raise ValueError('Pose cache budget must be from zero to 128 MiB')
        self.fetcher = fetcher
        self.max_assets = max_assets
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix='animation-assets')
        self.pending = {}
        self.cache = {}
        self.cache_bytes = 0
        self.pose_cache = collections.OrderedDict()
        self.pose_cache_bytes = 0
        self.pose_cache_limit_bytes = pose_cache_limit_bytes
        self.ready = collections.deque()
        self.references = set()
        self.closed = False

    def _load(self, asset_id):
        # Asset delivery can briefly fail during character spawn/load bursts.
        # Retry only network/overload statuses; deterministic format and 4xx
        # failures stay terminal. Sleeps are confined to the worker lane.
        for attempt in range(3):
            try:
                clip = self.fetcher('rbxassetid://' + asset_id)
                size = len(json.dumps(clip, allow_nan=False).encode())
                return {'clip': animation_timeline(clip), '_pose_clip': clip, '_pose_bytes': size}
            except AnimationAssetError as error:
                code = animation_error_code(error)
                retry = code == 'animation_asset_fetch_failed' or code in {
                    'animation_asset_http_408', 'animation_asset_http_429',
                    'animation_asset_http_500', 'animation_asset_http_502',
                    'animation_asset_http_503', 'animation_asset_http_504',
                }
                if not retry or attempt == 2:
                    return {'error': code}
            except Exception:
                # Neither asset-server responses nor subprocess output enter logs.
                if attempt == 2:
                    return {'error': 'animation_asset_unavailable'}
            time.sleep((0.2, 0.6)[attempt])

    def submit(self, requests):
        if self.closed:
            raise RuntimeError('Animation asset queue is closed')
        for request in requests:
            reference = request['reference']
            if not isinstance(reference, str) or len(reference) > 512:
                raise ValueError('Invalid runtime animation request')
            if reference in self.references:
                continue
            if len(self.references) >= self.max_assets:
                raise ValueError('Animation request count limit exceeded')
            self.references.add(reference)
            try:
                asset_id = animation_asset_id(reference)
            except ValueError:
                self.ready.append({'reference': reference, 'error': 'invalid_animation_reference'})
                continue
            if asset_id in self.cache:
                self.ready.append({'reference': reference, **self.cache[asset_id]})
            elif asset_id in self.pending:
                self.pending[asset_id][1].append(reference)
            else:
                self.pending[asset_id] = (self.executor.submit(self._load, asset_id), [reference])

    def drain(self):
        for asset_id, (future, references) in list(self.pending.items()):
            if not future.done():
                continue
            result = future.result()
            pose_clip = result.pop('_pose_clip', None)
            pose_bytes = result.pop('_pose_bytes', 0)
            size = len(json.dumps(result, allow_nan=False).encode())
            if self.cache_bytes + size > 16 * 1024 * 1024:
                result = {'error': 'animation_cache_size_limit'}
                size = len(json.dumps(result).encode())
            if ('clip' in result and pose_clip is not None
                    and 0 < pose_bytes <= min(8 * 1024 * 1024, self.pose_cache_limit_bytes)):
                while self.pose_cache and self.pose_cache_bytes + pose_bytes > self.pose_cache_limit_bytes:
                    _, (_, evicted_size) = self.pose_cache.popitem(last=False)
                    self.pose_cache_bytes -= evicted_size
                self.pose_cache[asset_id] = (pose_clip, pose_bytes)
                self.pose_cache_bytes += pose_bytes
            self.cache[asset_id] = result
            self.cache_bytes += size
            self.ready.extend({'reference': ref, **result} for ref in references)
            del self.pending[asset_id]
        responses, size = [], 0
        while self.ready and len(responses) < 16:
            response = self.ready[0]
            length = len(json.dumps(response, allow_nan=False).encode())
            if responses and size + length > 2 * 1024 * 1024:
                break
            responses.append(self.ready.popleft())
            size += length
        return responses

    def pose_clip(self, reference):
        """Return a completed full clip without I/O; consumers treat it as read-only.

        Joint samples stay on the owning Python thread, outside the VM's small
        timeline response. An unfinished or evicted clip returns None.
        """
        asset_id = animation_asset_id(reference)
        entry = self.pose_cache.get(asset_id)
        if entry is None:
            return None
        self.pose_cache.move_to_end(asset_id)
        return entry[0]

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.pending.clear()
        self.cache.clear()
        self.pose_cache.clear()
        self.pose_cache_bytes = 0
        self.ready.clear()
