-- Zhixing Banking Agent v4.0: 15 project tables, utf8mb4 / InnoDB.
-- Credentials are supplied through the process environment.
-- Startup applies idempotent schema migrations; do not replace an existing bank state.

CREATE TABLE IF NOT EXISTS zx_auth_identities (user_id VARCHAR(48) PRIMARY KEY, username VARCHAR(32) NOT NULL UNIQUE, display_name VARCHAR(80) NOT NULL, password_hash VARCHAR(200) NOT NULL, created_at DOUBLE NOT NULL) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_auth_sessions (token_hash CHAR(64) PRIMARY KEY, user_id VARCHAR(48) NOT NULL, is_demo BOOLEAN NOT NULL, created_at DOUBLE NOT NULL, last_seen DOUBLE NOT NULL, expires_at DOUBLE NOT NULL, INDEX ix_session_expiry(expires_at)) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_auth_attempts (bucket CHAR(64) PRIMARY KEY, failures INT NOT NULL, reset_at DOUBLE NOT NULL) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_schema_version (version INT PRIMARY KEY, applied_at DOUBLE NOT NULL) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_bank_state (id INT PRIMARY KEY, version BIGINT NOT NULL, payload JSON NOT NULL, updated_at DOUBLE NOT NULL) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_users (user_id VARCHAR(48) PRIMARY KEY, name_mask VARCHAR(80), phone_mask VARCHAR(32), branch_id VARCHAR(16) NOT NULL) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_accounts (user_id VARCHAR(48) PRIMARY KEY, account_mask VARCHAR(40), balance DECIMAL(18,2) NOT NULL, currency VARCHAR(8) NOT NULL, branch_id VARCHAR(16) NOT NULL) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_transactions (txn_id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(48) NOT NULL, branch_id VARCHAR(16) NOT NULL, amount DECIMAL(18,2) NOT NULL, direction VARCHAR(8) NOT NULL, category VARCHAR(64), channel VARCHAR(64), event_ts DOUBLE NOT NULL, INDEX ix_tx_time(branch_id,event_ts)) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_resources (kind VARCHAR(32) NOT NULL, resource_id VARCHAR(96) NOT NULL, owner_id VARCHAR(48), status VARCHAR(40), amount DECIMAL(18,2), payload JSON NOT NULL, PRIMARY KEY(kind,resource_id)) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_audit_log (hash CHAR(64) PRIMARY KEY, event_ts DOUBLE NOT NULL, operation VARCHAR(64), risk VARCHAR(16), success BOOLEAN, payload LONGTEXT NOT NULL, sequence_id BIGINT NOT NULL AUTO_INCREMENT UNIQUE, INDEX ix_audit_time(event_ts)) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_requests (request_id VARCHAR(64) PRIMARY KEY, session_alias VARCHAR(48), source VARCHAR(24) NOT NULL, scenario VARCHAR(32) NOT NULL, status VARCHAR(24) NOT NULL, duration_ms DOUBLE NOT NULL, event_ts DOUBLE NOT NULL, branch_id VARCHAR(16) NOT NULL, INDEX ix_req_time(branch_id,event_ts)) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_devices (device_id VARCHAR(48) PRIMARY KEY, branch_id VARCHAR(16) NOT NULL, name VARCHAR(80) NOT NULL, kind VARCHAR(40) NOT NULL, source VARCHAR(24) NOT NULL, status VARCHAR(24) NOT NULL, last_seen DOUBLE NOT NULL, latency_ms DOUBLE, cpu_pct DOUBLE, memory_pct DOUBLE, INDEX ix_device_branch(branch_id,status)) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_device_samples (id BIGINT AUTO_INCREMENT PRIMARY KEY, device_id VARCHAR(48) NOT NULL, event_ts DOUBLE NOT NULL, status VARCHAR(24), latency_ms DOUBLE, cpu_pct DOUBLE, memory_pct DOUBLE, INDEX ix_sample_time(event_ts,device_id)) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_work_orders (ticket_id VARCHAR(48) PRIMARY KEY, source_key VARCHAR(128) UNIQUE NOT NULL, device_id VARCHAR(48), branch_id VARCHAR(16) NOT NULL, title VARCHAR(120) NOT NULL, priority VARCHAR(8) NOT NULL, status VARCHAR(24) NOT NULL, created_at DOUBLE NOT NULL, assigned_at DOUBLE, resolved_at DOUBLE, sla_minutes INT NOT NULL, assignee VARCHAR(40), resolution VARCHAR(200), version INT NOT NULL DEFAULT 1, INDEX ix_ticket_scope(branch_id,created_at,status)) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS zx_ops_events (event_id BIGINT AUTO_INCREMENT PRIMARY KEY, event_ts DOUBLE NOT NULL, event_type VARCHAR(40) NOT NULL, branch_id VARCHAR(16) NOT NULL, ref_id VARCHAR(64), actor VARCHAR(48), payload LONGTEXT NOT NULL, INDEX ix_ops_time(event_ts)) ENGINE=InnoDB;
