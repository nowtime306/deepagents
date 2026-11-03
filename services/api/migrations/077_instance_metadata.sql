CREATE TABLE service_instance_metadata (
 instance_id uuid PRIMARY KEY REFERENCES service_instances(id) ON DELETE CASCADE,
 node text NOT NULL, fault_domain text NOT NULL, release text NOT NULL,
 required_locks jsonb NOT NULL
);
