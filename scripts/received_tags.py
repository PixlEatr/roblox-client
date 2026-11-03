"""CollectionService tags from received Tags SharedStrings and fresh instances.

The payload is NUL-delimited UTF-8, as documented by rbx_types::Tags in
https://github.com/rojo-rbx/rbx-dom/blob/master/rbx_types/src/tags.rs .
World.shared already verifies the wire digest before retaining these bytes.
"""


def decode_tags(data):
    if not isinstance(data, bytes) or len(data) > 1024 * 1024:
        raise ValueError('Invalid tag payload')
    tags = set()
    for raw in data.split(b'\0'):
        if not raw:
            continue
        if len(raw) > 65536:
            raise ValueError('Tag exceeds runtime limit')
        tags.add(raw.decode('utf-8'))
        if len(tags) > 256:
            raise ValueError('Tag count exceeds runtime limit')
    return sorted(tags)


def tag_snapshot(properties, shared=None, creation=None):
    if 'Tags' in properties:
        value = properties['Tags']
        try:
            if isinstance(value, dict) and set(value) == {'shared'}:
                key = bytes.fromhex(value['shared'])
                if len(key) != 16:
                    raise ValueError('Invalid shared string reference')
                value = (shared or {})[key]
            return {'collection_tags': decode_tags(value), 'collection_tags_known': True}
        except (ValueError, TypeError, KeyError):
            return {'collection_tags': [], 'collection_tags_known': False}
    if creation and all(creation.get(flag) for flag in ('fresh', 'complete')):
        return {'collection_tags': [], 'collection_tags_known': True,
                'collection_tags_origin': 'constructor_default'}
    return {}
