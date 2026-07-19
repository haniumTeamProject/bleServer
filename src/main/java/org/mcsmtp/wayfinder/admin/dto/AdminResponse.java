package org.mcsmtp.wayfinder.admin.dto;

import lombok.Getter;
import lombok.Setter;

import java.time.LocalDateTime;

// 목록/승인 API 응답용. passwordHash는 절대 포함하지 않음
@Getter
@Setter
public class AdminResponse {
    private String id;
    private String email;
    private String name;
    private String org;
    private String position;
    private String phone;
    private String building;
    private String status;
    private String role;
    private String officialDocUrl;
    private String approvedBy;
    private LocalDateTime approvedAt;
    private LocalDateTime createdAt;
}
