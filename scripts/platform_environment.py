"""Restore the native-library search path in every new terminal process."""
import json
import os
from pathlib import Path


def runtime_environment(root, base=None):
    env=dict(os.environ if base is None else base)
    settings=Path(root)/'.rove-platform.json'
    if settings.is_file():
        try:
            config=json.loads(settings.read_text(encoding='utf-8'))
            prefix=config.get('native_prefix')
            if prefix is not None and not isinstance(prefix,str):raise ValueError()
        except (ValueError,AttributeError):
            raise ValueError('Invalid .rove-platform.json. Run setup again to restore the library location.') from None
        if prefix:env['PATH']=str(Path(prefix)/'bin')+os.pathsep+env.get('PATH','')
    env['PYTHONUTF8']='1'
    return env
