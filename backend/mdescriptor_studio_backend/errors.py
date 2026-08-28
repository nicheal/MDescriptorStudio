"""Application error codes (docs/plan/02-IPC_PROTOCOL.md §6)."""

DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
DATASET_CHANGED = "DATASET_CHANGED"
INVALID_DATASET = "INVALID_DATASET"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
UNSUPPORTED_PERIODICITY = "UNSUPPORTED_PERIODICITY"
MDESCRIPTOR_INCOMPATIBLE = "MDESCRIPTOR_INCOMPATIBLE"
DESCRIPTOR_CONFIGURATION_ERROR = "DESCRIPTOR_CONFIGURATION_ERROR"
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
OUT_OF_MEMORY = "OUT_OF_MEMORY"
JOB_CANCELLED = "JOB_CANCELLED"
RESULT_INCOMPATIBLE = "RESULT_INCOMPATIBLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
PROTOCOL_VERSION_MISMATCH = "PROTOCOL_VERSION_MISMATCH"
JOB_NOT_FOUND = "JOB_NOT_FOUND"
INVALID_PARAMS = "INVALID_PARAMS"


class AppError(Exception):
    """Error carrying an IPC error code; message is developer-facing (docs §6)."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def invalid_params(message: str, details: dict | None = None) -> AppError:
    return AppError(INVALID_PARAMS, message, details)
