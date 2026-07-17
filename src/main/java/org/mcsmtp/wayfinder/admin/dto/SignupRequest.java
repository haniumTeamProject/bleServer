package org.mcsmtp.wayfinder.admin.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class SignupRequest {
    private String email;
    private String password;
    private String name;
    private String org; // 소속 기관
    // officialDoc(공문 파일)은 현재 프론트에서 별도 전송하지 않음 —
    // 나중에 파일 업로드 붙이면 별도 multipart 엔드포인트로 처리
}
