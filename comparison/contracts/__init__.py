"""Transport and provenance contracts for external RAG comparisons."""

from comparison.contracts.provenance import (
    COMMON_IDENTITY_FIELDS,
    METRICS_CONTRACT_VERSION,
    REQUIRED_STATUS_FIELDS,
    backend_source_sha256,
    config_sha256,
    implementation_source_sha256,
    service_identity_issues,
    shared_source_sha256,
)

__all__ = [
    "COMMON_IDENTITY_FIELDS",
    "METRICS_CONTRACT_VERSION",
    "REQUIRED_STATUS_FIELDS",
    "backend_source_sha256",
    "config_sha256",
    "implementation_source_sha256",
    "service_identity_issues",
    "shared_source_sha256",
]
