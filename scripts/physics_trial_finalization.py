"""Finalize bounded experimental physics reports after the native session ends."""
def session_end_reason(session_report):
    """Classify a completed NativeSession without inferring server acceptance."""
    if not isinstance(session_report, dict):
        return 'session_ended'
    if session_report.get('duration_limit_reached'):
        return 'session_duration_reached'
    if session_report.get('termination') or session_report.get('application_close'):
        return 'server_disconnected'
    return 'session_ended'


def finalize_physics_reports(session_report, physics_trial, walk_exercise=None, *, now,
                            report_key='experimental_physics', walk_report_key='experimental_physics_walk'):
    """Stop trial reporting after transport completion without ticking/sending.

    ``LivePhysicsTrial.stop`` is idempotent, so an earlier terminal reason is
    retained. The walk exercise observes ``connected=False`` and cannot submit
    a Luau task after the transport has ended.
    """
    if not isinstance(session_report, dict):
        raise TypeError('session_report must be a mapping')
    if physics_trial is None:
        return session_report

    trial_report = physics_trial.stop(session_end_reason(session_report))
    session_report[report_key] = {
        key: value for key, value in trial_report.items()
        if key != 'local_prediction'
    }
    if walk_exercise is not None:
        session_report[walk_report_key] = walk_exercise.tick(
            now=now, trial_report=trial_report,
            execute_source=None, results={}, connected=False, close=lambda: None)
    return session_report


__all__ = ['session_end_reason', 'finalize_physics_reports']
