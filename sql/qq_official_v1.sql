-- REVIEWED TEMPLATE ONLY. User executes against the chosen empty official schema.
-- No USE, credentials, IF NOT EXISTS, old-table changes or automatic migration.
-- Not yet verified against a live MySQL/TiDB instance.
-- Version-one delivery structure finalized 2026-11-19. Execute manually only.
CREATE TABLE bindings_qqofficial (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    environment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    group_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    game_key VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    game_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    qq VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revision BIGINT UNSIGNED NOT NULL,
    last_operation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    bind_time DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_official_game (environment_id, group_id, game_key),
    KEY ix_official_user (environment_id, group_id, qq, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE bindings_qqofficial_ops (
    operation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    environment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    group_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    request_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    target_user_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    original_server_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    token_generation CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    reservation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    policy_revision BIGINT UNSIGNED NOT NULL,
    intent JSON NOT NULL,
    nickname_basis JSON NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    result_code VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    effects JSON NOT NULL,
    followup JSON NOT NULL,
    next_attempt_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (operation_id),
    UNIQUE KEY uq_official_request (environment_id, request_key),
    UNIQUE KEY uq_official_token (environment_id, original_server_id, token_generation),
    KEY ix_official_pending (environment_id, state, next_attempt_at),
    KEY ix_official_actor (environment_id, group_id, actor_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
