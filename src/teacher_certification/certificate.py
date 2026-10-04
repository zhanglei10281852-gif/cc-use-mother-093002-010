"""认证证书：同一候选人至多一张有效证书。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class CertificateStatus(Enum):
    ACTIVE = "有效"
    REVOKED = "已注销"


@dataclass
class Certificate:
    certificate_id: str
    candidate_id: str
    case_id: str
    decision_id: str
    serial_no: str
    issued_on: date
    status: CertificateStatus = CertificateStatus.ACTIVE
