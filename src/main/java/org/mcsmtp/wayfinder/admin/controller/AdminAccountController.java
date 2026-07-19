package org.mcsmtp.wayfinder.admin.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.admin.dto.AdminResponse;
import org.mcsmtp.wayfinder.admin.dto.UpdateStatusRequest;
import org.mcsmtp.wayfinder.admin.service.AdminService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;

// 가입 신청 목록 조회 + 승인/거절. super_admin만 접근 가능 (SecurityConfig에서 제한)
@RestController
@RequestMapping("/api/admin/accounts")
@RequiredArgsConstructor
public class AdminAccountController {

    private final AdminService adminService;

    @GetMapping
    public List<AdminResponse> list(@RequestParam(required = false) String status) {
        return adminService.list(status);
    }

    @PatchMapping("/{id}/status")
    public AdminResponse updateStatus(
            @PathVariable String id,
            @RequestBody UpdateStatusRequest req,
            Authentication authentication
    ) {
        // JwtAuthenticationFilter가 principal로 로그인한 관리자의 email을 넣어둠
        String approverEmail = authentication.getName();
        return adminService.updateStatus(id, req, approverEmail);
    }
}
