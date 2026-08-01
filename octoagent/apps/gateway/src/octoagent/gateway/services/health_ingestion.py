"""F154 健康预览提交的单一应用编排。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from octoagent.core.models import (
    AuditDecision,
    AuditResult,
    DeviceCapability,
    PrivacyAuditEvent,
    PrivacyAuditEventType,
    ReviewBundle,
)
from octoagent.core.store import StoreGroup
from octoagent.core.store.device_trust_store import SqliteDeviceTrustStore
from octoagent.core.store.privacy_ingestion_store import SqlitePrivacyIngestionStore
from octoagent.policy.privacy_ingestion_policy import AuthorizedRequest
from octoagent.protocol.device_trust import DeviceProofHeaders
from octoagent.protocol.privacy_ingestion import (
    PrivacyConsumer,
    PrivacyContractName,
    validate_consumer_payload,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .mobile_device_auth import (
    DeviceAuthError,
    MobileAuthorizationRequest,
    authorize_mobile_request,
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _new_id() -> str:
    return str(uuid4())


def _identifier_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _health_auth_reason(reason_code: str) -> str:
    return {
        "CAPABILITY_DENIED": "HEALTH_CAPABILITY_DENIED",
        "REQUEST_REPLAYED": "HEALTH_REQUEST_REPLAYED",
    }.get(reason_code, reason_code)


def _health_validation_reason(exc: ValidationError | ValueError) -> str:
    if isinstance(exc, ValidationError) and any(
        error.get("type") == "extra_forbidden" for error in exc.errors()
    ):
        return "HEALTH_RAW_FIELD_FORBIDDEN"
    if "preview hash" in str(exc).casefold():
        return "HEALTH_PREVIEW_HASH_MISMATCH"
    return "HEALTH_REVIEW_INVALID"


@dataclass(frozen=True, slots=True)
class HealthIngestionServiceOptions:
    """health review 编排的可注入时钟与标识生成器。"""

    clock: Callable[[], datetime] = _utc_now
    id_factory: Callable[[], str] = _new_id


class HealthReviewAccepted(BaseModel):
    """review durable 后返回给 iOS 的最小非敏感确认。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime


class HealthIngestionError(ValueError):
    """只向 route 暴露稳定 reason code，不携带健康事实。"""

    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class HealthIngestionService:
    """复用 F153 device proof 与 F152 store/audit 的唯一 health service。"""

    def __init__(
        self,
        *,
        device_store: SqliteDeviceTrustStore,
        privacy_store: SqlitePrivacyIngestionStore,
        options: HealthIngestionServiceOptions,
    ) -> None:
        self._device_store = device_store
        self._privacy_store = privacy_store
        self._clock = options.clock
        self._id_factory = options.id_factory

    async def submit_review(
        self,
        *,
        headers: DeviceProofHeaders,
        method: str,
        canonical_path: str,
        raw_body: bytes,
        payload: Mapping[str, Any],
    ) -> HealthReviewAccepted:
        """验设备证明后验证并持久化一个 exact F154 ReviewBundle。"""

        now = self._clock()
        authorized = await self._authorize(
            headers=headers,
            method=method,
            canonical_path=canonical_path,
            raw_body=raw_body,
            now=now,
        )
        review = self._validate_review(payload)
        owner_id = await self._require_review_scope(
            device_id=authorized.device_id,
            review=review,
        )
        bundle_sha256 = await self._persist_review(
            review=review,
            owner_id=owner_id,
            device_id=authorized.device_id,
            now=now,
        )
        return HealthReviewAccepted(
            bundle_sha256=bundle_sha256,
            expires_at=review.retention.expires_at,
        )

    async def _authorize(
        self,
        *,
        headers: DeviceProofHeaders,
        method: str,
        canonical_path: str,
        raw_body: bytes,
        now: datetime,
    ) -> AuthorizedRequest:
        try:
            return await authorize_mobile_request(
                self._device_store,
                MobileAuthorizationRequest(
                    headers=headers,
                    method=method,
                    canonical_path=canonical_path,
                    raw_body=raw_body,
                    required_capability=DeviceCapability.HEALTH_REVIEW_SUBMIT.value,
                    now=now,
                ),
            )
        except DeviceAuthError as exc:
            raise HealthIngestionError(
                _health_auth_reason(exc.reason_code),
                status_code=401,
            ) from exc

    @staticmethod
    def _validate_review(payload: Mapping[str, Any]) -> ReviewBundle:
        try:
            validated = validate_consumer_payload(
                PrivacyConsumer.F154,
                PrivacyContractName.REVIEW_BUNDLE,
                payload,
            )
        except (ValidationError, ValueError) as exc:
            raise HealthIngestionError(
                _health_validation_reason(exc),
                status_code=422,
            ) from exc
        if not isinstance(validated, ReviewBundle):
            raise HealthIngestionError("HEALTH_REVIEW_INVALID", status_code=422)
        return validated

    async def _require_review_scope(
        self,
        *,
        device_id: str,
        review: ReviewBundle,
    ) -> str:
        provenance = review.provenance[0]
        device = await self._device_store.get_device(device_id)
        if device is None:
            raise HealthIngestionError("DEVICE_NOT_FOUND", status_code=401)
        if provenance.owner_id != device.owner_id or provenance.device_id != device_id:
            raise HealthIngestionError("HEALTH_REVIEW_SCOPE_MISMATCH", status_code=403)
        return device.owner_id

    async def _persist_review(
        self,
        *,
        review: ReviewBundle,
        owner_id: str,
        device_id: str,
        now: datetime,
    ) -> str:
        provenance = review.provenance[0]
        try:
            bundle_sha256 = await self._privacy_store.put_review_bundle(review)
            await self._privacy_store.append_audit(
                PrivacyAuditEvent(
                    event_id=self._id_factory(),
                    event_type=PrivacyAuditEventType.PACKET_SENT,
                    owner_hash=_identifier_hash(owner_id),
                    device_hash=_identifier_hash(device_id),
                    object_hash=bundle_sha256,
                    count=review.fact_count,
                    data_types=provenance.data_types,
                    capabilities=(DeviceCapability.HEALTH_REVIEW_SUBMIT,),
                    decision=AuditDecision.ALLOW,
                    result=AuditResult.SUCCESS,
                    reason_code="HEALTH_REVIEW_STORED",
                    occurred_at=now,
                )
            )
        except ValueError as exc:
            raise HealthIngestionError("HEALTH_REVIEW_INVALID", status_code=422) from exc
        return bundle_sha256


def build_health_ingestion_service(
    *,
    store_group: StoreGroup,
) -> HealthIngestionService:
    """从既有 StoreGroup 构造唯一 health ingestion service。"""

    return HealthIngestionService(
        device_store=store_group.device_trust_store,
        privacy_store=store_group.privacy_ingestion_store,
        options=HealthIngestionServiceOptions(),
    )


__all__ = [
    "HealthIngestionError",
    "HealthIngestionService",
    "HealthIngestionServiceOptions",
    "HealthReviewAccepted",
    "build_health_ingestion_service",
]
