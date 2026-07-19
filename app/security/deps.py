from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security.jwt import decode_access_token

# Authorization: Bearer <token> 헤더를 읽어주는 FastAPI 표준 보안 스킴
bearer_scheme = HTTPBearer(auto_error=False)


# Java의 JwtAuthenticationFilter 역할.
# 다만 Spring Security처럼 모든 요청에 자동으로 걸리는 게 아니라,
# 각 router에 Depends(get_current_admin)을 명시적으로 붙여줘야 적용됨.
def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.")

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰이 유효하지 않습니다.")

    return {"email": payload["sub"], "role": payload["role"]}


# Java SecurityConfig의 .hasRole("SUPER_ADMIN")에 대응
def require_super_admin(current: dict = Depends(get_current_admin)) -> dict:
    if current["role"] != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "super_admin만 접근 가능합니다.")
    return current
