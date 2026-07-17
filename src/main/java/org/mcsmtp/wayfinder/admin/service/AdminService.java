package org.mcsmtp.wayfinder.admin.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.admin.dto.LoginRequest;
import org.mcsmtp.wayfinder.admin.dto.LoginResponse;
import org.mcsmtp.wayfinder.admin.dto.SignupRequest;
import org.mcsmtp.wayfinder.admin.entity.Admin;
import org.mcsmtp.wayfinder.admin.repository.AdminRepository;
import org.mcsmtp.wayfinder.security.JwtProvider;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

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
        admin.setPassword(passwordEncoder.encode(req.getPassword()));
        admin.setName(req.getName());
        admin.setOrg(req.getOrg());
        admin.setStatus("pending"); // 슈퍼관리자 승인 전까지 로그인 불가
        admin.setRole("admin");

        adminRepository.save(admin);
    }

    public LoginResponse login(LoginRequest req) {
        Admin admin = adminRepository.findByEmail(req.getEmail())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다."));

        if (!passwordEncoder.matches(req.getPassword(), admin.getPassword())) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다.");
        }

        if (!"active".equals(admin.getStatus())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "승인 대기 중이거나 거절된 계정입니다.");
        }

        String token = jwtProvider.generateToken(admin.getEmail(), admin.getRole());
        return new LoginResponse(token);
    }
}
