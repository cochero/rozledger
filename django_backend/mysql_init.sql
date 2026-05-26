CREATE DATABASE IF NOT EXISTS rozledger
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'rozledger_user'@'localhost'
  IDENTIFIED BY 'change-this-password';

GRANT ALL PRIVILEGES ON rozledger.* TO 'rozledger_user'@'localhost';
FLUSH PRIVILEGES;
