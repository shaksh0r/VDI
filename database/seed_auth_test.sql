CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO users (
    user_id,
    username,
    email,
    password_hash,
    full_name,
    student_id,
    department,
    role,
    is_active,
    email_verified
)
VALUES (
    '11111111-1111-1111-1111-111111111111',
    'student1',
    'student1@example.com',
    crypt('studentpass123', gen_salt('bf')),
    'Student One',
    '2024001',
    'CSE',
    'student',
    TRUE,
    TRUE
)
ON CONFLICT (user_id) DO UPDATE
SET username = EXCLUDED.username,
    email = EXCLUDED.email,
    password_hash = EXCLUDED.password_hash,
    full_name = EXCLUDED.full_name,
    student_id = EXCLUDED.student_id,
    department = EXCLUDED.department,
    role = EXCLUDED.role,
    is_active = EXCLUDED.is_active,
    email_verified = EXCLUDED.email_verified;

INSERT INTO desktop_pools (
    pool_id,
    name,
    description,
    base_image_id,
    flavor_id,
    network_id,
    security_group_id,
    min_vms,
    max_vms,
    current_count,
    desktop_type,
    auto_scaling_enabled,
    status,
    allowed_roles,
    max_session_duration_minutes,
    created_by
)
VALUES (
    '22222222-2222-2222-2222-222222222222',
    'test-student-pool',
    'Pool for auth-service login test',
    'dummy-image-id',
    'm1.small',
    'dummy-network-id',
    'dummy-security-group-id',
    1,
    5,
    1,
    'non_persistent',
    FALSE,
    'active',
    ARRAY['student']::user_role[],
    240,
    '11111111-1111-1111-1111-111111111111'
)
ON CONFLICT (pool_id) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    base_image_id = EXCLUDED.base_image_id,
    flavor_id = EXCLUDED.flavor_id,
    network_id = EXCLUDED.network_id,
    security_group_id = EXCLUDED.security_group_id,
    min_vms = EXCLUDED.min_vms,
    max_vms = EXCLUDED.max_vms,
    current_count = EXCLUDED.current_count,
    desktop_type = EXCLUDED.desktop_type,
    auto_scaling_enabled = EXCLUDED.auto_scaling_enabled,
    status = EXCLUDED.status,
    allowed_roles = EXCLUDED.allowed_roles,
    max_session_duration_minutes = EXCLUDED.max_session_duration_minutes,
    created_by = EXCLUDED.created_by;

INSERT INTO desktop_instances (
    instance_id,
    pool_id,
    openstack_vm_id,
    floating_ip,
    private_ip,
    hostname,
    status,
    vcpus,
    ram_mb,
    disk_gb,
    assigned_user_id,
    connection_details,
    provisioned_at
)
VALUES (
    '33333333-3333-3333-3333-333333333333',
    '22222222-2222-2222-2222-222222222222',
    'dummy-openstack-vm-id',
    '192.168.1.50',
    '10.0.0.15',
    'student1-vm',
    'ready',
    2,
    4096,
    20,
    NULL,
    '{"protocol":"rdp","host":"192.168.1.50","port":3389,"username":"student"}'::jsonb,
    CURRENT_TIMESTAMP
)
ON CONFLICT (instance_id) DO UPDATE
SET pool_id = EXCLUDED.pool_id,
    openstack_vm_id = EXCLUDED.openstack_vm_id,
    floating_ip = EXCLUDED.floating_ip,
    private_ip = EXCLUDED.private_ip,
    hostname = EXCLUDED.hostname,
    status = 'ready',
    vcpus = EXCLUDED.vcpus,
    ram_mb = EXCLUDED.ram_mb,
    disk_gb = EXCLUDED.disk_gb,
    assigned_user_id = NULL,
    connection_details = EXCLUDED.connection_details,
    provisioned_at = EXCLUDED.provisioned_at,
    assigned_at = NULL,
    last_accessed_at = NULL;

DELETE FROM user_sessions
WHERE user_id = '11111111-1111-1111-1111-111111111111'::uuid;
