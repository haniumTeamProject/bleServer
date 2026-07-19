from datetime import datetime

from app.common import CamelModel


class LoginRequest(CamelModel):
    email: str
    password: str


class LoginResponse(CamelModel):
    access_token: str


class SignupRequest(CamelModel):
    email: str
    password: str
    name: str
    org: str
    # officialDoc(공문 파일)은 현재 프론트에서 별도 전송 안 함 —
    # 나중에 파일 업로드 붙이면 별도 multipart 엔드포인트로 처리


class UpdateStatusRequest(CamelModel):
    status: str  # active | rejected


# 목록/승인 API 응답용. password_hash는 절대 포함하지 않음
class AdminResponse(CamelModel):
    id: str
    email: str
    name: str | None
    org: str | None
    position: str | None
    phone: str | None
    building: str | None
    status: str
    role: str
    official_doc_url: str | None
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
