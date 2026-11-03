"""Conservative, offline movement-authority eligibility gate.

The current World model does not interpret NetworkOwnerV3 or infer ownership
from an avatar being local/unanchored. Callers may set ownership_granted only
when a separate decoder has a documented, server-replicated grant signal.
"""
from collections.abc import Mapping

_REQUIRED_TRUE = (
    'own_player_confirmed',
    'character_model_confirmed',
    'humanoid_confirmed',
    'root_part_confirmed',
    'root_in_workspace_confirmed',
    'assembly_root_confirmed',
    'streaming_region_confirmed',
)


def assess_physics_authority(evidence):
    """Return eligibility and reasons from explicit evidence only.

    Expected input is a mapping of booleans. ``anchored`` must be an explicitly
    received bool (not a class default); ``ownership_granted`` must be an
    explicit bool from ``ownership_source == 'server_replication'``. This is a
    prerequisite check, not proof that a server accepts a movement update.
    """
    if not isinstance(evidence, Mapping):
        raise TypeError('evidence must be a mapping')

    reasons = []
    for key in _REQUIRED_TRUE:
        if evidence.get(key) is not True:
            reasons.append(key + '_unconfirmed')

    anchored = evidence.get('anchored')
    if type(anchored) is not bool or evidence.get('anchored_source') != 'server_replication':
        reasons.append('anchored_state_unconfirmed')
    elif anchored:
        reasons.append('root_anchored')

    ownership = evidence.get('ownership_granted')
    if (type(ownership) is not bool
            or evidence.get('ownership_source') != 'server_replication'):
        reasons.append('network_ownership_unconfirmed')
    elif not ownership:
        reasons.append('network_ownership_not_granted')

    if (evidence.get('workspace_authority_mode') == 'Server'
            and evidence.get('workspace_authority_mode_source') == 'server_replication'):
        reasons.append('workspace_server_authority')

    return {
        'eligible_for_physics_submission': not reasons,
        'authority_evidence': 'explicit_server_replication' if not reasons else 'insufficient',
        'server_acceptance_confirmed': False,
        'reasons': reasons,
    }
