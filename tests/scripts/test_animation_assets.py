import gzip
import subprocess
import unittest
from unittest.mock import patch
import urllib.request

from animation_assets import (AnimationAssetError, _AssetRedirect,
                              animation_asset_id, decode_animation_asset,
                              animation_error_code, fetch_animation_clip)


class AnimationAssetTests(unittest.TestCase):
    def test_normalizes_only_supported_asset_references(self):
        for value in ('rbxassetid://00123','http://www.roblox.com/asset/?id=123',
                      'https://roblox.com/asset?id=123'):
            self.assertEqual(animation_asset_id(value),'123')
        for value in ('rbxassetid://0','rbxassetid://-1','file:///etc/passwd',
                      'https://evil.example/asset?id=123',
                      'https://www.roblox.com/asset?id=123&id=456',
                      'https://www.roblox.com/asset?id=123&token=private'):
            with self.subTest(value=value),self.assertRaises(AnimationAssetError):
                animation_asset_id(value)

    def test_expanding_compressed_input_stops_before_native_process(self):
        raw=gzip.compress(b'x'*1000)
        with patch('animation_assets.MAX_INPUT',100),patch('animation_assets.subprocess.run') as run:
            with self.assertRaisesRegex(AnimationAssetError,'expansion_limit'):
                decode_animation_asset(raw)
        run.assert_not_called()

    def test_corrupt_compression_and_unrecognized_container_are_rejected(self):
        for raw in (b'\x1f\x8bgarbage',b'<html>not an animation</html>'):
            with self.subTest(raw=raw),self.assertRaises(AnimationAssetError):
                decode_animation_asset(raw)

    def test_native_timeout_returns_no_partial_clip(self):
        raw=b'<roblox!\x89\xff\r\n\x1a\n'+bytes(18)
        with patch('animation_assets.subprocess.run',side_effect=subprocess.TimeoutExpired('codec',5)):
            with self.assertRaisesRegex(AnimationAssetError,'decode_timeout'):
                decode_animation_asset(raw)

    def test_decoder_diagnostics_preserve_only_known_reason_codes(self):
        raw=b'<roblox!\x89\xff\r\n\x1a\n'+bytes(18)
        for stderr,expected in ((b'unsupported_face_controls_child\n',
                                 'animation_decode:unsupported_face_controls_child'),
                                (b'private content from an unexpected decoder',
                                 'animation_decode_failed')):
            result=subprocess.CompletedProcess(['codec'],2,stdout=b'',stderr=stderr)
            with self.subTest(expected=expected),patch('animation_assets.subprocess.run',return_value=result):
                with self.assertRaises(AnimationAssetError) as caught:decode_animation_asset(raw)
                self.assertEqual(str(caught.exception),expected)

    def test_http_status_is_reported_without_server_body_or_credentials(self):
        import urllib.error
        from unittest.mock import Mock
        opener=Mock()
        opener.open.side_effect=urllib.error.HTTPError(
            'https://assetdelivery.roblox.com/v1/asset/',403,'private server body',{},None)
        with patch('animation_assets.urllib.request.build_opener',return_value=opener):
            with self.assertRaisesRegex(AnimationAssetError,'^animation_asset_http_403$'):
                fetch_animation_clip('rbxassetid://123')
        request=opener.open.call_args.args[0]
        self.assertFalse(request.has_header('Cookie'))
        self.assertFalse(request.has_header('Authorization'))

    def test_error_codes_do_not_forward_unknown_exception_text(self):
        for code in ('animation_asset_http_429','animation_decode:unsupported_pose_child',
                     'animation_decode_timeout'):
            self.assertEqual(animation_error_code(AnimationAssetError(code)),code)
        for code in ('secret url and text','animation_asset_http_999',
                     'animation_decode:unknown_private_message','unsupported_pose_child'):
            self.assertEqual(animation_error_code(AnimationAssetError(code)),
                             'animation_asset_unavailable')

    def test_redirect_cannot_leave_asset_hosts_or_downgrade_https(self):
        request=urllib.request.Request('https://assetdelivery.roblox.com/v1/asset/?id=123')
        handler=_AssetRedirect()
        for url in ('http://t1.rbxcdn.com/a','https://rbxcdn.com.evil.example/a',
                    'https://127.0.0.1/a','https://user:secret@t1.rbxcdn.com/a',
                    'https://t1.rbxcdn.com:invalid/a','https://[invalid/a'):
            with self.subTest(url=url),self.assertRaises(AnimationAssetError):
                handler.redirect_request(request,None,302,'Found',{},url)
        redirected=handler.redirect_request(request,None,302,'Found',{},'https://t1.rbxcdn.com/a')
        self.assertEqual(redirected.full_url,'https://t1.rbxcdn.com/a')


if __name__=='__main__':unittest.main()
