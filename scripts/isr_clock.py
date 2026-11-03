"""Session ISR clock estimate from observed remote steps and real send ACKs.

Implements the integer-smoothing branch of native 10449B4EC with its explicit
10 percent / 20-update defaults. Native optional float/stall feature branches
are not reproduced; reports do not claim full native scheduling parity.
"""
import struct


def _f32(value):
    return struct.unpack('<f',struct.pack('<f',value))[0]


def _step(value):
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        raise ValueError('ISR clock requires a u32 simulation step')
    return value


def _delta(a,b):
    return ((a-b+0x80000000) & 0xffffffff)-0x80000000


class IsrClockEstimator:
    def __init__(self):
        self.remote_step = None
        self.acknowledged_local_step = None
        self.offset = None
        self.stabilized = False
        self._remote_new = self._ack_new = False
        self.updates = self.acknowledgments = self.losses = 0
        self.remote_observations = 0

    def observe_remote(self, step):
        _step(step)
        if self.remote_step is None or _delta(step,self.remote_step) > 0:
            self.remote_step = step
            self._remote_new = True
            self.remote_observations += 1

    def observe_delivery(self, outcome, local_step):
        _step(local_step)
        if outcome == 'lost':
            self.losses += 1
            return
        if outcome != 'acknowledged':
            raise ValueError('ISR requires a transport ACK or loss observation')
        self.acknowledgments += 1
        if (self.acknowledged_local_step is None
                or _delta(local_step,self.acknowledged_local_step) > 0):
            self.acknowledged_local_step = local_step
            self._ack_new = True

    def update(self, current_local_step):
        _step(current_local_step)
        if not self._remote_new or not self._ack_new:
            return self.offset
        raw = _delta(self.remote_step,self.acknowledged_local_step)
        if not -0x7ffffffe <= raw <= 0x7ffffffe:
            raise ValueError('ISR clock observation hits an unknown/reserved offset')
        if self.offset is None:
            self.offset = raw
        else:
            alpha = _f32(.1)
            self.offset = int(_f32(_f32(_f32(1.-alpha)*_f32(self.offset))
                                      + _f32(alpha*_f32(raw))))
        # Native +4 increments until it already equals the 20-update threshold.
        if not self.stabilized and self.updates >= 20:
            value = _delta(self.remote_step,current_local_step)
            if not -0x7ffffffe <= value <= 0x7ffffffe:
                raise ValueError('ISR current local step is outside the observed epoch')
            self.offset = value
            self.stabilized = True
        self.updates += 1
        self._remote_new = self._ack_new = False
        return self.offset

    def report(self):
        return {'source':'remote_isr_steps_and_transport_acknowledged_local_steps',
                'remote_step':self.remote_step,
                'acknowledged_local_step':self.acknowledged_local_step,
                'remote_to_local_offset':self.offset, 'updates':self.updates,
                'stabilized':self.stabilized, 'acknowledgments':self.acknowledgments,
                'losses':self.losses, 'remote_observations':self.remote_observations,
                'native_branch':'integer_smoothing_10_percent_stabilize_after_20',
                'optional_native_clock_features_implemented':False}
