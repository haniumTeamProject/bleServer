package org.mcsmtp.wayfinder.admin.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;
import java.util.UUID;

@Entity
@Table(name = "admins")
@Getter
@Setter
@NoArgsConstructor
public class Admin {

    @Id
    private String id = UUID.randomUUID().toString();

    private String email;

    // BCrypt로 해시된 값만 저장 (평문 저장 금지)
    private String passwordHash;

    private String name;
    private String org; // 소속 기관
    private String position;
    private String phone;
    private String building; // 담당 건물

    // pending | active | rejected
    private String status = "pending";

    // super_admin | admin
    private String role = "admin";

    private String officialDocUrl; // 기관 공문(직인) — 추후 파일 업로드 기능 붙을 때 채워짐

    // 승인 감사 로그 — 승인/거절 처리한 슈퍼관리자의 id (승인 API 만들 때 채워짐)
    private String approvedBy;
    private LocalDateTime approvedAt;

    @CreationTimestamp
    private LocalDateTime createdAt;
}
