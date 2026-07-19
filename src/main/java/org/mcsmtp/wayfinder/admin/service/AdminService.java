package org.mcsmtp.wayfinder.admin.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.admin.dto.AdminResponse;
import org.mcsmtp.wayfinder.admin.dto.LoginRequest;
import org.mcsmtp.wayfinder.admin.dto.LoginResponse;
import org.mcsmtp.wayfinder.admin.dto.SignupRequest;
import org.mcsmtp.wayfinder.admin.dto.UpdateStatusRequest;
import org.mcsmtp.wayfinder.admin.entity.Admin;
import org.mcsmtp.wayfinder.admin.repository.AdminRepository;
import org.mcsmtp.wayfinder.security.JwtProvider;
import org.springframework.beans.BeanUtils;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class AdminService {

    private final AdminRepository adminRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtProvider jwtProvider;

    public void signup(SignupRequest req) {
        if (adminRepository.findByEmail(req.getEmail()).isPresent()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "이미 가입된 이메일입니다.");
        }

        Admin admin = new Admin();
        admin.setEmail(req.getEmail());
        admin.setPasswordHash(passwordEncoder.encode(req.getPassword()));
        admin.setName(req.getName());
        admin.setOrg(req.getOrg());
        admin.setStatus("pending"); // 슈퍼관리자 승인 전까지 로그인 불가
        admin.setRole("admin");

        adminRepository.save(admin);
    }

    public LoginResponse login(LoginRequest req) {
        Admin admin = adminRepository.findByEmail(req.getEmail())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다."));

        if (!passwordEncoder.matches(req.getPassword(), admin.getPasswordHash())) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다.");
        }

        if (!"active".equals(admin.getStatus())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "승인 대기 중이거나 거절된 계정입니다.");
        }

        String token = jwtProvider.generateToken(admin.getEmail(), admin.getRole());
        return new LoginResponse(token);
    }

    // status가 null/빈값이면 전체 목록, 있으면 해당 상태만 (예: pending 목록 조회)
    public List<AdminResponse> list(String status) {
        List<Admin> admins = (status == null || status.isBlank())
                ? adminRepository.findAll()
                : adminRepository.findByStatus(status);

        return admins.stream().map(this::toResponse).toList();
    }

    // approverEmail: 지금 로그인한(=JWT 토큰의 주인) 슈퍼관리자의 이메일
    public AdminResponse updateStatus(String id, UpdateStatusRequest req, String approverEmail) {
        if (!"active".equals(req.getStatus()) && !"rejected".equals(req.getStatus())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "status는 active 또는 rejected여야 합니다.");
        }

        Admin admin = adminRepository.findById(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "계정 없음: " + id));

        Admin approver = adminRepository.findByEmail(approverEmail)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "승인자 정보를 확인할 수 없습니다."));

        admin.setStatus(req.getStatus());
        admin.setApprovedBy(approver.getId());
        admin.setApprovedAt(LocalDateTime.now());

        return toResponse(adminRepository.save(admin));
    }

    private AdminResponse toResponse(Admin admin) {
        AdminResponse response = new AdminResponse();
        BeanUtils.copyProperties(admin, response); // passwordHash는 AdminResponse에 필드가 없어서 자동으로 빠짐
        return response;
    }
}
