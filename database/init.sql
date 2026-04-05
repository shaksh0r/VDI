-- ============================================================================
-- VDI System - PostgreSQL Database Schema
-- Virtual Desktop Infrastructure on OpenStack
-- Version: 1.0.0
-- ============================================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pg_trgm for text search (optional but useful)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- ENUMS
-- ============================================================================

-- Desktop pool types
CREATE TYPE desktop_type AS ENUM ('persistent', 'non_persistent');

-- VM instance statuses
CREATE TYPE instance_status AS ENUM (
    'provisioning',
    'ready',
    'assigned',
    'in_use',
    'stopping',
    'stopped',
    'error',
    'deleting',
    'deleted'
);

-- Job types for provisioning
CREATE TYPE job_type AS ENUM (
    'create_vm',
    'delete_vm',
    'start_vm',
    'stop_vm',
    'restart_vm',
    'scale_up',
    'scale_down'
);

-- Job statuses
CREATE TYPE job_status AS ENUM (
    'queued',
    'processing',
    'completed',
    'failed',
    'cancelled'
);

-- User roles
CREATE TYPE user_role AS ENUM (
    'student',
    'faculty',
    'admin',
    'guest'
);

-- Pool status
CREATE TYPE pool_status AS ENUM (
    'active',
    'inactive',
    'error',
    'deleted'
);

-- Release reasons
CREATE TYPE release_reason AS ENUM (
    'user_logout',
    'timeout',
    'admin_action',
    'vm_error',
    'session_expired'
);

-- Health check types
CREATE TYPE check_type AS ENUM (
    'ping',
    'rdp',
    'vnc',
    'spice',
    'ssh',
    'http'
);

-- ============================================================================
-- TABLES
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Users Table (Authentication Service)
-- ----------------------------------------------------------------------------
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4 (),
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    student_id VARCHAR(50), -- For students
    department VARCHAR(100),
    role user_role NOT NULL DEFAULT 'student',
    is_active BOOLEAN DEFAULT TRUE,
    email_verified BOOLEAN DEFAULT FALSE,
    last_login_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

-- Indexes for users
CREATE INDEX idx_users_username ON users (username)
WHERE
    deleted_at IS NULL;

CREATE INDEX idx_users_email ON users (email)
WHERE
    deleted_at IS NULL;

CREATE INDEX idx_users_role ON users (role);

CREATE INDEX idx_users_student_id ON users (student_id)
WHERE
    student_id IS NOT NULL;

-- Comments
COMMENT ON TABLE users IS 'System users (students, faculty, admins)';

COMMENT ON COLUMN users.password_hash IS 'bcrypt hashed password';

COMMENT ON COLUMN users.student_id IS 'University student ID number';

-- ----------------------------------------------------------------------------
-- User Sessions (for JWT token management)
-- ----------------------------------------------------------------------------
CREATE TABLE user_sessions (
    session_id UUID PRIMARY KEY DEFAULT uuid_generate_v4 (),
    user_id UUID NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL, -- SHA-256 hash of JWT token
    ip_address INET,
    user_agent TEXT,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for sessions
CREATE INDEX idx_sessions_user_id ON user_sessions (user_id);

CREATE INDEX idx_sessions_token_hash ON user_sessions (token_hash);

CREATE INDEX idx_sessions_expires_at ON user_sessions (expires_at);

COMMENT ON TABLE user_sessions IS 'Active user sessions and JWT tokens';

COMMENT ON COLUMN user_sessions.token_hash IS 'Hash of JWT token for blacklist checking';

-- ----------------------------------------------------------------------------
-- Desktop Pools
-- ----------------------------------------------------------------------------
CREATE TABLE desktop_pools (
    pool_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,

-- OpenStack Configuration
base_image_id VARCHAR(255) NOT NULL, -- Glance image UUID
flavor_id VARCHAR(255) NOT NULL, -- OpenStack flavor (m1.small, m1.medium, etc)
network_id VARCHAR(255) NOT NULL, -- Neutron network UUID
security_group_id VARCHAR(255), -- Security group UUID

-- Pool Sizing
min_vms INTEGER NOT NULL DEFAULT 1 CHECK (min_vms >= 0),
max_vms INTEGER NOT NULL CHECK (max_vms >= min_vms),
current_count INTEGER NOT NULL DEFAULT 0 CHECK (current_count >= 0),

-- Pool Configuration
desktop_type desktop_type NOT NULL,
auto_scaling_enabled BOOLEAN DEFAULT TRUE,
status pool_status DEFAULT 'active',

-- Access Control
allowed_roles user_role[] DEFAULT '{student, faculty}', -- Array of allowed roles
max_session_duration_minutes INTEGER DEFAULT 240, -- 4 hours default

-- Metadata
created_by UUID REFERENCES users (user_id),
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
deleted_at TIMESTAMP,

-- Constraints
CONSTRAINT check_pool_name_length CHECK (char_length(name) >= 3),
    CONSTRAINT check_current_not_exceed_max CHECK (current_count <= max_vms)
);

-- Indexes for desktop_pools
CREATE INDEX idx_pools_name ON desktop_pools (name)
WHERE
    deleted_at IS NULL;

CREATE INDEX idx_pools_status ON desktop_pools (status);

CREATE INDEX idx_pools_created_by ON desktop_pools (created_by);

CREATE INDEX idx_pools_deleted_at ON desktop_pools (deleted_at);

-- Full-text search on pool name and description
CREATE INDEX idx_pools_search ON desktop_pools USING gin (
    to_tsvector(
        'english',
        name || ' ' || COALESCE(description, '')
    )
);

COMMENT ON TABLE desktop_pools IS 'Configuration for desktop VM pools';

COMMENT ON COLUMN desktop_pools.base_image_id IS 'Glance image UUID (ubuntu-22.04-desktop, etc)';

COMMENT ON COLUMN desktop_pools.flavor_id IS 'OpenStack flavor name';

COMMENT ON COLUMN desktop_pools.allowed_roles IS 'Which user roles can access this pool';

-- ----------------------------------------------------------------------------
-- Desktop Instances (Virtual Machines)
-- ----------------------------------------------------------------------------
CREATE TABLE desktop_instances (
    instance_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES desktop_pools(pool_id) ON DELETE CASCADE,

-- OpenStack Resource IDs
openstack_vm_id VARCHAR(255) UNIQUE, -- Nova server UUID
volume_id VARCHAR(255), -- Cinder volume UUID (for persistent)
floating_ip INET, -- Public IP address
private_ip INET, -- Private IP address
hostname VARCHAR(255),

-- Instance Configuration
status instance_status NOT NULL DEFAULT 'provisioning',
vcpus INTEGER,
ram_mb INTEGER,
disk_gb INTEGER,

-- Assignment
assigned_user_id UUID REFERENCES users (user_id) ON DELETE SET NULL,
assigned_at TIMESTAMP,
last_accessed_at TIMESTAMP,

-- Connection Details (stored as JSON)
connection_details JSONB,

-- Metadata
provisioned_at TIMESTAMP,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

-- Health Monitoring
health_check_failures INTEGER DEFAULT 0,
last_health_check_at TIMESTAMP,

-- Constraints
CONSTRAINT check_vcpus_positive CHECK (vcpus > 0 OR vcpus IS NULL),
    CONSTRAINT check_ram_positive CHECK (ram_mb > 0 OR ram_mb IS NULL),
    CONSTRAINT check_disk_positive CHECK (disk_gb > 0 OR disk_gb IS NULL),
    CONSTRAINT check_health_failures CHECK (health_check_failures >= 0)
);

-- Indexes for desktop_instances
CREATE INDEX idx_instances_pool_id ON desktop_instances (pool_id);

CREATE INDEX idx_instances_status ON desktop_instances (status);

CREATE INDEX idx_instances_pool_status ON desktop_instances (pool_id, status);
-- Composite for queries
CREATE INDEX idx_instances_assigned_user ON desktop_instances (assigned_user_id)
WHERE
    assigned_user_id IS NOT NULL;

CREATE INDEX idx_instances_openstack_id ON desktop_instances (openstack_vm_id)
WHERE
    openstack_vm_id IS NOT NULL;

CREATE INDEX idx_instances_floating_ip ON desktop_instances (floating_ip)
WHERE
    floating_ip IS NOT NULL;

CREATE INDEX idx_instances_last_accessed ON desktop_instances (last_accessed_at);

-- GIN index for JSONB connection_details
CREATE INDEX idx_instances_connection_details ON desktop_instances USING gin (connection_details);

COMMENT ON TABLE desktop_instances IS 'Individual virtual desktop VM instances';

COMMENT ON COLUMN desktop_instances.connection_details IS 'JSON: {protocol, host, port, username, password_hash}';

COMMENT ON COLUMN desktop_instances.health_check_failures IS 'Consecutive health check failures';

-- ----------------------------------------------------------------------------
-- Provisioning Jobs
-- ----------------------------------------------------------------------------
CREATE TABLE provisioning_jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES desktop_pools(pool_id) ON DELETE CASCADE,
    instance_id UUID REFERENCES desktop_instances(instance_id) ON DELETE SET NULL,

-- Job Configuration
job_type job_type NOT NULL,
status job_status NOT NULL DEFAULT 'queued',
priority INTEGER DEFAULT 5 CHECK (priority BETWEEN 0 AND 10), -- 0=low, 10=high

-- Error Handling
error_message TEXT,
error_details JSONB,
retry_count INTEGER DEFAULT 0,
max_retries INTEGER DEFAULT 3,

-- Job Parameters (flexible JSON)
job_params JSONB,

-- Timing
queued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
started_at TIMESTAMP,
completed_at TIMESTAMP,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

-- Constraints
CONSTRAINT check_retry_count CHECK (retry_count <= max_retries),
    CONSTRAINT check_started_after_queued CHECK (started_at IS NULL OR started_at >= queued_at),
    CONSTRAINT check_completed_after_started CHECK (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at))
);

-- Indexes for provisioning_jobs
CREATE INDEX idx_jobs_pool_id ON provisioning_jobs (pool_id);

CREATE INDEX idx_jobs_instance_id ON provisioning_jobs (instance_id)
WHERE
    instance_id IS NOT NULL;

CREATE INDEX idx_jobs_status ON provisioning_jobs (status);

CREATE INDEX idx_jobs_status_queued ON provisioning_jobs (status, queued_at)
WHERE
    status = 'queued';
-- For job processing
CREATE INDEX idx_jobs_created_at ON provisioning_jobs (created_at);

CREATE INDEX idx_jobs_pool_status ON provisioning_jobs (pool_id, status);

COMMENT ON TABLE provisioning_jobs IS 'Async jobs for VM provisioning and lifecycle';

COMMENT ON COLUMN provisioning_jobs.priority IS '0-10, higher = more important';

COMMENT ON COLUMN provisioning_jobs.job_params IS 'Additional parameters specific to job type';

-- ----------------------------------------------------------------------------
-- User Assignments (Desktop Sessions)
-- ----------------------------------------------------------------------------
CREATE TABLE user_assignments (
    assignment_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    instance_id UUID NOT NULL REFERENCES desktop_instances(instance_id) ON DELETE CASCADE,
    pool_id UUID NOT NULL REFERENCES desktop_pools(pool_id) ON DELETE CASCADE,

-- Assignment Type
assignment_type VARCHAR(50) NOT NULL CHECK (
    assignment_type IN ('persistent', 'temporary')
),

-- Session Timing
assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
released_at TIMESTAMP,
session_duration_seconds INTEGER,

-- Session Metadata
release_reason release_reason,
session_metadata JSONB, -- Bandwidth used, apps opened, etc.

-- Metadata
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

-- Constraints
CONSTRAINT check_released_after_assigned CHECK (released_at IS NULL OR released_at >= assigned_at),
    CONSTRAINT check_session_duration CHECK (session_duration_seconds IS NULL OR session_duration_seconds >= 0)
);

-- Indexes for user_assignments
CREATE INDEX idx_assignments_user_id ON user_assignments (user_id);

CREATE INDEX idx_assignments_instance_id ON user_assignments (instance_id);

CREATE INDEX idx_assignments_pool_id ON user_assignments (pool_id);

CREATE INDEX idx_assignments_active ON user_assignments (user_id, released_at)
WHERE
    released_at IS NULL;
-- Active sessions
CREATE INDEX idx_assignments_assigned_at ON user_assignments (assigned_at);

-- GIN index for session metadata
CREATE INDEX idx_assignments_metadata ON user_assignments USING gin (session_metadata);

COMMENT ON TABLE user_assignments IS 'History of VM-to-user assignments and sessions';

COMMENT ON COLUMN user_assignments.session_metadata IS 'JSON: bandwidth, apps_used, activity_logs';

-- ----------------------------------------------------------------------------
-- Scaling Policies
-- ----------------------------------------------------------------------------
CREATE TABLE scaling_policies (
    policy_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID UNIQUE NOT NULL REFERENCES desktop_pools(pool_id) ON DELETE CASCADE,

-- Scale Up Configuration
scale_up_threshold_percent INTEGER DEFAULT 20 CHECK (
    scale_up_threshold_percent BETWEEN 0 AND 100
),
scale_up_increment INTEGER DEFAULT 2 CHECK (scale_up_increment > 0),

-- Scale Down Configuration
scale_down_threshold_percent INTEGER DEFAULT 50 CHECK (
    scale_down_threshold_percent BETWEEN 0 AND 100
),
scale_down_increment INTEGER DEFAULT 1 CHECK (scale_down_increment > 0),

-- Policy Configuration
cooldown_period_seconds INTEGER DEFAULT 300 CHECK (cooldown_period_seconds >= 0),
min_available_vms INTEGER DEFAULT 2 CHECK (min_available_vms >= 0),
enabled BOOLEAN DEFAULT TRUE,

-- Advanced Rules (JSON)
advanced_rules JSONB, -- Time-based rules, custom metrics, etc.

-- State
last_scaled_at TIMESTAMP,
last_scale_action VARCHAR(50), -- 'scale_up' or 'scale_down'

-- Metadata
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

-- Constraints
CONSTRAINT check_scale_up_less_than_down CHECK (scale_up_threshold_percent < scale_down_threshold_percent)
);

-- Indexes for scaling_policies
CREATE UNIQUE INDEX idx_policies_pool_id ON scaling_policies (pool_id);

CREATE INDEX idx_policies_enabled ON scaling_policies (enabled)
WHERE
    enabled = TRUE;

COMMENT ON TABLE scaling_policies IS 'Auto-scaling configuration per pool';

COMMENT ON COLUMN scaling_policies.scale_up_threshold_percent IS 'Scale up when available% < this';

COMMENT ON COLUMN scaling_policies.scale_down_threshold_percent IS 'Scale down when idle% > this';

COMMENT ON COLUMN scaling_policies.advanced_rules IS 'JSON: time-based rules, ML predictions, etc.';

-- ----------------------------------------------------------------------------
-- Pool Metrics (Time-Series Data)
-- ----------------------------------------------------------------------------
CREATE TABLE pool_metrics (
    metric_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES desktop_pools(pool_id) ON DELETE CASCADE,

-- VM Counts
total_vms INTEGER NOT NULL,
available_vms INTEGER NOT NULL,
assigned_vms INTEGER NOT NULL,
in_use_vms INTEGER NOT NULL,
stopped_vms INTEGER NOT NULL,
error_vms INTEGER NOT NULL,

-- Calculated Metrics
utilization_percent NUMERIC(5, 2) CHECK (
    utilization_percent BETWEEN 0 AND 100
),
active_users INTEGER NOT NULL DEFAULT 0,
queued_requests INTEGER NOT NULL DEFAULT 0,

-- Performance Metrics
avg_provisioning_time_seconds NUMERIC(10, 2),
avg_session_duration_seconds NUMERIC(10, 2),

-- Timestamp
recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

-- Constraints
CONSTRAINT check_metric_counts_positive CHECK (
        total_vms >= 0 AND
        available_vms >= 0 AND
        assigned_vms >= 0 AND
        in_use_vms >= 0 AND
        stopped_vms >= 0 AND
        error_vms >= 0
    )
);

-- Indexes for pool_metrics (time-series queries)
CREATE INDEX idx_metrics_pool_id ON pool_metrics (pool_id, recorded_at DESC);

CREATE INDEX idx_metrics_recorded_at ON pool_metrics (recorded_at);

-- Partitioning hint: Consider partitioning by recorded_at for large datasets
-- ALTER TABLE pool_metrics PARTITION BY RANGE (recorded_at);

COMMENT ON TABLE pool_metrics IS 'Time-series metrics for pool monitoring';

COMMENT ON COLUMN pool_metrics.recorded_at IS 'Metric collection timestamp';

-- ----------------------------------------------------------------------------
-- VM Health Checks
-- ----------------------------------------------------------------------------
CREATE TABLE vm_health_checks (
    check_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    instance_id UUID NOT NULL REFERENCES desktop_instances(instance_id) ON DELETE CASCADE,

-- Check Configuration
check_type check_type NOT NULL,
is_healthy BOOLEAN NOT NULL,
response_time_ms INTEGER CHECK (response_time_ms >= 0),

-- Error Details
error_message TEXT, check_details JSONB,

-- Timestamp
checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP );

-- Indexes for health checks
CREATE INDEX idx_health_instance_id ON vm_health_checks (instance_id, checked_at DESC);

CREATE INDEX idx_health_unhealthy ON vm_health_checks (is_healthy, checked_at)
WHERE
    is_healthy = FALSE;

CREATE INDEX idx_health_checked_at ON vm_health_checks (checked_at);

-- Partitioning hint for large datasets
-- ALTER TABLE vm_health_checks PARTITION BY RANGE (checked_at);

COMMENT ON TABLE vm_health_checks IS 'Health check history for VMs';

COMMENT ON COLUMN vm_health_checks.check_details IS 'JSON: ping_loss, tcp_errors, etc.';

-- ----------------------------------------------------------------------------
-- Instance Snapshots (Backup/Restore)
-- ----------------------------------------------------------------------------
CREATE TABLE instance_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    instance_id UUID NOT NULL REFERENCES desktop_instances(instance_id) ON DELETE CASCADE,

-- OpenStack Snapshot
openstack_snapshot_id VARCHAR(255) UNIQUE, -- Glance snapshot UUID
name VARCHAR(255) NOT NULL,
description TEXT,

-- Snapshot Configuration
snapshot_type VARCHAR(50) NOT NULL CHECK (
    snapshot_type IN (
        'manual',
        'auto',
        'pre_update'
    )
),
size_bytes BIGINT CHECK (size_bytes >= 0),
status VARCHAR(50) DEFAULT 'creating' CHECK (
    status IN (
        'creating',
        'available',
        'error',
        'deleting'
    )
),

-- Metadata
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
deleted_at TIMESTAMP,

-- Constraints
CONSTRAINT check_snapshot_name_length CHECK (char_length(name) >= 3)
);

-- Indexes for snapshots
CREATE INDEX idx_snapshots_instance_id ON instance_snapshots (instance_id, created_at DESC);

CREATE INDEX idx_snapshots_status ON instance_snapshots (status);

CREATE INDEX idx_snapshots_openstack_id ON instance_snapshots (openstack_snapshot_id)
WHERE
    openstack_snapshot_id IS NOT NULL;

COMMENT ON TABLE instance_snapshots IS 'VM snapshots for backup/restore';

-- ----------------------------------------------------------------------------
-- Audit Logs (Security & Compliance)
-- ----------------------------------------------------------------------------
CREATE TABLE audit_logs (
    log_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

-- Who
user_id UUID REFERENCES users (user_id) ON DELETE SET NULL,
username VARCHAR(255), -- Denormalized for deleted users

-- What
action VARCHAR(100) NOT NULL, -- 'create_pool', 'delete_vm', 'login', etc.
resource_type VARCHAR(50), -- 'pool', 'instance', 'user'
resource_id UUID,

-- Details
old_values JSONB, -- State before action
new_values JSONB, -- State after action

-- Context
ip_address INET, user_agent TEXT, api_endpoint VARCHAR(255),

-- Result
success BOOLEAN NOT NULL, error_message TEXT,

-- Timestamp
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP );

-- Indexes for audit logs
CREATE INDEX idx_audit_user_id ON audit_logs (user_id, created_at DESC);

CREATE INDEX idx_audit_action ON audit_logs (action);

CREATE INDEX idx_audit_resource ON audit_logs (resource_type, resource_id);

CREATE INDEX idx_audit_created_at ON audit_logs (created_at);

CREATE INDEX idx_audit_ip_address ON audit_logs (ip_address);

-- Partitioning recommendation
-- ALTER TABLE audit_logs PARTITION BY RANGE (created_at);

COMMENT ON TABLE audit_logs IS 'Audit trail for security and compliance';

COMMENT ON COLUMN audit_logs.old_values IS 'JSON snapshot before action';

COMMENT ON COLUMN audit_logs.new_values IS 'JSON snapshot after action';

-- ----------------------------------------------------------------------------
-- System Configuration (Key-Value Store)
-- ----------------------------------------------------------------------------
CREATE TABLE system_config (
    config_key VARCHAR(255) PRIMARY KEY,
    config_value TEXT NOT NULL,
    description TEXT,
    is_encrypted BOOLEAN DEFAULT FALSE,
    updated_by UUID REFERENCES users (user_id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE system_config IS 'System-wide configuration settings';

-- ----------------------------------------------------------------------------
-- Notifications (Future Use)
-- ----------------------------------------------------------------------------
CREATE TABLE notifications (
    notification_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,

-- Notification Content
title VARCHAR(255) NOT NULL,
message TEXT NOT NULL,
notification_type VARCHAR(50) NOT NULL CHECK (
    notification_type IN (
        'info',
        'warning',
        'error',
        'success'
    )
),

-- State
is_read BOOLEAN DEFAULT FALSE, read_at TIMESTAMP,

-- Related Resources
related_resource_type VARCHAR(50), related_resource_id UUID,

-- Metadata
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);

-- Indexes for notifications
CREATE INDEX idx_notifications_user_id ON notifications (user_id, created_at DESC);

CREATE INDEX idx_notifications_unread ON notifications (user_id, is_read)
WHERE
    is_read = FALSE;

COMMENT ON TABLE notifications IS 'User notifications (desktop ready, errors, etc.)';

-- ============================================================================
-- VIEWS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- View: Active Pool Status
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW active_pool_status AS
SELECT
    p.pool_id,
    p.name,
    p.desktop_type,
    p.min_vms,
    p.max_vms,
    p.current_count,
    p.status,
    COUNT(
        CASE
            WHEN di.status = 'ready' THEN 1
        END
    ) AS available_vms,
    COUNT(
        CASE
            WHEN di.status = 'in_use' THEN 1
        END
    ) AS in_use_vms,
    COUNT(
        CASE
            WHEN di.status = 'error' THEN 1
        END
    ) AS error_vms,
    COUNT(
        CASE
            WHEN di.status = 'stopped' THEN 1
        END
    ) AS stopped_vms,
    CASE
        WHEN p.current_count > 0 THEN ROUND(
            (
                COUNT(
                    CASE
                        WHEN di.status = 'in_use' THEN 1
                    END
                )::NUMERIC / p.current_count
            ) * 100,
            2
        )
        ELSE 0
    END AS utilization_percent,
    COUNT(
        DISTINCT CASE
            WHEN ua.released_at IS NULL THEN ua.user_id
        END
    ) AS active_users
FROM
    desktop_pools p
    LEFT JOIN desktop_instances di ON p.pool_id = di.pool_id
    LEFT JOIN user_assignments ua ON di.instance_id = ua.instance_id
    AND ua.released_at IS NULL
WHERE
    p.deleted_at IS NULL
GROUP BY
    p.pool_id,
    p.name,
    p.desktop_type,
    p.min_vms,
    p.max_vms,
    p.current_count,
    p.status;

COMMENT ON VIEW active_pool_status IS 'Real-time pool status with utilization metrics';

-- ----------------------------------------------------------------------------
-- View: User Active Sessions
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW user_active_sessions AS
SELECT
    u.user_id,
    u.username,
    u.email,
    u.role,
    ua.assignment_id,
    ua.instance_id,
    di.floating_ip,
    di.hostname,
    di.connection_details,
    ua.assigned_at,
    EXTRACT(
        EPOCH
        FROM (NOW() - ua.assigned_at)
    ) AS session_duration_seconds,
    p.pool_id,
    p.name AS pool_name
FROM
    user_assignments ua
    JOIN users u ON ua.user_id = u.user_id
    JOIN desktop_instances di ON ua.instance_id = di.instance_id
    JOIN desktop_pools p ON ua.pool_id = p.pool_id
WHERE
    ua.released_at IS NULL
    AND di.status = 'in_use';

COMMENT ON VIEW user_active_sessions IS 'Currently active user desktop sessions';

-- ----------------------------------------------------------------------------
-- View: Pool Health Summary
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW pool_health_summary AS
SELECT
    p.pool_id,
    p.name,
    p.status AS pool_status,
    COUNT(di.instance_id) AS total_instances,
    COUNT(
        CASE
            WHEN vhc.is_healthy = FALSE THEN 1
        END
    ) AS unhealthy_instances,
    AVG(vhc.response_time_ms) AS avg_response_time_ms,
    MAX(vhc.checked_at) AS last_health_check
FROM
    desktop_pools p
    LEFT JOIN desktop_instances di ON p.pool_id = di.pool_id
    LEFT JOIN LATERAL (
        SELECT
            is_healthy,
            response_time_ms,
            checked_at
        FROM vm_health_checks
        WHERE
            instance_id = di.instance_id
        ORDER BY checked_at DESC
        LIMIT 1
    ) vhc ON TRUE
WHERE
    p.deleted_at IS NULL
GROUP BY
    p.pool_id,
    p.name,
    p.status;

COMMENT ON VIEW pool_health_summary IS 'Health status aggregated by pool';

-- ============================================================================
-- FUNCTIONS & TRIGGERS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Function: Update updated_at timestamp
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to tables with updated_at
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_pools_updated_at BEFORE UPDATE ON desktop_pools
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_instances_updated_at BEFORE UPDATE ON desktop_instances
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_jobs_updated_at BEFORE UPDATE ON provisioning_jobs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_policies_updated_at BEFORE UPDATE ON scaling_policies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_config_updated_at BEFORE UPDATE ON system_config
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ----------------------------------------------------------------------------
-- Function: Calculate session duration on release
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION calculate_session_duration()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.released_at IS NOT NULL AND OLD.released_at IS NULL THEN
        NEW.session_duration_seconds := EXTRACT(EPOCH FROM (NEW.released_at - NEW.assigned_at))::INTEGER;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER calculate_session_duration_trigger
BEFORE UPDATE ON user_assignments
FOR EACH ROW
EXECUTE FUNCTION calculate_session_duration();

-- ----------------------------------------------------------------------------
-- Function: Update pool current_count
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_pool_current_count()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE desktop_pools 
        SET current_count = current_count + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE pool_id = NEW.pool_id;
        
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE desktop_pools 
        SET current_count = GREATEST(current_count - 1, 0),
            updated_at = CURRENT_TIMESTAMP
        WHERE pool_id = OLD.pool_id;
    END IF;
    
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_pool_count_on_instance_change
AFTER INSERT OR DELETE ON desktop_instances
FOR EACH ROW
EXECUTE FUNCTION update_pool_current_count();

-- ----------------------------------------------------------------------------
-- Function: Auto-create scaling policy for new pool
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_default_scaling_policy()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO scaling_policies (
        pool_id,
        scale_up_threshold_percent,
        scale_down_threshold_percent,
        scale_up_increment,
        scale_down_increment,
        cooldown_period_seconds,
        min_available_vms,
        enabled
    ) VALUES (
        NEW.pool_id,
        20,   -- Scale up when available < 20%
        50,   -- Scale down when idle > 50%
        2,    -- Add 2 VMs at a time
        1,    -- Remove 1 VM at a time
        300,  -- 5 minutes cooldown
        NEW.min_vms,
        TRUE
    );
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auto_create_scaling_policy
AFTER INSERT ON desktop_pools
FOR EACH ROW
EXECUTE FUNCTION create_default_scaling_policy();

-- ----------------------------------------------------------------------------
-- Function: Log audit trail
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION log_pool_changes()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO audit_logs (
            user_id,
            action,
            resource_type,
            resource_id,
            old_values,
            success
        ) VALUES (
            OLD.created_by,
            'delete_pool',
            'pool',
            OLD.pool_id,
            row_to_json(OLD),
            TRUE
        );
        RETURN OLD;
        
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO audit_logs (
            user_id,
            action,
            resource_type,
            resource_id,
            old_values,
            new_values,
            success
        ) VALUES (
            NEW.created_by,
            'update_pool',
            'pool',
            NEW.pool_id,
            row_to_json(OLD),
            row_to_json(NEW),
            TRUE
        );
        RETURN NEW;
        
    ELSIF TG_OP = 'INSERT' THEN
        INSERT INTO audit_logs (
            user_id,
            action,
            resource_type,
            resource_id,
            new_values,
            success
        ) VALUES (
            NEW.created_by,
            'create_pool',
            'pool',
            NEW.pool_id,
            row_to_json(NEW),
            TRUE
        );
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_pool_changes
AFTER INSERT OR UPDATE OR DELETE ON desktop_pools
FOR EACH ROW
EXECUTE FUNCTION log_pool_changes();

-- ============================================================================
-- INITIAL DATA
-- ============================================================================

-- Default admin user (password: admin123)
INSERT INTO
    users (
        username,
        email,
        password_hash,
        full_name,
        role,
        is_active,
        email_verified
    )
VALUES (
        'admin',
        'admin@buet.ac.bd',
        '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5F7w7P3kXZX.i', -- bcrypt hash of 'admin123'
        'System Administrator',
        'admin',
        TRUE,
        TRUE
    );

-- Default system configuration
INSERT INTO
    system_config (
        config_key,
        config_value,
        description
    )
VALUES (
        'maintenance_mode',
        'false',
        'Enable/disable maintenance mode'
    ),
    (
        'max_concurrent_users',
        '50',
        'Maximum concurrent users allowed'
    ),
    (
        'default_session_timeout',
        '240',
        'Default session timeout in minutes'
    ),
    (
        'min_password_length',
        '8',
        'Minimum password length requirement'
    ),
    (
        'enable_auto_scaling',
        'true',
        'Global auto-scaling toggle'
    );

-- ============================================================================
-- INDEXES SUMMARY
-- ============================================================================

-- Total indexes created:
-- - users: 4 indexes
-- - user_sessions: 3 indexes
-- - desktop_pools: 5 indexes
-- - desktop_instances: 8 indexes
-- - provisioning_jobs: 6 indexes
-- - user_assignments: 6 indexes
-- - scaling_policies: 2 indexes
-- - pool_metrics: 2 indexes
-- - vm_health_checks: 3 indexes
-- - instance_snapshots: 3 indexes
-- - audit_logs: 5 indexes
-- - notifications: 2 indexes

-- ============================================================================
-- MAINTENANCE QUERIES
-- ============================================================================

-- Clean up old metrics (keep last 90 days)
-- Run this as a cron job
-- DELETE FROM pool_metrics WHERE recorded_at < NOW() - INTERVAL '90 days';

-- Clean up old health checks (keep last 30 days)
-- DELETE FROM vm_health_checks WHERE checked_at < NOW() - INTERVAL '30 days';

-- Clean up old audit logs (keep last 1 year)
-- DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '1 year';

-- Vacuum and analyze for performance
-- VACUUM ANALYZE;

-- ============================================================================
-- USEFUL QUERIES
-- ============================================================================

-- Get pools needing scaling
-- SELECT * FROM active_pool_status
-- WHERE (utilization_percent > 80 AND available_vms < min_vms)
--    OR (utilization_percent < 20 AND current_count > min_vms);

-- Get idle VMs for cleanup
-- SELECT * FROM desktop_instances
-- WHERE status = 'ready'
--   AND assigned_user_id IS NULL
--   AND last_accessed_at < NOW() - INTERVAL '30 minutes';

-- Get user session statistics
-- SELECT
--     u.username,
--     COUNT(*) as total_sessions,
--     AVG(session_duration_seconds) as avg_duration,
--     SUM(session_duration_seconds) as total_duration
-- FROM user_assignments ua
-- JOIN users u ON ua.user_id = u.user_id
-- WHERE ua.released_at IS NOT NULL
-- GROUP BY u.username
-- ORDER BY total_sessions DESC;

-- ============================================================================
-- SCHEMA VERSION
-- ============================================================================

INSERT INTO
    system_config (
        config_key,
        config_value,
        description
    )
VALUES (
        'schema_version',
        '1.0.0',
        'Database schema version'
    )
ON CONFLICT (config_key) DO
UPDATE
SET
    config_value = '1.0.0';

-- ============================================================================
-- END OF SCHEMA
-- ============================================================================