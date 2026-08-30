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
ENGINE_UPDATE_UNSUPPORTED = "ENGINE_UPDATE_UNSUPPORTED"
# Analysis is intentionally a separate error family so the UI can distinguish
# invalid scientific input from a missing optional runtime dependency or a
# stale artifact.  These codes are stable IPC contract, not sklearn errors.
ANALYSIS_NOT_FOUND = "ANALYSIS_NOT_FOUND"
ANALYSIS_DEPENDENCY_MISSING = "ANALYSIS_DEPENDENCY_MISSING"
ANALYSIS_INPUT_INVALID = "ANALYSIS_INPUT_INVALID"
ANALYSIS_INSUFFICIENT_SAMPLES = "ANALYSIS_INSUFFICIENT_SAMPLES"
ANALYSIS_STALE = "ANALYSIS_STALE"
ARTIFACT_INVALID = "ARTIFACT_INVALID"
EXPORT_FAILED = "EXPORT_FAILED"


class AppError(Exception):
    """Error carrying an IPC error code; message is developer-facing (docs §6)."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def invalid_params(message: str, details: dict | None = None) -> AppError:
    return AppError(INVALID_PARAMS, message, details)
