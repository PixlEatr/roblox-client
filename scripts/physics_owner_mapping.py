"""Fail-closed comparison for replicated NetworkOwnerV3 values.

The schema only says type 34 (varuint); it does not define the numeric domain.
This module intentionally refuses to equate that value with a user id or local
peer id until the caller supplies a native-trace mapping record.
"""
from collections.abc import Mapping


def map_network_owner_v3(owner_value, local_identity, evidence=None):
    """Return True/False only for a documented same-domain numeric mapping.

    ``evidence`` must be produced from a static native trace, with both numeric
    values explicitly identified as the same domain. A traced ``unowned_value``
    takes precedence over equality so an unowned sentinel cannot be mistaken for
    a local identity. Missing or incomplete evidence returns None (unknown).
    This is an eligibility hint, not proof the server accepted a movement update.
    """
    if type(owner_value) is not int or owner_value < 0:
        return None
    if type(local_identity) is not int or local_identity < 0:
        return None
    if not isinstance(evidence, Mapping):
        return None
    if evidence.get('source') != 'native_trace':
        return None
    if evidence.get('owner_domain') != evidence.get('local_identity_domain'):
        return None
    domain = evidence.get('owner_domain')
    if not isinstance(domain, str) or not domain:
        return None
    if evidence.get('comparison') != 'numeric_equality':
        return None
    if 'unowned_value' in evidence:
        unowned_value = evidence['unowned_value']
        if type(unowned_value) is not int or unowned_value < 0:
            return None
        if owner_value == unowned_value:
            return False
    return owner_value == local_identity
