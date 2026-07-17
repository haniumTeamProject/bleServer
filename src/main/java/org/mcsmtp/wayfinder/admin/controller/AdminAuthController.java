package org.mcsmtp.wayfinder.admin.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.admin.dto.LoginRequest;
import org.mcsmtp.wayfinder.admin.dto.LoginResponse;
import org.mcsmtp.wayfinder.admin.dto.SignupRequest;
import org.mcsmtp.wayfinder.admin.service.AdminService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/admin/auth")
@RequiredArgsConstructor
public class AdminAuthController {

    private final AdminService adminService;

    @PostMapping("/login")
    public LoginResponse login(@RequestBody LoginRequest req) {
        return adminService.login(req);
    }

    @PostMapping("/signup")
    @ResponseStatus(HttpStatus.CREATED)
    public void signup(@RequestBody SignupRequest req) {
        adminService.signup(req);
    }
}
