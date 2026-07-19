package org.mcsmtp.wayfinder.admin.repository;

import org.mcsmtp.wayfinder.admin.entity.Admin;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AdminRepository extends JpaRepository<Admin, String> {
    Optional<Admin> findByEmail(String email);
    List<Admin> findByStatus(String status);
}
