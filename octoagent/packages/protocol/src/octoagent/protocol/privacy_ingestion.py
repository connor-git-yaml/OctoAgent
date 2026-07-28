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
    DeviceIdentity,
    IngestionRetention,
    IngestionStage,
    MemoryCandidateConfirmation,
    OptionalMemoryCandidate,
    RequestProofPayload,
    ReviewBundle,
    canonical_sha256,
)
from pydantic import BaseModel


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


def _contract_model(name: PrivacyContractName) -> type[BaseModel]:
    for known_name, model in _CONTRACT_MODELS:
        if known_name is name:
            return model
    raise ValueError(f"UNKNOWN_CONTRACT: {name}")


def privacy_contract_bundle(consumer: PrivacyConsumer) -> dict[str, Any]:
    """返回每次新建的 deterministic exact JSON-schema bundle。"""

    contracts: list[dict[str, Any]] = []
    for name in _contract_names(consumer):
        schema = _contract_model(name).model_json_schema()
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
    return _contract_model(contract_name).model_validate(payload)


__all__ = [
    "PrivacyConsumer",
    "PrivacyContractName",
    "privacy_contract_bundle",
    "validate_consumer_payload",
]
