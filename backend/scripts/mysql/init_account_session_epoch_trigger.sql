-- Fresh Docker volumes only. Existing volumes: use `flask --app app migrate-account-session-epoch`.
-- This trigger is deliberately separate from init_mysql.sql because init_mysql.py
-- uses a simple semicolon splitter and cannot parse trigger bodies.
DELIMITER //
CREATE TRIGGER cpa_user_account_auth_epoch_v1
BEFORE UPDATE ON user_account FOR EACH ROW
BEGIN
    IF NOT (NEW.status <=> OLD.status)
       OR NOT (NEW.role <=> OLD.role)
       OR NOT (NEW.password_hash <=> OLD.password_hash) THEN
        SET NEW.auth_epoch = OLD.auth_epoch + 1;
    ELSE
        SET NEW.auth_epoch = OLD.auth_epoch;
    END IF;
END//
DELIMITER ;
