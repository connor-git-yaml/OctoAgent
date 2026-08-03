"""F152 跨端隐私合同的唯一 protocol 投影。"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from octoagent.core.models import (
    AnalysisResult,
    ApprovedAnalysisPacket,
    CapabilityGrant,
    ConsentGrant,
    DeletionReceipt,
    DeviceCapability,
    DeviceIdentity,
    IngestionRetention,
    IngestionStage,
    MemoryCandidateConfirmation,
    OptionalMemoryCandidate,
    Provenance,
    RequestProofPayload,
    ReviewBundle,
    SourceKind,
    canonical_sha256,
)
from pydantic import BaseModel, field_validator, model_validator

F154_HEALTH_PURPOSE = "summarize_recent_activity_and_sleep"
F154_HEALTH_DATA_TYPES = (
    "apple_health.sleep_analysis",
    "apple_health.step_count",
)
F154_HEALTH_FIELD_MANIFEST = (
    "daily_steps[].local_day",
    "daily_steps[].count",
    "daily_steps[].unit",
    "sleep.window_start_utc",
    "sleep.window_end_utc",
    "sleep.total_asleep_minutes",
    "sleep.stage_minutes.awake",
    "sleep.stage_minutes.core",
    "sleep.stage_minutes.deep",
    "sleep.stage_minutes.rem",
    "sleep.stage_minutes.unspecified",
    "sleep.unit",
    "completeness_notice",
)

_F153DeviceCapability = Literal[
    DeviceCapability.DEVICE_READY_READ,
    DeviceCapability.DEVICE_PROFILE_READ,
    DeviceCapability.CONVERSATION_READ,
    DeviceCapability.CONVERSATION_SEND,
    DeviceCapability.TASK_READ,
    DeviceCapability.APPROVAL_READ,
    DeviceCapability.APPROVAL_DECIDE,
    DeviceCapability.MEMORY_CANDIDATE_READ,
    DeviceCapability.MEMORY_CANDIDATE_DECIDE,
]
_F154HealthDataType = Literal[
    "apple_health.sleep_analysis",
    "apple_health.step_count",
]
_F154HealthField = Literal[
    "daily_steps[].local_day",
    "daily_steps[].count",
    "daily_steps[].unit",
    "sleep.window_start_utc",
    "sleep.window_end_utc",
    "sleep.total_asleep_minutes",
    "sleep.stage_minutes.awake",
    "sleep.stage_minutes.core",
    "sleep.stage_minutes.deep",
    "sleep.stage_minutes.rem",
    "sleep.stage_minutes.unspecified",
    "sleep.unit",
    "completeness_notice",
]


class PrivacyConsumer(StrEnum):
    F153 = "F153"
    F154 = "F154"
    F155 = "F155"


class PrivacyContractName(StrEnum):
    DEVICE_IDENTITY = "device_identity"
    CAPABILITY_GRANT = "capability_grant"
    REQUEST_PROOF_PAYLOAD = "request_proof_payload"
    REVIEW_BUNDLE = "review_bundle"
    CONSENT_GRANT = "consent_grant"
    APPROVED_ANALYSIS_PACKET = "approved_analysis_packet"
    ANALYSIS_RESULT = "analysis_result"
    OPTIONAL_MEMORY_CANDIDATE = "optional_memory_candidate"
    MEMORY_CANDIDATE_CONFIRMATION = "memory_candidate_confirmation"
    DELETION_RECEIPT = "deletion_receipt"


class _ReviewBundleRetention(IngestionRetention):
    stage: Literal[IngestionStage.REVIEW_BUNDLE]


class _ApprovedPacketRetention(IngestionRetention):
    stage: Literal[IngestionStage.APPROVED_ANALYSIS_PACKET]


class _ReviewBundleContract(ReviewBundle):
    retention: _ReviewBundleRetention


class _ApprovedAnalysisPacketContract(ApprovedAnalysisPacket):
    retention: _ApprovedPacketRetention


class _F153CapabilityGrantContract(CapabilityGrant):
    capabilities: tuple[_F153DeviceCapability, ...]


class _F154Provenance(Provenance):
    source_kind: Literal[SourceKind.HEALTHKIT]
    data_types: tuple[_F154HealthDataType, ...]

    @field_validator("data_types")
    @classmethod
    def validate_exact_data_types(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if values != F154_HEALTH_DATA_TYPES:
            raise ValueError("F154 health data types must be exact")
        return values


class _F154ReviewBundleContract(_ReviewBundleContract):
    purpose: Literal["summarize_recent_activity_and_sleep"]
    provenance: tuple[_F154Provenance, ...]
    field_manifest: tuple[_F154HealthField, ...]

    @field_validator("field_manifest")
    @classmethod
    def normalize_field_manifest(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not values or len(set(values)) != len(values):
            raise ValueError("F154 field manifest must be non-empty and unique")
        selected = set(values)
        return tuple(field for field in F154_HEALTH_FIELD_MANIFEST if field in selected)

    @model_validator(mode="after")
    def validate_preview_lineage(self) -> _F154ReviewBundleContract:
        if len(self.provenance) != 1:
            raise ValueError("F154 review requires one preview provenance")
        if self.preview_hash != self.provenance[0].source_object_hash:
            raise ValueError("F154 preview hash must match provenance")
        return self


class _F154ConsentGrantContract(ConsentGrant):
    purpose: Literal["summarize_recent_activity_and_sleep"]


class _F154ApprovedAnalysisPacketContract(_ApprovedAnalysisPacketContract):
    purpose: Literal["summarize_recent_activity_and_sleep"]
    provenance: tuple[_F154Provenance, ...]

    @model_validator(mode="after")
    def validate_preview_lineage(self) -> _F154ApprovedAnalysisPacketContract:
        if len(self.provenance) != 1:
            raise ValueError("F154 packet requires one preview provenance")
        return self


_CONTRACT_MODELS: tuple[tuple[PrivacyContractName, type[BaseModel]], ...] = (
    (PrivacyContractName.DEVICE_IDENTITY, DeviceIdentity),
    (PrivacyContractName.CAPABILITY_GRANT, CapabilityGrant),
    (PrivacyContractName.REQUEST_PROOF_PAYLOAD, RequestProofPayload),
    (PrivacyContractName.REVIEW_BUNDLE, _ReviewBundleContract),
    (PrivacyContractName.CONSENT_GRANT, ConsentGrant),
    (
        PrivacyContractName.APPROVED_ANALYSIS_PACKET,
        _ApprovedAnalysisPacketContract,
    ),
    (PrivacyContractName.ANALYSIS_RESULT, AnalysisResult),
    (PrivacyContractName.OPTIONAL_MEMORY_CANDIDATE, OptionalMemoryCandidate),
    (
        PrivacyContractName.MEMORY_CANDIDATE_CONFIRMATION,
        MemoryCandidateConfirmation,
    ),
    (PrivacyContractName.DELETION_RECEIPT, DeletionReceipt),
)

_F153_CONTRACT_MODELS = ((PrivacyContractName.CAPABILITY_GRANT, _F153CapabilityGrantContract),)
_F154_CONTRACT_MODELS = (
    (PrivacyContractName.REVIEW_BUNDLE, _F154ReviewBundleContract),
    (PrivacyContractName.CONSENT_GRANT, _F154ConsentGrantContract),
    (
        PrivacyContractName.APPROVED_ANALYSIS_PACKET,
        _F154ApprovedAnalysisPacketContract,
    ),
)

_F153_CONTRACTS = (
    PrivacyContractName.DEVICE_IDENTITY,
    PrivacyContractName.CAPABILITY_GRANT,
    PrivacyContractName.REQUEST_PROOF_PAYLOAD,
)
_INGESTION_CONTRACTS = (
    PrivacyContractName.REVIEW_BUNDLE,
    PrivacyContractName.CONSENT_GRANT,
    PrivacyContractName.APPROVED_ANALYSIS_PACKET,
    PrivacyContractName.ANALYSIS_RESULT,
    PrivacyContractName.OPTIONAL_MEMORY_CANDIDATE,
    PrivacyContractName.MEMORY_CANDIDATE_CONFIRMATION,
    PrivacyContractName.DELETION_RECEIPT,
)


def _contract_names(consumer: PrivacyConsumer) -> tuple[PrivacyContractName, ...]:
    if consumer is PrivacyConsumer.F153:
        return _F153_CONTRACTS
    if consumer in {PrivacyConsumer.F154, PrivacyConsumer.F155}:
        return _INGESTION_CONTRACTS
    raise ValueError(f"UNKNOWN_CONSUMER: {consumer}")


def _contract_model(
    consumer: PrivacyConsumer,
    name: PrivacyContractName,
) -> type[BaseModel]:
    specialized = ()
    if consumer is PrivacyConsumer.F153:
        specialized = _F153_CONTRACT_MODELS
    elif consumer is PrivacyConsumer.F154:
        specialized = _F154_CONTRACT_MODELS
    for known_name, model in specialized:
        if known_name is name:
            return model
    for known_name, model in _CONTRACT_MODELS:
        if known_name is name:
            return model
    raise ValueError(f"UNKNOWN_CONTRACT: {name}")


def privacy_contract_bundle(consumer: PrivacyConsumer) -> dict[str, Any]:
    """返回每次新建的 deterministic exact JSON-schema bundle。"""

    contracts: list[dict[str, Any]] = []
    for name in _contract_names(consumer):
        schema = _contract_model(consumer, name).model_json_schema()
        contracts.append(
            {
                "name": name.value,
                "schema_sha256": canonical_sha256(schema),
                "json_schema": schema,
            }
        )
    return {
        "version": 1,
        "consumer": consumer.value,
        "contracts": contracts,
    }


def validate_consumer_payload(
    consumer: PrivacyConsumer,
    contract_name: PrivacyContractName,
    payload: Mapping[str, Any],
) -> BaseModel:
    """按 consumer allowlist 验证 payload，不做跨 Feature fallback。"""

    if contract_name not in _contract_names(consumer):
        raise ValueError(f"CONTRACT_NOT_ALLOWED: {consumer.value}/{contract_name.value}")
    return _contract_model(consumer, contract_name).model_validate(payload)


__all__ = [
    "F154_HEALTH_DATA_TYPES",
    "F154_HEALTH_FIELD_MANIFEST",
    "F154_HEALTH_PURPOSE",
    "PrivacyConsumer",
    "PrivacyContractName",
    "privacy_contract_bundle",
    "validate_consumer_payload",
]
