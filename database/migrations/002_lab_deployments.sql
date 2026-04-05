-- Run against existing VDI DB if init.sql was applied before lab tables existed:
--   psql -h ... -U ... -d vdi -f database/migrations/002_lab_deployments.sql

CREATE TYPE lab_deployment_status AS ENUM (
    'pending',
    'processing',
    'completed',
    'failed',
    'partial'
);

CREATE TABLE lab_deployments (
    deployment_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES desktop_pools (pool_id) ON DELETE CASCADE,
    teacher_user_id UUID REFERENCES users (user_id) ON DELETE SET NULL,
    lab_title VARCHAR(255) NOT NULL,
    portal_url TEXT,
    roster_json JSONB NOT NULL,
    status lab_deployment_status NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX idx_lab_deployments_pool ON lab_deployments (pool_id, created_at DESC);
CREATE INDEX idx_lab_deployments_status ON lab_deployments (status);

CREATE TABLE lab_access_codes (
    seat_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deployment_id UUID NOT NULL REFERENCES lab_deployments (deployment_id) ON DELETE CASCADE,
    access_code CHAR(6) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL,
    student_external_id VARCHAR(100),
    full_name VARCHAR(255),
    openstack_server_id VARCHAR(255),
    instance_id UUID REFERENCES desktop_instances (instance_id) ON DELETE SET NULL,
    user_id UUID REFERENCES users (user_id) ON DELETE SET NULL,
    vm_error TEXT,
    email_sent_at TIMESTAMP,
    email_last_error TEXT,
    email_attempts INTEGER NOT NULL DEFAULT 0,
    resend_message_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_lab_seat_deployment_email UNIQUE (deployment_id, email)
);

CREATE INDEX idx_lab_access_codes_code ON lab_access_codes (access_code);
CREATE INDEX idx_lab_access_codes_deployment ON lab_access_codes (deployment_id);
