"""Native program-check framing and signature-verified runtime handling.

The parser alone is data-only. BytecodeAuthHandler additionally validates the
original signature, converts opcode headers, executes within a bounded worker,
and formats the actual numeric return. Reports exclude identifiers and answers.
"""
from dataclasses import dataclass
import struct

from wire_reader import WireError


MAX_PROGRAM_BYTES = 128 * 1024


@dataclass(frozen=True)
class ProgramCheck:
    first: int
    second: int
    program: bytes

    @property
    def arguments(self):
        # Native 104ca08ec constructs callback arguments in this order.
        return self.second, self.first

    def summary(self):
        return {'program_bytes': len(self.program),
                'execution_implemented': False,
                'response_implemented': False}


def parse_program_check(payload):
    """Read ID155/two LE32 fields/LE32-length binary string, consuming all bytes."""
    if not isinstance(payload, bytes):
        raise WireError('Program check must be bytes')
    if len(payload) < 13 or payload[0] != 155:
        raise WireError('Invalid program-check packet header')
    first, second, length = struct.unpack_from('<III', payload, 1)
    if not 1 <= length <= MAX_PROGRAM_BYTES:
        raise WireError('Invalid program-check size')
    if len(payload) != 13 + length:
        raise WireError('Program-check length mismatch')
    return ProgramCheck(first, second, payload[13:])


def native_unsigned_result(value):
    """Finite-number subset of native FCVTZU W22,D0 (104ca0b7c)."""
    import math
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Program result must be a finite number')
    return int(min(0xffffffff, max(0, value)))


class BytecodeAuthError(ValueError):
    """Sanitized failure, without any received fields or engine error text."""


class BytecodeAuthHandler:
    """Verify original server code, convert its representation, then execute it.

    No expected answers or integrity measurements are substituted. The engine
    receives the issued session job ID. The original signature must verify
    before opcode decoding; all bytes including its trailer are retained.
    """
    def __init__(self, job_id, *, max_checks=16, unpack=None, verify=None,
                 normalize=None, evaluate=None):
        import uuid
        uuid.UUID(job_id)
        from bytecode_container import unpack_container, standardize_opcodes
        from bytecode_signature import verify_signed_bytecode
        from local_runtime import run_bytecode
        self.job_id = job_id
        self.max_checks = max_checks
        self.unpack = unpack or unpack_container
        self.verify = verify or verify_signed_bytecode
        self.normalize = normalize or standardize_opcodes
        self.evaluate = evaluate or run_bytecode
        self.cache = {}
        self.signatures_verified = 0
        self.programs_executed = 0
        self.responses_prepared = 0
        self.replays = 0
        self.last_metadata = None
        self.last_runtime_error = None
        self.last_failure = None

    def handle(self, payload):
        import hashlib
        key = hashlib.sha256(payload).digest()
        if key in self.cache:
            self.replays += 1
            return self.cache[key]
        if len(self.cache) >= self.max_checks:
            self.last_failure = 'check_limit'
            raise BytecodeAuthError(self.last_failure)
        try:
            check = parse_program_check(payload)
            original = self.unpack(check.program)
            verified = self.verify(original)
            if verified.get('verification') is not True:
                raise ValueError('signature_not_verified')
            self.signatures_verified += 1
            standard, format_info = self.normalize(original)
            self.last_metadata = {**verified, **format_info, 'authentication_validated': True}
            result = self.evaluate(standard, check.arguments,
                                   session_job_id=self.job_id,
                                   timeout_ms=1000, memory_mb=32)
            self.programs_executed += 1
            if result.get('ok') is not True:
                # The caller may save this diagnostic only in an owner-only file.
                self.last_runtime_error = result
                raise ValueError('runtime_failure')
            values = result.get('values')
            if not isinstance(values, list) or not values:
                raise ValueError('missing_numeric_result')
            value = native_unsigned_result(values[0])
            response = bytes([155]) + struct.pack('<II', check.second, value)
        except Exception as error:
            self.last_failure = getattr(error, 'reason', type(error).__name__)
            raise BytecodeAuthError(self.last_failure) from None
        self.cache[key] = response
        self.responses_prepared += 1
        return response

    def summary(self):
        return {'signature_validation_implemented': True,
                'execution_implemented': True, 'response_implemented': True,
                'signatures_verified': self.signatures_verified,
                'programs_executed': self.programs_executed,
                'responses_prepared': self.responses_prepared,
                'replays': self.replays, 'last_failure': self.last_failure,
                'last_program_format': self.last_metadata}
