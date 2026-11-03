"""Validated local display surface, in UI units; never a replicated property."""
import math


def validate_display(profile):
    if (not isinstance(profile, dict) or 'size' not in profile
            or set(profile) - {'size', 'device_insets', 'core_insets', 'topbar_insets'}):
        raise ValueError('Display requires size and optional safe-area margins')
    result = {}
    for key, value in profile.items():
        count = 2 if key == 'size' else 4
        if (not isinstance(value, list) or len(value) != count
                or any(type(v) not in (int, float) or not 0 <= v <= 32768
                       or not math.isfinite(v) for v in value)):
            raise ValueError('Display dimensions must be finite numbers in 0..32768')
        result[key] = value.copy()
    result.setdefault('device_insets', [0, 0, 0, 0])
    result.setdefault('core_insets', result['device_insets'].copy())
    width, height = result['size']
    for key in ('device_insets', 'core_insets', 'topbar_insets'):
        if key not in result:
            continue
        left, top, right, bottom = result[key]
        if left + right > width or top + bottom > height:
            raise ValueError('Display insets exceed surface size')
        if key != 'device_insets' and any(a < b for a, b in zip(result[key], result['device_insets'])):
            raise ValueError('UI safe area must be inside device safe area')
    return result
