package org.mcsmtp.wayfinder.building.repository;

import org.mcsmtp.wayfinder.building.entity.Building;
import org.springframework.data.jpa.repository.JpaRepository;

public interface BuildingRepository extends JpaRepository<Building, String> {
    // findAll(), findById(), save(), deleteById() 는 JpaRepository가 이미 제공
    // 추가로 필요한 조회는 메서드 이름만 선언하면 됨. 예:
    // List<Building> findByFavoriteTrue();
}