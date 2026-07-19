from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.admin import service
from app.admin.schemas import (
    AdminResponse,
    LoginRequest,
    LoginResponse,
    SignupRequest,
    UpdateStatusRequest,
)
from app.database import get_db
from app.security.deps import require_super_admin

# 로그인/회원가입 — 인증 필요 없음 (Java SecurityConfig의 permitAll에 대응)
auth_router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


@auth_router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    return service.login(db, req)


@auth_router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    service.signup(db, req)


# 가입 승인/거절 — super_admin 전용
accounts_router = APIRouter(
    prefix="/api/admin/accounts",
    tags=["admin-accounts"],
    dependencies=[Depends(require_super_admin)],
)


@accounts_router.get("", response_model=list[AdminResponse])
def list_admins(status: str | None = None, db: Session = Depends(get_db)):
    return service.list_admins(db, status)


@accounts_router.patch("/{admin_id}/status", response_model=AdminResponse)
def update_status(
    admin_id: str,
    req: UpdateStatusRequest,
    db: Session = Depends(get_db),
    current=Depends(require_super_admin),
):
    return service.update_status(db, admin_id, req, current["email"])
