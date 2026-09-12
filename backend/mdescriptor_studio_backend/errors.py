"""Application error codes and the public/developer error boundary."""

from __future__ import annotations

import uuid

DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
DATASET_CHANGED = "DATASET_CHANGED"
INVALID_DATASET = "INVALID_DATASET"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
UNSUPPORTED_PERIODICITY = "UNSUPPORTED_PERIODICITY"
MDESCRIPTOR_INCOMPATIBLE = "MDESCRIPTOR_INCOMPATIBLE"
DESCRIPTOR_CONFIGURATION_ERROR = "DESCRIPTOR_CONFIGURATION_ERROR"
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
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
    """Error carrying an IPC code.

    ``message`` is kept for local logs and diagnostics.  ``public_message`` is
    the only text that the IPC layer should expose to the renderer, so paths,
    exception text and dependency internals do not become an information leak.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict | None = None,
        *,
        public_message: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.error_id = uuid.uuid4().hex[:12]
        self.public_message = public_message or _PUBLIC_MESSAGES.get(code, "Request failed.")


_PUBLIC_MESSAGES = {
    INVALID_PARAMS: "Invalid request.",
    DATASET_NOT_FOUND: "Dataset not found.",
    DATASET_CHANGED: "Dataset changed on disk; rescan it before continuing.",
    INVALID_DATASET: "Dataset is invalid or unavailable.",
    UNSUPPORTED_FORMAT: "This dataset format is not supported.",
    UNSUPPORTED_PERIODICITY: "This operation does not support the dataset periodicity.",
    MDESCRIPTOR_INCOMPATIBLE: "The descriptor engine cannot process this input.",
    DESCRIPTOR_CONFIGURATION_ERROR: "Descriptor parameters are invalid.",
    MODEL_NOT_FOUND: "The selected model is unavailable.",
    DEVICE_UNAVAILABLE: "The selected compute device is unavailable on this machine.",
    OUT_OF_MEMORY: "The operation needs more memory than is available.",
    JOB_CANCELLED: "The job was cancelled.",
    RESULT_INCOMPATIBLE: "This result is not compatible with the requested operation.",
    INTERNAL_ERROR: "The backend failed to complete the request.",
    PROTOCOL_VERSION_MISMATCH: "The backend protocol version is incompatible.",
    JOB_NOT_FOUND: "Job not found.",
    ENGINE_UPDATE_UNSUPPORTED: "Engine updates require a new installer.",
    ANALYSIS_NOT_FOUND: "Analysis result not found.",
    ANALYSIS_DEPENDENCY_MISSING: "An optional analysis dependency is unavailable.",
    ANALYSIS_INPUT_INVALID: "Analysis input is invalid.",
    ANALYSIS_INSUFFICIENT_SAMPLES: "There are not enough samples for this analysis.",
    ANALYSIS_STALE: "This analysis input is stale; recompute the descriptor first.",
    ARTIFACT_INVALID: "The stored analysis artifact is invalid.",
    EXPORT_FAILED: "The export could not be written.",
}
