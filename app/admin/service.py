from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.admin.models import Admin
from app.admin.schemas import LoginRequest, LoginResponse, SignupRequest, UpdateStatusRequest
from app.security.jwt import create_access_token
from app.security.password import hash_password, verify_password


def signup(db: Session, req: SignupRequest) -> None:
    existing = db.query(Admin).filter(Admin.email == req.email).first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 가입된 이메일입니다.")

    admin = Admin(
        email=req.email,
        password_hash=hash_password(req.password),
        name=req.name,
        org=req.org,
        status="pending",  # 슈퍼관리자 승인 전까지 로그인 불가
        role="admin",
    )
    db.add(admin)
    db.commit()


def login(db: Session, req: LoginRequest) -> LoginResponse:
    admin = db.query(Admin).filter(Admin.email == req.email).first()
    if admin is None or not verify_password(req.password, admin.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다.")

    if admin.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "승인 대기 중이거나 거절된 계정입니다.")

    token = create_access_token(admin.email, admin.role)
    return LoginResponse(access_token=token)


def list_admins(db: Session, status_filter: str | None) -> list[Admin]:
    query = db.query(Admin)
    if status_filter:
        query = query.filter(Admin.status == status_filter)
    return query.all()


def update_status(db: Session, admin_id: str, req: UpdateStatusRequest, approver_email: str) -> Admin:
    if req.status not in ("active", "rejected"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status는 active 또는 rejected여야 합니다.")

    admin = db.get(Admin, admin_id)
    if admin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"계정 없음: {admin_id}")

    approver = db.query(Admin).filter(Admin.email == approver_email).first()
    if approver is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "승인자 정보를 확인할 수 없습니다.")

    admin.status = req.status
    admin.approved_by = approver.id
    admin.approved_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(admin)
    return admin
