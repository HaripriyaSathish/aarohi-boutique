-- Run this once in MySQL Workbench (connected as root).
-- Replace the password below, and put the SAME password in .env as DB_PASSWORD.
CREATE DATABASE IF NOT EXISTS aarohi_boutique
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'aarohi_user'@'localhost' IDENTIFIED BY 'ChooseAStrongPassword123!';
GRANT ALL PRIVILEGES ON aarohi_boutique.* TO 'aarohi_user'@'localhost';
-- Django's test runner needs to create test_aarohi_boutique:
GRANT ALL PRIVILEGES ON test_aarohi_boutique.* TO 'aarohi_user'@'localhost';
FLUSH PRIVILEGES;
